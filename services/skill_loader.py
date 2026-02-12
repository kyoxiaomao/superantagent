"""
动态技能管理。

负责将兵蚁生成的技能脚本/文档安全写入 `agents/skills/`，并动态加载脚本模块的 `register(toolkit)` 完成工具注册。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from types import ModuleType
from typing import Any, Callable

from agentscope.tool import Toolkit


def get_skills_dir(*, role_key: str, base_dir: str | None = None) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key 不能为空。")
    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return os.path.join(root, "agents", "skills", rk)


def safe_write_skill_file(*, role_key: str, file_name: str, content: str, base_dir: str | None = None) -> str:
    if not file_name.endswith(".py"):
        raise ValueError("技能文件必须以 .py 结尾。")

    if os.path.sep in file_name or "/" in file_name or "\\" in file_name:
        raise ValueError("技能文件名不允许包含路径分隔符。")

    _validate_python_source(content)

    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    os.makedirs(skills_dir, exist_ok=True)
    full_path = os.path.join(skills_dir, file_name)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return full_path


def safe_write_skill_doc(*, role_key: str, file_name: str, content: str, base_dir: str | None = None) -> str:
    if not file_name.endswith(".md"):
        raise ValueError("技能文档必须以 .md 结尾。")

    if os.path.sep in file_name or "/" in file_name or "\\" in file_name:
        raise ValueError("技能文档文件名不允许包含路径分隔符。")

    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    os.makedirs(skills_dir, exist_ok=True)
    full_path = os.path.join(skills_dir, file_name)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return full_path


def list_skill_artifacts(*, role_key: str, base_dir: str | None = None) -> dict[str, list[str]]:
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    if not os.path.isdir(skills_dir):
        return {"scripts": [], "docs": []}
    scripts = sorted([fn for fn in os.listdir(skills_dir) if fn.endswith(".py") and fn != "__init__.py"])
    docs = sorted([fn for fn in os.listdir(skills_dir) if fn.endswith(".md")])
    return {"scripts": scripts, "docs": docs}


def load_skills(toolkit: Toolkit, *, role_key: str, prefix: str = "", base_dir: str | None = None) -> list[str]:
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    if not os.path.isdir(skills_dir):
        return []

    loaded: list[str] = []
    for fn in os.listdir(skills_dir):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        if prefix and not fn.startswith(prefix):
            continue

        file_path = os.path.join(skills_dir, fn)
        mod = _load_module_from_path(file_path=file_path, role_key=role_key)
        register = getattr(mod, "register", None)
        if callable(register):
            register(toolkit)
            loaded.append(fn)

    return loaded


def _sanitize_module_seg(s: str) -> str:
    out = []
    for ch in str(s or "").strip():
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    seg = "".join(out) or "_"
    if seg[0].isdigit():
        seg = "_" + seg
    return seg


def _load_module_from_path(*, file_path: str, role_key: str) -> ModuleType:
    base = os.path.splitext(os.path.basename(file_path))[0]
    rk = _sanitize_module_seg(role_key)
    mod_seg = _sanitize_module_seg(base)
    module_name = f"agents.skills._scoped_{rk}.{mod_seg}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载技能模块：{file_path}")

    module = sys.modules.get(module_name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    spec.loader.exec_module(module)
    return module


def _validate_python_source(source: str) -> None:
    try:
        ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"技能脚本语法错误：{e.msg} (line {e.lineno})") from e

