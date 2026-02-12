"""
GLM 模型适配器。

把智谱 HTTP API 的 chat/completions 封装为 AgentScope `ChatModelBase`，支持流式输出与工具调用块映射。
"""

import json
import time
from collections import OrderedDict
from typing import Any, AsyncGenerator, Literal
from uuid import uuid4

import aiohttp

from agentscope.message import TextBlock, ThinkingBlock, ToolUseBlock
from agentscope.model import ChatResponse
from agentscope.model._model_base import ChatModelBase
from agentscope.model._model_usage import ChatUsage


class GLMChatModel(ChatModelBase):
    def __init__(
        self,
        model_name: str = "glm-4.5-air",
        api_key: str | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        stream: bool = True,
        timeout_s: float = 120.0,
        include_thinking: bool = False,
        generate_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, stream=stream)
        # 基础连接与请求参数
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.include_thinking = bool(include_thinking)
        self.generate_kwargs = generate_kwargs or {}

    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "required"] | str | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        # 消息格式严格要求 role/content，以便与 GLM 接口一致
        if not isinstance(messages, list):
            raise ValueError(
                f"GLM `messages` 需要 list，当前为 {type(messages)}",
            )
        if not all(isinstance(m, dict) and "role" in m and "content" in m for m in messages):
            raise ValueError("GLM `messages` 每条消息必须包含 role 与 content")
        if not self.api_key:
            raise ValueError("未配置 GLM_API_KEY（请在 .env 中设置）")

        # 统一构造 payload，stream 控制走流式或非流式
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream,
            **self.generate_kwargs,
            **kwargs,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        if self.stream:
            return self._stream_chat(url, headers, payload)
        return await self._non_stream_chat(url, headers, payload)

    async def _non_stream_chat(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> ChatResponse:
        # 非流式一次性返回
        start = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"GLM 调用失败: HTTP {resp.status}: {text}")
                data = json.loads(text)
        return self._parse_non_stream_response(data, time.perf_counter() - start)

    async def _stream_chat(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> AsyncGenerator[ChatResponse, None]:
        # 流式增量解析，支持思考内容与工具调用
        start = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)

        usage: ChatUsage | None = None
        text = ""
        thinking = ""
        tool_calls: OrderedDict[int, dict[str, Any]] = OrderedDict()

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status >= 400:
                    err = await resp.text()
                    raise RuntimeError(f"GLM 流式调用失败: HTTP {resp.status}: {err}")

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue

                    data_part = line[5:].strip()
                    if data_part == "[DONE]":
                        break

                    chunk = json.loads(data_part)
                    if isinstance(chunk, dict) and "usage" in chunk and chunk["usage"]:
                        u = chunk["usage"]
                        usage = ChatUsage(
                            input_tokens=int(u.get("prompt_tokens") or 0),
                            output_tokens=int(u.get("completion_tokens") or 0),
                            time=time.perf_counter() - start,
                            metadata=u,
                        )

                    if not chunk.get("choices"):
                        continue

                    choice = chunk["choices"][0]
                    delta = (choice or {}).get("delta") or {}

                    thinking += str(delta.get("reasoning_content") or "")
                    text += str(delta.get("content") or "")

                    # 工具调用在流式中以增量 arguments 形式返回，需要拼接
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index") or 0)
                        fn = (tc.get("function") or {})
                        if idx in tool_calls:
                            tool_calls[idx]["arguments"] += str(fn.get("arguments") or "")
                        else:
                            tool_calls[idx] = {
                                "id": tc.get("id") or str(uuid4()),
                                "name": fn.get("name") or "",
                                "arguments": str(fn.get("arguments") or ""),
                            }

                    contents: list[TextBlock | ToolUseBlock | ThinkingBlock] = []
                    if self.include_thinking and thinking:
                        contents.append(ThinkingBlock(type="thinking", thinking=thinking))
                    if text:
                        contents.append(TextBlock(type="text", text=text))

                    for tc in tool_calls.values():
                        raw_input = tc["arguments"]
                        try:
                            input_obj = json.loads(raw_input or "{}")
                        except Exception:
                            input_obj = {}

                        contents.append(
                            ToolUseBlock(
                                type="tool_use",
                                id=tc["id"] or str(uuid4()),
                                name=tc["name"] or "",
                                input=input_obj,
                                raw_input=raw_input,
                            ),
                        )

                    if contents:
                        yield ChatResponse(content=contents, usage=usage, metadata=None)

    def _parse_non_stream_response(self, data: dict[str, Any], elapsed_s: float) -> ChatResponse:
        # 非流式响应解析为 AgentScope 统一结构
        contents: list[TextBlock | ToolUseBlock | ThinkingBlock] = []

        choices = data.get("choices") or []
        if choices:
            message = (choices[0] or {}).get("message") or {}

            if self.include_thinking and message.get("reasoning_content"):
                contents.append(ThinkingBlock(type="thinking", thinking=str(message.get("reasoning_content"))))
            if message.get("content"):
                contents.append(TextBlock(type="text", text=str(message.get("content"))))

            for tc in message.get("tool_calls") or []:
                fn = (tc.get("function") or {})
                raw_input = str(fn.get("arguments") or "")
                try:
                    input_obj = json.loads(raw_input or "{}")
                except Exception:
                    input_obj = {}

                contents.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=tc.get("id") or str(uuid4()),
                        name=str(fn.get("name") or ""),
                        input=input_obj,
                        raw_input=raw_input,
                    ),
                )

        usage_obj = None
        usage = data.get("usage") or {}
        if usage:
            usage_obj = ChatUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                time=elapsed_s,
                metadata=usage,
            )

        return ChatResponse(
            content=contents or [TextBlock(type="text", text="")],
            usage=usage_obj,
            metadata={
                "request_id": data.get("request_id"),
                "model": data.get("model"),
            },
        )
