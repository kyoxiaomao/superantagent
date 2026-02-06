"""
消息内容解析工具。

- `msg_to_text()`：将 Msg 内容（含 blocks）规整为纯文本。
- `extract_first_json_obj()`：从文本或 fenced code 中提取第一个 JSON 对象，供蚁王调度解析。
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentscope.message import Msg, TextBlock


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_TOPIC_TAG_RE = re.compile(r"(?:^|\s)(#[0-9A-Za-z_\-\u4e00-\u9fff]{1,20})(?:\s|$)")


def msg_to_text(msg: Msg | list[Msg] | None) -> str:
    if msg is None:
        return ""
    if isinstance(msg, list):
        return "\n".join([msg_to_text(m) for m in msg if m is not None])

    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            elif isinstance(block, TextBlock):
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


def extract_first_json_obj(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    fenced = _JSON_FENCE_RE.findall(text)
    candidates = [f.strip() for f in fenced if f.strip()] + [text]

    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned:
            continue

        start = cleaned.find("{")
        if start < 0:
            continue

        brace = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue

            if ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
                if brace == 0:
                    snippet = cleaned[start : i + 1]
                    try:
                        return json.loads(snippet)
                    except Exception:
                        break
        continue

    return None


def extract_first_topic_tag(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    m = _TOPIC_TAG_RE.search(s)
    return m.group(1) if m else None


def is_valid_topic_tag(tag: str) -> bool:
    t = (tag or "").strip()
    if not t.startswith("#"):
        return False
    if len(t) < 2:
        return False
    if len(t) > 21:
        return False
    return extract_first_topic_tag(t) == t
