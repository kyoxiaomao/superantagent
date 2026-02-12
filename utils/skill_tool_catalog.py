from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillInterface:
    name: str
    signature: str


@dataclass(frozen=True)
class SkillArtifact:
    key: str
    title: str
    doc_path: str
    script_path: str
    doc_markdown: str
    interfaces: list[SkillInterface]
    errors: list[str]


@dataclass(frozen=True)
class CompositeToolStep:
    idx: int
    skill_key: str
    interface_name: str
    note: str
    params: dict[str, Any]


@dataclass(frozen=True)
class CompositeToolArtifact:
    key: str
    title: str
    doc_path: str
    spec_path: str
    doc_markdown: str
    steps: list[CompositeToolStep]
    errors: list[str]


@dataclass(frozen=True)
class SkillToolCatalog:
    skills: list[SkillArtifact]
    tools: list[CompositeToolArtifact]


_MD_H2_TOOL_LIST = "## 工具列表"


def get_repo_root() -> str:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    return repo_root


def load_catalog(repo_root: str | None = None) -> SkillToolCatalog:
    root = repo_root or get_repo_root()
    return SkillToolCatalog(
        skills=_scan_skills(repo_root=root),
        tools=_scan_tools(repo_root=root),
    )


def _scan_skills(*, repo_root: str) -> list[SkillArtifact]:
    base_dir = os.path.join(repo_root, "utils", "allskill")
    if not os.path.isdir(base_dir):
        return []

    out: list[SkillArtifact] = []
    for dirpath, _dirnames, filenames in os.walk(base_dir):
        if "skill.md" not in filenames and "skill.py" not in filenames:
            continue
        if "skill.md" not in filenames or "skill.py" not in filenames:
            continue

        doc_path = os.path.join(dirpath, "skill.md")
        script_path = os.path.join(dirpath, "skill.py")
        key = os.path.basename(dirpath)
        md = _read_text(doc_path)
        title = _extract_md_h1_title(md) or key
        interfaces, errs = _extract_interfaces(md_markdown=md, script_path=script_path)
        out.append(
            SkillArtifact(
                key=key,
                title=title,
                doc_path=doc_path,
                script_path=script_path,
                doc_markdown=md,
                interfaces=interfaces,
                errors=errs,
            )
        )
    out.sort(key=lambda s: (s.key.lower(), s.title))
    return out


def _scan_tools(*, repo_root: str) -> list[CompositeToolArtifact]:
    base_dir = os.path.join(repo_root, "utils", "alltool")
    if not os.path.isdir(base_dir):
        return []

    out: list[CompositeToolArtifact] = []
    for dirpath, _dirnames, filenames in os.walk(base_dir):
        if "tool.md" not in filenames and "tool.json" not in filenames:
            continue
        if "tool.md" not in filenames or "tool.json" not in filenames:
            continue

        doc_path = os.path.join(dirpath, "tool.md")
        spec_path = os.path.join(dirpath, "tool.json")
        key = os.path.basename(dirpath)
        md = _read_text(doc_path)
        title = _extract_md_h1_title(md) or key
        steps, errs = _parse_tool_spec(spec_path=spec_path)
        out.append(
            CompositeToolArtifact(
                key=key,
                title=title,
                doc_path=doc_path,
                spec_path=spec_path,
                doc_markdown=md,
                steps=steps,
                errors=errs,
            )
        )
    out.sort(key=lambda t: (t.key.lower(), t.title))
    return out


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_md_h1_title(md: str) -> str:
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def _extract_interfaces(*, md_markdown: str, script_path: str) -> tuple[list[SkillInterface], list[str]]:
    md_ifaces = _extract_interfaces_from_md(md_markdown)
    if md_ifaces:
        return md_ifaces, []

    py_ifaces, errs = _extract_interfaces_from_script(script_path)
    if py_ifaces:
        return py_ifaces, errs
    return [], errs + ["未在 skill.md 找到“工具列表”，且无法从 skill.py 推断注册接口。"]


def _extract_interfaces_from_md(md: str) -> list[SkillInterface]:
    lines = (md or "").splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == _MD_H2_TOOL_LIST:
            start_idx = i + 1
            break
    if start_idx < 0:
        return []

    out: list[SkillInterface] = []
    for line in lines[start_idx:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("## "):
            break
        if not s.startswith("- "):
            continue
        sig = s[2:].strip()
        name = _extract_callable_name(sig)
        out.append(SkillInterface(name=name, signature=sig))
    return out


_CALLABLE_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _extract_callable_name(signature_line: str) -> str:
    m = _CALLABLE_NAME_RE.match(signature_line.strip())
    if m:
        return m.group(1)
    return signature_line.strip().split()[0]


def _extract_interfaces_from_script(script_path: str) -> tuple[list[SkillInterface], list[str]]:
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=script_path)
    except Exception as e:
        return [], [f"解析 skill.py 失败：{e}"]

    func_defs: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            func_defs[node.name] = node

    register_def = func_defs.get("register")
    if register_def is None:
        return [], ["skill.py 缺少 register(toolkit) 函数，无法推断接口。"]

    registered: list[str] = []
    for node in ast.walk(register_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_register_call = False
        if isinstance(func, ast.Attribute) and func.attr == "register_tool_function":
            is_register_call = True
        if isinstance(func, ast.Name) and func.id == "register_tool_function":
            is_register_call = True
        if not is_register_call:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            registered.append(first.id)

    out: list[SkillInterface] = []
    errs: list[str] = []
    for name in registered:
        fdef = func_defs.get(name)
        if fdef is None:
            out.append(SkillInterface(name=name, signature=f"{name}(...)"))
            errs.append(f"register() 注册了 {name}，但模块内未找到同名函数定义。")
            continue
        out.append(SkillInterface(name=name, signature=_format_signature_from_ast(fdef)))

    return out, errs


def _format_signature_from_ast(func_def: ast.FunctionDef) -> str:
    args = func_def.args
    parts: list[str] = []

    posonly = getattr(args, "posonlyargs", [])
    for a in posonly:
        parts.append(a.arg)
    if posonly:
        parts.append("/")

    for a in args.args:
        parts.append(a.arg)

    if args.vararg is not None:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")

    for a in args.kwonlyargs:
        parts.append(a.arg)

    if args.kwarg is not None:
        parts.append(f"**{args.kwarg.arg}")

    return f"{func_def.name}({', '.join(parts)})"


def _parse_tool_spec(*, spec_path: str) -> tuple[list[CompositeToolStep], list[str]]:
    errs: list[str] = []
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return [], [f"解析 tool.json 失败：{e}"]

    if not isinstance(data, dict):
        return [], ["tool.json 内容格式不正确，应为对象（dict）。"]

    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list):
        return [], ["tool.json 缺少 steps 列表。"]

    steps: list[CompositeToolStep] = []
    for i, item in enumerate(steps_raw):
        if not isinstance(item, dict):
            errs.append(f"steps[{i}] 不是对象。")
            continue
        skill_key = str(item.get("skill") or "").strip()
        interface_name = str(item.get("interface") or "").strip()
        note = str(item.get("note") or "").strip()
        params = item.get("params") or {}
        if not isinstance(params, dict):
            params = {}
            errs.append(f"steps[{i}].params 不是对象，已忽略。")
        if not skill_key:
            errs.append(f"steps[{i}] 缺少 skill。")
            continue
        if not interface_name:
            errs.append(f"steps[{i}] 缺少 interface。")
            continue
        steps.append(CompositeToolStep(idx=i + 1, skill_key=skill_key, interface_name=interface_name, note=note, params=params))

    return steps, errs
