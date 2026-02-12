"""
JSONL 归档与回退检索。

用于在 ReMe/向量库之外提供可追溯的本地落盘，以及在向量库检索为空时的关键词回退检索。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class JsonlItem:
    timestamp: str
    agent_name: str
    memory_type: str
    content: str
    keywords: list[str]
    metadata: dict[str, Any]


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def make_file_path(storage_dir: str, *, agent_name: str, memory_type: str) -> str:
    safe_mkdir(storage_dir)
    safe_type = memory_type.strip().lower() or "unknown"
    return os.path.join(storage_dir, f"{agent_name}.{safe_type}.jsonl")


def append_item(file_path: str, item: JsonlItem) -> None:
    safe_mkdir(os.path.dirname(file_path))
    obj = {
        "timestamp": item.timestamp,
        "agent_name": item.agent_name,
        "memory_type": item.memory_type,
        "content": item.content,
        "keywords": item.keywords,
        "metadata": item.metadata,
    }
    with open(file_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_items(file_path: str) -> list[JsonlItem]:
    if not os.path.exists(file_path):
        return []
    items: list[JsonlItem] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                items.append(
                    JsonlItem(
                        timestamp=str(obj.get("timestamp") or ""),
                        agent_name=str(obj.get("agent_name") or ""),
                        memory_type=str(obj.get("memory_type") or ""),
                        content=str(obj.get("content") or ""),
                        keywords=list(obj.get("keywords") or []),
                        metadata=dict(obj.get("metadata") or {}),
                    ),
                )
            except Exception:
                continue
    return items


def extract_keywords(text: str) -> list[str]:
    raw: list[str] = []
    for token in split_tokens(text):
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


def split_tokens(text: str) -> list[str]:
    buf: list[str] = []
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


def rank_items(items: list[JsonlItem], *, keywords: list[str]) -> list[JsonlItem]:
    keys = [k for k in keywords if k]
    if not keys:
        return list(reversed(items))[:]

    def score(it: JsonlItem) -> int:
        s = 0
        hay = it.content
        for k in keys:
            if k in hay:
                s += 3
            if k in it.keywords:
                s += 2
        return s

    return sorted(items, key=lambda it: (score(it), it.timestamp), reverse=True)

