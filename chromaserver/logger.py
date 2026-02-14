from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

_lock = threading.Lock()


def _logs_dir(base_dir: str) -> str:
    path = os.path.join(base_dir, "chromaserver", "_logs")
    os.makedirs(path, exist_ok=True)
    return path


def _day() -> str:
    return datetime.now().strftime("%Y%m%d")


def third_party_log_path(*, base_dir: str) -> str:
    return os.path.join(_logs_dir(base_dir), f"chromaserver_{_day()}.log")


def append_chromaserver_event(*, base_dir: str, event_type: str, payload: dict[str, Any] | None = None, level: str = "INFO") -> str:
    path = os.path.join(_logs_dir(base_dir), f"chromaserver_events_{_day()}.jsonl")
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": str(level or "INFO").upper(),
        "event_type": str(event_type or "").strip(),
        "pid": os.getpid(),
        "payload": payload or {},
    }
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return path

