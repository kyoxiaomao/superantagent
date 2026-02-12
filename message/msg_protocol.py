"""
消息协议薄封装。

提供统一的 Msg 构造入口，便于在编排与运行时中保持消息格式一致。
"""

from __future__ import annotations

from typing import Any

from agentscope.message import Msg


def make_msg(
    *,
    role: str,
    name: str,
    content: Any,
    metadata: dict[str, Any] | None = None,
) -> Msg:
    return Msg(
        role=role,
        name=name,
        content=content,
        metadata=metadata,
    )
