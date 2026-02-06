"""
结构化事件日志（JSONL）。

用于记录运行时关键事件（心跳、状态、消息、工具调用与错误），便于回放与排障。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any


_lock = threading.Lock()
_state: dict[str, Any] = {"path": None, "run": None}


@dataclass(frozen=True)
class EventRecord:
    ts: str
    run: str
    level: str
    agent: str
    event_type: str
    payload: dict[str, Any]


def init_event_logger(*, base_dir: str, run_name: str) -> str:
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join([c for c in run_name if c.isalnum() or c in {"_", "-", "."}]).strip() or "run"
    path = os.path.join(logs_dir, f"events_{safe}_{ts}.jsonl")
    with _lock:
        _state["path"] = path
        _state["run"] = safe
    return path


def get_event_log_path() -> str | None:
    with _lock:
        return _state.get("path")


def log_event(
    *,
    event_type: str,
    agent: str = "",
    payload: dict[str, Any] | None = None,
    level: str = "INFO",
) -> None:
    with _lock:
        path = _state.get("path")
        run_name = _state.get("run") or ""
    if not path:
        return

    record = EventRecord(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        run=str(run_name),
        level=str(level),
        agent=str(agent),
        event_type=str(event_type),
        payload=payload or {},
    )

    line = json.dumps(record.__dict__, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _compact_msg_dict(d: dict[str, Any]) -> dict[str, Any]:
    content = d.get("content")
    if isinstance(content, str):
        d["content"] = content[:4000]
        return d

    if isinstance(content, list):
        log_thinking = _parse_bool(os.getenv("LOG_THINKING", "true"))
        clipped: list[dict[str, Any]] = []
        for block in content[:20]:
            if not isinstance(block, dict):
                continue
            b = dict(block)
            if not log_thinking and b.get("type") == "thinking":
                continue
            if "text" in b and isinstance(b["text"], str):
                b["text"] = b["text"][:2000]
            if "thinking" in b and isinstance(b["thinking"], str):
                if log_thinking:
                    b["thinking"] = b["thinking"][:2000]
                else:
                    b.pop("thinking", None)
            if "raw_input" in b and isinstance(b["raw_input"], str):
                b["raw_input"] = b["raw_input"][:2000]
            clipped.append(b)
        d["content"] = clipped
    return d


def log_msg(*, event_type: str, agent: str, msg: Any, level: str = "INFO") -> None:
    try:
        d = msg.to_dict() if hasattr(msg, "to_dict") else {"msg": str(msg)}
    except Exception:
        d = {"msg": str(msg)}
    d = _compact_msg_dict(d)
    log_event(event_type=event_type, agent=agent, payload={"msg": d}, level=level)


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}
