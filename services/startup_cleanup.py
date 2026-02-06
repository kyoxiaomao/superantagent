"""
启动清理（便于测试）。

当环境变量 `ANT_RESET_ON_START=true` 时，启动阶段会清除上一轮运行动态产生的数据：
- memory/storage/*.jsonl
- agents/skills/ 下除 __init__.py 外的 *.py 与 *.md
- docs/generated/ 下所有文件
- logs/ 下的 *.log 与 events_*.jsonl
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CleanupReport:
    enabled: bool
    removed_files: int
    removed_dirs: int
    errors: int


def maybe_cleanup_on_start(*, base_dir: str) -> CleanupReport:
    enabled = _parse_bool(os.getenv("ANT_RESET_ON_START", "false"))
    if not enabled:
        return CleanupReport(enabled=False, removed_files=0, removed_dirs=0, errors=0)
    return cleanup_runtime_artifacts(base_dir=base_dir)


def cleanup_runtime_artifacts(*, base_dir: str) -> CleanupReport:
    base = os.path.abspath(base_dir)
    removed_files = 0
    removed_dirs = 0
    errors = 0

    targets = list(_iter_target_paths(base))
    for p in targets:
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
                removed_files += 1
                continue
            if os.path.isdir(p):
                _remove_dir_tree(p)
                removed_dirs += 1
        except Exception:
            errors += 1

    return CleanupReport(enabled=True, removed_files=removed_files, removed_dirs=removed_dirs, errors=errors)


def _iter_target_paths(base: str) -> Iterable[str]:
    yield from _glob_files(os.path.join(base, "memory", "storage"), suffixes=(".jsonl",))

    skills_dir = os.path.join(base, "agents", "skills")
    for p in _glob_files(skills_dir, suffixes=(".py", ".md")):
        if os.path.basename(p) == "__init__.py":
            continue
        yield p

    docs_generated = os.path.join(base, "docs", "generated")
    if os.path.isdir(docs_generated):
        yield docs_generated

    logs_dir = os.path.join(base, "logs")
    yield from _glob_files(logs_dir, prefix="events_", suffixes=(".jsonl",))
    yield from _glob_files(logs_dir, suffixes=(".log",))


def _glob_files(dir_path: str, *, suffixes: tuple[str, ...], prefix: str = "") -> list[str]:
    if not os.path.isdir(dir_path):
        return []
    buf: list[str] = []
    for name in os.listdir(dir_path):
        if prefix and not name.startswith(prefix):
            continue
        if not any(name.endswith(s) for s in suffixes):
            continue
        buf.append(os.path.join(dir_path, name))
    return buf


def _remove_dir_tree(path: str) -> None:
    for root, dirs, files in os.walk(path, topdown=False):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                os.remove(p)
            except Exception:
                pass
        for dn in dirs:
            d = os.path.join(root, dn)
            try:
                os.rmdir(d)
            except Exception:
                pass
    try:
        os.rmdir(path)
    except Exception:
        pass


def _parse_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}

