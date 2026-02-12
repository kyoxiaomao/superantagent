"""
基于 JSONL 文件的长期记忆。

为每个智能体提供简单的 record/retrieve 能力，并额外提供面向工具调用的记忆写入与检索接口。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentscope.memory import LongTermMemoryBase
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse


@dataclass(frozen=True)
class _MemoryItem:
    timestamp: str
    content: str
    keywords: list[str]


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _msg_to_plain_text(msg: Msg | None) -> str:
    if msg is None:
        return ""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, TextBlock):
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(c)


class FileLongTermMemory(LongTermMemoryBase):
    def __init__(self, *, agent_name: str, storage_dir: str) -> None:
        super().__init__()
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        _safe_mkdir(self.storage_dir)
        self.file_path = os.path.join(self.storage_dir, f"{self.agent_name}.jsonl")

    async def record(self, msgs: list[Msg | None], **kwargs: Any) -> None:
        texts = [t.strip() for t in (_msg_to_plain_text(m) for m in msgs) if t and t.strip()]
        if not texts:
            return
        content = "\n".join(texts)
        item = _MemoryItem(timestamp=_now(), content=content, keywords=_extract_keywords(content))
        _append_item(self.file_path, item)

    async def retrieve(self, msg: Msg | list[Msg] | None, limit: int = 5, **kwargs: Any) -> str:
        query = ""
        if isinstance(msg, list):
            query = "\n".join([_msg_to_plain_text(m) for m in msg if m is not None])
        elif isinstance(msg, Msg):
            query = _msg_to_plain_text(msg)
        query = query.strip()
        if not query:
            return ""

        keywords = _extract_keywords(query)
        items = _load_items(self.file_path)
        ranked = _rank_items(items, keywords)
        top = ranked[: max(0, int(limit))]
        if not top:
            return ""
        lines = []
        for it in top:
            lines.append(f"[{it.timestamp}] {it.content}")
        return "\n".join(lines)

    async def record_to_memory(self, thinking: str, content: list[str], **kwargs: Any) -> ToolResponse:
        texts = [c.strip() for c in content if c and c.strip()]
        merged = "\n".join(texts).strip()
        if not merged:
            return ToolResponse(content=[TextBlock(type="text", text="未记录：内容为空。")])

        item = _MemoryItem(timestamp=_now(), content=merged, keywords=_extract_keywords(merged))
        _append_item(self.file_path, item)
        return ToolResponse(content=[TextBlock(type="text", text="已记录到长期记忆。")], metadata={"count": 1})

    async def retrieve_from_memory(self, keywords: list[str], limit: int = 5, **kwargs: Any) -> ToolResponse:
        keys = [k.strip() for k in keywords if k and k.strip()]
        if not keys:
            return ToolResponse(content=[TextBlock(type="text", text="未检索：关键词为空。")])

        items = _load_items(self.file_path)
        ranked = _rank_items(items, keys)
        top = ranked[: max(0, int(limit))]
        if not top:
            return ToolResponse(content=[TextBlock(type="text", text="未找到匹配的长期记忆。")], metadata={"items": []})

        text = "\n".join([f"[{it.timestamp}] {it.content}" for it in top])
        return ToolResponse(content=[TextBlock(type="text", text=text)], metadata={"items": [it.__dict__ for it in top]})


def _append_item(file_path: str, item: _MemoryItem) -> None:
    _safe_mkdir(os.path.dirname(file_path))
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item.__dict__, ensure_ascii=False) + "\n")


def _load_items(file_path: str) -> list[_MemoryItem]:
    if not os.path.exists(file_path):
        return []
    items: list[_MemoryItem] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                items.append(
                    _MemoryItem(
                        timestamp=str(obj.get("timestamp") or ""),
                        content=str(obj.get("content") or ""),
                        keywords=list(obj.get("keywords") or []),
                    ),
                )
            except Exception:
                continue
    return items


def _extract_keywords(text: str) -> list[str]:
    raw = []
    for token in _split_tokens(text):
        if len(token) < 2:
            continue
        raw.append(token)
    uniq: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
        if len(uniq) >= 12:
            break
    return uniq


def _split_tokens(text: str) -> list[str]:
    buf = []
    token = ""
    for ch in text:
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            token += ch
        else:
            if token:
                buf.append(token)
                token = ""
    if token:
        buf.append(token)
    return buf


def _rank_items(items: list[_MemoryItem], keywords: list[str]) -> list[_MemoryItem]:
    keys = [k for k in keywords if k]
    if not keys:
        return list(reversed(items))[:]

    def score(it: _MemoryItem) -> int:
        s = 0
        hay = it.content
        for k in keys:
            if k in hay:
                s += 3
            if k in it.keywords:
                s += 2
        return s

    return sorted(items, key=lambda it: (score(it), it.timestamp), reverse=True)

