"""
agent 装备库（skills/tools）管理。

负责将技能脚本/文档安全写入每个 Agent 的个人目录 `agent_home/skills/`，
并扫描并加载该 Agent 的个人装备库：
- skills：加载 `skill.py` 并执行 `register(toolkit)` 完成工具函数注册
- tools：扫描 `tool.md/tool.json` 形成工具工件索引
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from types import ModuleType
from typing import Any

from agentscope.tool import Toolkit

from utils.agent_home_locator import get_agent_skill_dir, get_agent_tool_dir


_SKILL_GROUP_PREFIX = "skill:"
_TOOL_GROUP_PREFIX = "tool:"


def _skill_group_name(skill_key: str) -> str:
    return f"{_SKILL_GROUP_PREFIX}{str(skill_key or '').strip()}"


def _tool_group_name(tool_key: str) -> str:
    return f"{_TOOL_GROUP_PREFIX}{str(tool_key or '').strip()}"


class _SkillGroupToolkit:
    def __init__(self, *, toolkit: Toolkit, group_name: str, role_key: str) -> None:
        self._toolkit = toolkit
        self._group_name = group_name
        self.role_key = str(role_key or "").strip()

    def register_tool_function(self, tool_func: Any, **kwargs: Any) -> None:
        group_name = kwargs.get("group_name")
        if not group_name or str(group_name) == "basic":
            kwargs["group_name"] = self._group_name
        if "namesake_strategy" not in kwargs:
            kwargs["namesake_strategy"] = "override"
        self._toolkit.register_tool_function(tool_func, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._toolkit, name)


class _ToolGroupToolkit:
    def __init__(self, *, toolkit: Toolkit, group_name: str, role_key: str) -> None:
        self._toolkit = toolkit
        self._group_name = group_name
        self.role_key = str(role_key or "").strip()

    def register_tool_function(self, tool_func: Any, **kwargs: Any) -> None:
        group_name = kwargs.get("group_name")
        if not group_name or str(group_name) == "basic":
            kwargs["group_name"] = self._group_name
        if "namesake_strategy" not in kwargs:
            kwargs["namesake_strategy"] = "override"
        self._toolkit.register_tool_function(tool_func, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._toolkit, name)


def get_skills_dir(*, role_key: str, base_dir: str | None = None) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key 不能为空。")
    repo_root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return get_agent_skill_dir(repo_root=repo_root, role_key=rk)


def get_tools_dir(*, role_key: str, base_dir: str | None = None) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key 不能为空。")
    repo_root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return get_agent_tool_dir(repo_root=repo_root, role_key=rk)


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


def load_utils(
    toolkit: Toolkit, *, role_key: str, prefix: str = "", base_dir: str | None = None
) -> dict[str, list[str]]:
    skills_dir = get_skills_dir(role_key=role_key, base_dir=base_dir)
    tools_dir = get_tools_dir(role_key=role_key, base_dir=base_dir)

    desired_skill_groups: set[str] = set()
    loaded_skills: list[str] = []
    if os.path.isdir(skills_dir):
        for name in os.listdir(skills_dir):
            if prefix and not str(name).startswith(prefix):
                continue
            skill_dir = os.path.join(skills_dir, name)
            if not os.path.isdir(skill_dir):
                continue
            file_path = os.path.join(skill_dir, "skill.py")
            if not os.path.isfile(file_path):
                continue
            group_name = _skill_group_name(str(name))
            desired_skill_groups.add(group_name)
            if group_name not in getattr(toolkit, "groups", {}):
                toolkit.create_tool_group(group_name, description=f"skill:{name}", active=True)
            else:
                toolkit.update_tool_groups([group_name], active=True)
            mod = _load_module_from_path(file_path=file_path, role_key=role_key, module_key=str(name))
            register = getattr(mod, "register", None)
            if register is None or not callable(register):
                continue
            register(_SkillGroupToolkit(toolkit=toolkit, group_name=group_name, role_key=role_key))
            loaded_skills.append(os.path.join(str(name), "skill.py"))

    desired_tool_groups: set[str] = set()
    found_tools: list[str] = []
    loaded_tools: list[str] = []
    if os.path.isdir(tools_dir):
        for name in os.listdir(tools_dir):
            if prefix and not str(name).startswith(prefix):
                continue
            tool_dir = os.path.join(tools_dir, name)
            if not os.path.isdir(tool_dir):
                continue
            mp = os.path.join(tool_dir, "tool.md")
            jp = os.path.join(tool_dir, "tool.json")
            tp = os.path.join(tool_dir, "tool.py")
            group_name = _tool_group_name(str(name))
            desired_tool_groups.add(group_name)
            if group_name not in getattr(toolkit, "groups", {}):
                toolkit.create_tool_group(group_name, description=f"tool:{name}", active=True)
            else:
                toolkit.update_tool_groups([group_name], active=True)
            if os.path.isfile(mp):
                found_tools.append(os.path.join(str(name), "tool.md"))
            if os.path.isfile(jp):
                found_tools.append(os.path.join(str(name), "tool.json"))
            if os.path.isfile(tp):
                mod = _load_module_from_path(file_path=tp, role_key=role_key, module_key=f"tool_{name}")
                register = getattr(mod, "register", None)
                if register is not None and callable(register):
                    register(_ToolGroupToolkit(toolkit=toolkit, group_name=group_name, role_key=role_key))
                    loaded_tools.append(os.path.join(str(name), "tool.py"))

    existing_groups = list(getattr(toolkit, "groups", {}).keys())
    remove_skill_groups = [g for g in existing_groups if str(g).startswith(_SKILL_GROUP_PREFIX) and g not in desired_skill_groups]
    remove_tool_groups = [g for g in existing_groups if str(g).startswith(_TOOL_GROUP_PREFIX) and g not in desired_tool_groups]
    remove_groups = list(dict.fromkeys(remove_skill_groups + remove_tool_groups))
    if remove_groups:
        toolkit.remove_tool_groups(remove_groups)

    return {"skills": loaded_skills, "tools": found_tools, "loaded_tools": loaded_tools}


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


def _load_module_from_path(*, file_path: str, role_key: str, module_key: str = "") -> ModuleType:
    base = os.path.splitext(os.path.basename(file_path))[0]
    rk = _sanitize_module_seg(role_key)
    mod_seg = _sanitize_module_seg(base)
    mk = _sanitize_module_seg(module_key)
    tail = f"{mk}_{mod_seg}" if mk else mod_seg
    module_name = f"agents.personal_skills._scoped_{rk}.{tail}"
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
