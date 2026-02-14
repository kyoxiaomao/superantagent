"""
动态技能管理。

负责将技能脚本/文档安全写入每个 Agent 的个人目录 `agent_home/skills/`，
并动态加载 `skill.py` 的 `register(toolkit)` 完成工具注册。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from types import ModuleType
from typing import Any

from agentscope.tool import Toolkit

from utils.agent_home_locator import get_agent_skill_dir


def get_skills_dir(*, role_key: str, base_dir: str | None = None) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key 不能为空。")
    repo_root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return get_agent_skill_dir(repo_root=repo_root, role_key=rk)


def safe_write_skill_file(*, role_key: str, file_name: str, content: str, base_dir: str | None = None) -> str:
    if not file_name.endswith(".py"):
        raise ValueError("技能文件必须以 .py 结尾。")

    if os.path.sep in file_name or "/" in file_name or "\\" in file_name:
        raise ValueError("技能文件名不允许包含路径分隔符。")

    _validate_python_source(content)

    skill_key = os.path.splitext(os.path.basename(file_name))[0]
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    dest_dir = os.path.join(skills_dir, skill_key)
    os.makedirs(dest_dir, exist_ok=True)
    full_path = os.path.join(dest_dir, "skill.py")

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return full_path


def safe_write_skill_doc(*, role_key: str, file_name: str, content: str, base_dir: str | None = None) -> str:
    if not file_name.endswith(".md"):
        raise ValueError("技能文档必须以 .md 结尾。")

    if os.path.sep in file_name or "/" in file_name or "\\" in file_name:
        raise ValueError("技能文档文件名不允许包含路径分隔符。")

    skill_key = os.path.splitext(os.path.basename(file_name))[0]
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    dest_dir = os.path.join(skills_dir, skill_key)
    os.makedirs(dest_dir, exist_ok=True)
    full_path = os.path.join(dest_dir, "skill.md")

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return full_path


def list_skill_artifacts(*, role_key: str, base_dir: str | None = None) -> dict[str, list[str]]:
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    if not os.path.isdir(skills_dir):
        return {"scripts": [], "docs": []}
    scripts: list[str] = []
    docs: list[str] = []
    for name in sorted(os.listdir(skills_dir)):
        p = os.path.join(skills_dir, name)
        if not os.path.isdir(p):
            continue
        sp = os.path.join(p, "skill.py")
        dp = os.path.join(p, "skill.md")
        if os.path.isfile(sp):
            scripts.append(os.path.join(name, "skill.py"))
        if os.path.isfile(dp):
            docs.append(os.path.join(name, "skill.md"))
    return {"scripts": scripts, "docs": docs}


def load_skills(toolkit: Toolkit, *, role_key: str, prefix: str = "", base_dir: str | None = None) -> list[str]:
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    if not os.path.isdir(skills_dir):
        return []

    loaded: list[str] = []
    for name in os.listdir(skills_dir):
        if prefix and not str(name).startswith(prefix):
            continue
        skill_dir = os.path.join(skills_dir, name)
        if not os.path.isdir(skill_dir):
            continue
        file_path = os.path.join(skill_dir, "skill.py")
        if not os.path.isfile(file_path):
            continue
        mod = _load_module_from_path(file_path=file_path, role_key=role_key)
        register = getattr(mod, "register", None)
        if register is None or not callable(register):
            continue
        register(toolkit)
        loaded.append(os.path.join(str(name), "skill.py"))

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
    module_name = f"agents.personal_skills._scoped_{rk}.{mod_seg}"
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

