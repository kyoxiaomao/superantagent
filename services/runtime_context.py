from __future__ import annotations

from typing import Any

_CURRENT_RUNTIME: Any | None = None


def set_current_runtime(runtime: Any | None) -> None:
    global _CURRENT_RUNTIME
    _CURRENT_RUNTIME = runtime


def get_current_runtime() -> Any | None:
    return _CURRENT_RUNTIME

