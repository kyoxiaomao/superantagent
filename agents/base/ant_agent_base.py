"""
智能体基础工厂。

统一从配置加载角色参数与 system prompt，创建 AgentScope `ReActAgent`，
并为所有角色挂载记忆（短期/长期）与结构化事件日志 hooks。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import yaml

from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit

from message import make_msg, msg_to_text
from memory import build_memory_bundle
from services import ModelBundle
from services.event_logger import log_event, log_msg
from services.skill_loader import load_utils


def _load_yaml(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("配置文件内容格式不正确，应为字典结构")
    return data


@dataclass(frozen=True)
class AntAgentConfig:
    name: str
    sys_prompt: str
    max_iters: int


def load_agent_config(role_key: str) -> AntAgentConfig:
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    agent_cfg = _load_yaml(os.path.join(root, "configs", "agent_configs.yaml"))
    prompts_cfg = _load_yaml(os.path.join(root, "configs", "prompts", "system_prompts.yaml"))

    role_cfg = agent_cfg.get(role_key)
    if not isinstance(role_cfg, dict):
        raise ValueError(f"未找到角色 {role_key} 的配置。")
    name_raw = role_cfg.get("name")
    if not str(name_raw or "").strip():
        raise ValueError(f"角色 {role_key} 缺少 name 配置。")
    if "max_iters" not in role_cfg:
        raise ValueError(f"角色 {role_key} 缺少 max_iters 配置。")
    name = str(name_raw).strip()
    max_iters = int(role_cfg.get("max_iters"))
    sys_prompt_raw = prompts_cfg.get(role_key)
    sys_prompt = str(sys_prompt_raw or "")

    if not sys_prompt:
        raise ValueError(f"未找到角色 {role_key} 的 system prompt 配置。")

    return AntAgentConfig(name=name, sys_prompt=sys_prompt, max_iters=max_iters)


def create_react_ant_agent(
    *,
    role_key: str,
    model_bundle: ModelBundle,
    toolkit: Toolkit | None = None,
) -> ReActAgent:
    cfg = load_agent_config(role_key)
    mem = build_memory_bundle(role_key=role_key, agent_name=cfg.name)
    tk = toolkit or Toolkit()
    load_utils(tk, role_key=str(role_key))

    memory_rules = (
        "\n\n# 长期记忆使用规则\n"
        "你具有长期记忆能力，长期记忆分为三类：personal（个人）、task（任务）、tool（工具）。\n"
        f"你当前角色的默认记忆类型为：{mem.default_memory_type}。\n"
        "你可以通过工具函数调用长期记忆：\n"
        "- 检索：retrieve_from_memory(keywords, memory_type=...)\n"
        "约束：\n"
        "- 调用时必须显式传入 memory_type（personal/task/tool）；如不确定，用默认类型。\n"
        "- 回答涉及“过去信息/偏好/经验/工具用法”的问题前，必须先检索再回答。\n"
        "- 长期记忆写入由系统在回复后自动沉淀，不通过工具函数写入。\n"
    )

    class _ReadOnlyLongTermMemory:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def ensure_ready(self) -> None:
            fn = getattr(self._inner, "ensure_ready", None)
            if callable(fn):
                await fn()

        async def __aenter__(self) -> Any:
            fn = getattr(self._inner, "__aenter__", None)
            if callable(fn):
                return await fn()
            return self

        async def __aexit__(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
            fn = getattr(self._inner, "__aexit__", None)
            if callable(fn):
                await fn(exc_type, exc_val, exc_tb)

        async def record(self, *args: Any, **kwargs: Any) -> None:
            return

        async def record_to_memory(self, *args: Any, **kwargs: Any) -> Any:
            raise AttributeError("record_to_memory is disabled for ReAct agents")

        async def retrieve(self, *args: Any, **kwargs: Any) -> Any:
            fn = getattr(self._inner, "retrieve", None)
            if callable(fn):
                return await fn(*args, **kwargs)
            return ""

        async def retrieve_from_memory(self, *args: Any, **kwargs: Any) -> Any:
            fn = getattr(self._inner, "retrieve_from_memory", None)
            if callable(fn):
                return await fn(*args, **kwargs)
            raise AttributeError("retrieve_from_memory not available")

    react_long_term = _ReadOnlyLongTermMemory(mem.long_term)

    agent = ReActAgent(
        name=cfg.name,
        sys_prompt=f"{cfg.sys_prompt}{memory_rules}",
        model=model_bundle.model,
        formatter=model_bundle.formatter,
        toolkit=tk,
        memory=mem.short_term,
        long_term_memory=react_long_term,
        long_term_memory_mode="both",
        compression_config=mem.compression_config,
        max_iters=cfg.max_iters,
    )
    setattr(agent, "_ant_long_term_memory", mem.long_term)
    setattr(agent, "_ant_default_memory_type", mem.default_memory_type)
    setattr(agent, "_ant_last_user_text", "")
    setattr(agent, "_ant_pending_tool", None)

    def _打印记忆卡点(*, 阶段: str, 详情: str) -> None:
        msg = f"【记忆卡点】{阶段} | {详情}"
        print(msg, flush=True)

    async def _record_memory_safely(*, memory_type: str, thinking: str, content: list[str], score: float | None = None) -> None:
        lt = getattr(agent, "_ant_long_term_memory", None)
        if lt is None:
            return
        try:
            _打印记忆卡点(阶段="异步写入开始", 详情=f"类型={memory_type} 字数={sum(len(c) for c in content)}")
            await lt.record_to_memory(thinking=thinking, content=content, memory_type=memory_type, score=score)
            _打印记忆卡点(阶段="异步写入完成", 详情=f"类型={memory_type}")
        except Exception as e:
            log_event(event_type="long_term_memory_record_error", agent=str(getattr(agent, "name", "")), payload={"error": str(e), "memory_type": memory_type})
            _打印记忆卡点(阶段="异步写入失败", 详情=f"类型={memory_type} 错误={e}")

    def _schedule_record(*, memory_type: str, thinking: str, content: list[str], score: float | None = None) -> None:
        if not content:
            return
        _打印记忆卡点(阶段="进入写入队列", 详情=f"类型={memory_type} 条数={len(content)}")
        loop = asyncio.get_running_loop()
        loop.create_task(_record_memory_safely(memory_type=memory_type, thinking=thinking, content=content, score=score))

    def _looks_like_preference(text: str) -> bool:
        s = (text or "").strip()
        if not s:
            return False
        for k in ["我喜欢", "我不喜欢", "我希望", "我偏好", "以后", "请你以后", "长期", "默认"]:
            if k in s:
                return True
        return False

    def _extract_tool_blocks(msg: Any) -> list[dict[str, Any]]:
        if msg is None:
            return []
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return []
        blocks: list[dict[str, Any]] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = str(b.get("type") or "").strip()
            if t in {"tool_use", "tool_result"}:
                blocks.append(b)
                continue
            if "tool_name" in b or "raw_input" in b:
                blocks.append(b)
        return blocks

    def _pre_observe_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            log_msg(event_type="observe", agent=str(getattr(self, "name", "")), msg=msg)
        return kwargs

    def _pre_reply_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            log_msg(event_type="pre_reply", agent=str(getattr(self, "name", "")), msg=msg)
            setattr(self, "_ant_last_user_text", msg_to_text(msg))
            _打印记忆卡点(阶段="收到用户消息", 详情=str(getattr(self, "_ant_last_user_text", "") or "")[:120])
        if hasattr(self, "print") and getattr(self, "_ui_busy_flag", False) is not True:
            setattr(self, "_ui_busy_flag", True)
            ui_msg = make_msg(
                role="system",
                name="ui",
                content="",
                metadata={"ui_event": "agent_status", "agent_name": str(getattr(self, "name", "") or ""), "status": "busy"},
            )
            loop = asyncio.get_running_loop()
            loop.create_task(self.print(ui_msg))
        return kwargs

    def _post_reply_hook(self: Any, kwargs: dict[str, Any], output: Any) -> Any:
        if output is not None:
            log_msg(event_type="post_reply", agent=str(getattr(self, "name", "")), msg=output)
            _打印记忆卡点(阶段="生成回复完成", 详情=msg_to_text(output)[:120])
        pending = getattr(self, "_ant_pending_tool", None)
        if isinstance(pending, dict) and pending.get("tool_name") and pending.get("output"):
            _打印记忆卡点(阶段="工具记忆写入", 详情=str(pending.get("tool_name") or ""))
            payload = {
                "create_time": getattr(output, "timestamp", "") or "",
                "tool_name": str(pending.get("tool_name") or ""),
                "input": pending.get("input") or {},
                "output": str(pending.get("output") or ""),
                "success": pending.get("success", True),
            }
            _schedule_record(
                memory_type="tool",
                thinking="总结本轮工具使用经验（成功/失败模式、关键参数、可复用提示）。",
                content=[json.dumps(payload, ensure_ascii=False)],
            )
            setattr(self, "_ant_pending_tool", None)

        user_text = str(getattr(self, "_ant_last_user_text", "") or "")
        reply_text = msg_to_text(output) if output is not None else ""
        if _looks_like_preference(user_text):
            _打印记忆卡点(阶段="偏好识别", 详情=user_text.strip()[:120])
            _schedule_record(
                memory_type="personal",
                thinking="用户表达了偏好/长期设定/对话风格要求，需要沉淀到个人长期记忆。",
                content=[user_text.strip()[:800]],
            )
        if reply_text.strip():
            mt = str(getattr(self, "_ant_default_memory_type", "task") or "task")
            thinking = "沉淀本轮可复用经验要点。"
            _打印记忆卡点(阶段="回复记忆写入", 详情=f"类型={mt}")
            merged = reply_text.strip()
            if user_text.strip():
                merged = f"用户：{user_text.strip()}\n助手：{merged}"
            _schedule_record(
                memory_type=mt,
                thinking=thinking,
                content=[merged[:1200]],
            )
        if hasattr(self, "print") and getattr(self, "_ui_busy_flag", False) is True:
            setattr(self, "_ui_busy_flag", False)
            ui_msg = make_msg(
                role="system",
                name="ui",
                content="",
                metadata={"ui_event": "agent_status", "agent_name": str(getattr(self, "name", "") or ""), "status": "idle"},
            )
            loop = asyncio.get_running_loop()
            loop.create_task(self.print(ui_msg))
        return output

    def _pre_print_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            name = str(getattr(msg, "name", "") or "")
            role = str(getattr(msg, "role", "") or "")
            content = getattr(msg, "content", None)
            md = getattr(msg, "metadata", None) or {}
            ui_event = md.get("ui_event") if isinstance(md, dict) else None
            if name == "ui" and role == "system" and ui_event and content == "":
                return kwargs
            log_msg(event_type="print", agent=str(getattr(self, "name", "")), msg=msg)
            for b in _extract_tool_blocks(msg):
                bt = str(b.get("type") or "").strip()
                tool_name = str(b.get("name") or b.get("tool_name") or "")
                if bt == "tool_use" and tool_name:
                    setattr(self, "_ant_pending_tool", {"tool_name": tool_name, "input": b.get("input") or b.get("raw_input") or {}, "output": "", "success": True})
                if bt == "tool_result":
                    pending = getattr(self, "_ant_pending_tool", None)
                    if isinstance(pending, dict):
                        out = b.get("text") or b.get("content") or ""
                        if isinstance(out, list):
                            out = "\n".join([str(x) for x in out if x is not None])
                        out_s = str(out or "")
                        pending["output"] = (pending.get("output") or "") + out_s
                        if "error" in out_s.lower() or "失败" in out_s:
                            pending["success"] = False
        return kwargs

    agent.register_instance_hook("pre_observe", "antagent_log_pre_observe", _pre_observe_hook)
    agent.register_instance_hook("pre_reply", "antagent_log_pre_reply", _pre_reply_hook)
    agent.register_instance_hook("post_reply", "antagent_log_post_reply", _post_reply_hook)
    agent.register_instance_hook("pre_print", "antagent_log_pre_print", _pre_print_hook)

    log_event(event_type="agent_created", agent=cfg.name, payload={"role_key": role_key})
    return agent
