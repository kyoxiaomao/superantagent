"""LiteLLM asynchronous implementation for ReMe."""

import os
from typing import List, AsyncGenerator, Optional

import litellm
from loguru import logger

from .base_llm import BaseLLM
from ..context import C
from ..enumeration import ChunkEnum
from ..schema import Message
from ..schema import StreamChunk
from ..schema import ToolCall


@C.register_llm("litellm")
class LiteLLM(BaseLLM):
    """Async LLM implementation using LiteLLM to support multiple providers."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        custom_llm_provider: str = "openai",
        **kwargs,
    ):
        """Initialize the LiteLLM client with API configuration and provider settings."""
        super().__init__(**kwargs)
        self.api_key: Optional[str] = api_key or os.getenv("REME_LLM_API_KEY")
        self.base_url: Optional[str] = base_url or os.getenv("REME_LLM_BASE_URL")
        self.custom_llm_provider: str = custom_llm_provider

    def _build_stream_kwargs(
        self,
        messages: List[Message],
        tools: Optional[List[ToolCall]] = None,
        log_params: bool = True,
        **kwargs,
    ) -> dict:
        """Construct and log the parameters dictionary for LiteLLM API calls."""
        # Construct the API parameters by merging multiple sources
        llm_kwargs = {
            "model": self.model_name,
            "messages": [x.simple_dump() for x in messages],
            "tools": [x.simple_input_dump() for x in tools] if tools else None,
            "stream": True,
            "custom_llm_provider": self.custom_llm_provider,
            **self.kwargs,
            **kwargs,
        }

        # Add API key and base URL if provided
        if self.api_key:
            llm_kwargs["api_key"] = self.api_key
        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Log parameters for debugging, with message/tool counts instead of full content
        if log_params:
            log_kwargs: dict = {}
            for k, v in llm_kwargs.items():
                if k in ["messages", "tools"]:
                    log_kwargs[k] = len(v) if v is not None else 0
                elif k == "api_key":
                    # Mask API key in logs for security
                    log_kwargs[k] = "***" if v else None
                else:
                    log_kwargs[k] = v
            logger.info(f"llm_kwargs={log_kwargs}")

        return llm_kwargs

    async def _stream_chat(
        self,
        messages: List[Message],
        tools: Optional[List[ToolCall]] = None,
        stream_kwargs: Optional[dict] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Execute async streaming chat requests and yield processed response chunks."""
        # Create streaming completion request using LiteLLM asynchronously
        stream_kwargs = stream_kwargs or {}
        completion = await litellm.acompletion(**stream_kwargs)

        # Track accumulated tool calls across chunks
        ret_tools: List[ToolCall] = []
        # Flag to track if we've started receiving answer content
        is_answering: bool = False

        async for chunk in completion:
            # Handle usage information (typically the last chunk)
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    yield StreamChunk(chunk_type=ChunkEnum.USAGE, chunk=chunk.usage.model_dump())

            else:
                delta = chunk.choices[0].delta

                # Check for reasoning content (models that support thinking)
                if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
                    yield StreamChunk(chunk_type=ChunkEnum.THINK, chunk=delta.reasoning_content)

                else:
                    if not is_answering:
                        is_answering = True

                    # Yield regular text content
                    if delta.content is not None:
                        yield StreamChunk(chunk_type=ChunkEnum.ANSWER, chunk=delta.content)

                    # Process tool calls - LiteLLM streams them incrementally
                    if hasattr(delta, "tool_calls") and delta.tool_calls is not None:
                        for tool_call in delta.tool_calls:
                            self._accumulate_tool_call_chunk(tool_call, ret_tools)

        # After streaming completes, validate and yield complete tool calls
        for tool_data in self._validate_and_serialize_tools(ret_tools, tools):
            yield StreamChunk(chunk_type=ChunkEnum.TOOL, chunk=tool_data)
