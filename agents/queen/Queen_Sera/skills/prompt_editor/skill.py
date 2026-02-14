from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import Toolkit
from agentscope.tool import ToolResponse

_DEFAULT_ROLE_KEY: str = ""


def _effective_role_key(role_key: str) -> str:
    rk = str(role_key or "").strip()
    if rk:
        return rk
    return str(_DEFAULT_ROLE_KEY or "").strip()



def _now_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _utils_logs_dir() -> str:
    return os.path.join(_base_dir(), "utils", "logs")


def _prompt_editor_log_path() -> str:
    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(_utils_logs_dir(), f"prompt_editor_{day}.jsonl")


def _log_event(payload: dict[str, Any]) -> None:
    try:
        os.makedirs(_utils_logs_dir(), exist_ok=True)
        p = dict(payload or {})
        p.setdefault("ts", _now_ts())
        p.setdefault("source", "prompt_editor")
        with open(_prompt_editor_log_path(), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(p, ensure_ascii=False))
            f.write("\n")
    except Exception:
        return


def _base_dir() -> str:
    from services.role_config_store import get_base_dir

    return get_base_dir()


def _load_roles() -> dict[str, Any]:
    from services.role_config_store import load_roles

    return load_roles(_base_dir())


def _save_roles(roles: dict[str, Any]) -> None:
    from services.role_config_store import save_roles

    save_roles(roles, _base_dir())


def _make_text_response(text: str, metadata: dict[str, Any] | None = None) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=str(text or ""))], metadata=dict(metadata or {}))


def _read_system_prompt_value(*, role_key: str) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key is required")
    roles = _load_roles()
    if rk not in roles:
        raise KeyError(f"unknown role_key: {rk}")
    role = roles[rk]
    return str(getattr(role, "sys_prompt", "") or "")


def read_system_prompt(*, role_key: str = "") -> ToolResponse:
    """
    [一句话总结]
    读取指定角色的 system prompt 文本。

    [场景描述：什么时候该调用这个技能/什么时候不该调用]
    - 何时调用：需要查看某个角色当前配置的 prompt 内容，用于诊断或作为更新前基线。
    - 何时不该调用：你只想让运行中立即生效的行为（那是 update/append 的 apply_runtime 语义），或你其实要改 prompt（应调用 update/append）。

    Args:
        role_key: str. 目标角色键（如 queen_sera/king_tru/...）。可为空；为空时默认使用“当前发起调用的智能体 role_key”。

    Returns:
        ToolResponse: 返回文本为 prompt 内容；metadata 包含 role_key、ok、prompt_len、prompt_sha256（失败时 ok=false 且文本以 Error: 开头）。
    """
    rk = _effective_role_key(role_key)
    try:
        out = _read_system_prompt_value(role_key=rk)
        _log_event({"action": "read", "role_key": rk, "ok": True, "prompt_len": len(out), "prompt_sha256": _sha256_text(out)})
        return _make_text_response(out, {"role_key": rk, "ok": True, "prompt_len": len(out), "prompt_sha256": _sha256_text(out)})
    except Exception as e:
        _log_event({"action": "read", "role_key": rk, "ok": False, "error": str(e)})
        return _make_text_response(f"Error: {e}", {"role_key": rk, "ok": False})


def _resolve_role_key_by_agent_name(*, agent_name: str) -> str:
    name = str(agent_name or "").strip()
    if not name:
        raise ValueError("agent_name is required")
    roles = _load_roles()
    matches = [rk for rk, r in roles.items() if str(getattr(r, "name", "") or "").strip() == name]
    if not matches:
        raise KeyError(f"unknown agent_name: {name}")
    if len(matches) > 1:
        raise ValueError(f"agent_name duplicated: {name} -> {matches}")
    return matches[0]


def _apply_runtime_prompt(*, role_key: str) -> bool:
    from services.runtime_context import get_current_runtime

    runtime = get_current_runtime()
    if runtime is None:
        return False
    apply_fn = getattr(runtime, "apply_system_prompt", None)
    if apply_fn is None or not callable(apply_fn):
        raise AttributeError("runtime.apply_system_prompt not available")
    apply_fn(role_key=str(role_key))
    return True


def _get_runtime_prompt(*, role_key: str) -> str | None:
    from services.runtime_context import get_current_runtime

    runtime = get_current_runtime()
    if runtime is None:
        return None
    apply_fn = getattr(runtime, "apply_system_prompt", None)
    get_agent = getattr(runtime, "_get_agent_by_role_key", None)
    if apply_fn is None or not callable(apply_fn) or get_agent is None or not callable(get_agent):
        return None
    apply_fn(role_key=str(role_key))
    agent = get_agent(role_key=str(role_key))
    return str(getattr(agent, "sys_prompt", "") or "")


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _truncate_prompt(text: str, *, max_chars: int = 4000) -> tuple[str, bool]:
    s = str(text or "")
    m = int(max_chars)
    if m <= 0:
        return "", bool(s)
    if len(s) <= m:
        return s, False
    head = s[: max(1, m - 64)]
    tail = s[-64:] if len(s) > 64 else ""
    return f"{head}\n...\n{tail}", True


def _update_system_prompt_value(*, role_key: str, sys_prompt: str, apply_runtime: bool = True) -> dict[str, Any]:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key is required")
    prompt = str(sys_prompt or "")
    if not prompt.strip():
        raise ValueError("sys_prompt is empty")

    _log_event(
        {
            "action": "update_start",
            "role_key": rk,
            "apply_runtime": bool(apply_runtime),
            "prompt_len": len(prompt),
            "prompt_sha256": _sha256_text(prompt),
        }
    )

    roles = _load_roles()
    if rk not in roles:
        raise KeyError(f"unknown role_key: {rk}")
    role = roles[rk]
    setattr(role, "sys_prompt", prompt)
    _save_roles(roles)
    _log_event({"action": "persisted", "role_key": rk, "ok": True})

    applied = False
    runtime_prompt = None
    if bool(apply_runtime):
        _log_event({"action": "apply_runtime_attempt", "role_key": rk})
        runtime_prompt = _get_runtime_prompt(role_key=rk)
        applied = runtime_prompt is not None
        if not applied:
            applied = _apply_runtime_prompt(role_key=rk)
            _log_event({"action": "apply_runtime_fallback", "role_key": rk, "applied": bool(applied)})

    runtime_contains = bool(runtime_prompt) and (prompt in str(runtime_prompt))
    runtime_preview, truncated = _truncate_prompt(str(runtime_prompt or ""))
    result = {
        "role_key": rk,
        "persisted": True,
        "applied": bool(applied),
        "ok": bool(applied) and bool(runtime_contains),
        "runtime_contains": bool(runtime_contains),
        "runtime_len": len(str(runtime_prompt or "")),
        "runtime_sha256": _sha256_text(str(runtime_prompt or "")) if runtime_prompt is not None else "",
        "runtime_sys_prompt": runtime_preview,
        "runtime_sys_prompt_truncated": bool(truncated),
    }
    _log_event(
        {
            "action": "verify",
            "role_key": rk,
            "persisted": True,
            "applied": bool(applied),
            "runtime_contains": bool(runtime_contains),
            "ok": bool(result.get("ok")),
            "runtime_len": int(result.get("runtime_len") or 0),
            "runtime_sha256": str(result.get("runtime_sha256") or ""),
        }
    )
    return result


def update_system_prompt(*, role_key: str = "", sys_prompt: str = "", apply_runtime: bool = True) -> ToolResponse:
    """
    [一句话总结]
    全量覆盖更新指定角色的 system prompt（覆盖写入）。

    [场景描述：什么时候该调用这个技能/什么时候不该调用]
    - 何时调用：你要把某个角色的 prompt 替换成“一段完整的新提示词”（只用本函数即可）。
    - 何时不该调用：你只是想在原 prompt 后追加一小段补充规则（应调用 append_system_prompt）。

    Args:
        role_key: str. 目标角色键（如 queen_sera/king_tru/...）。可为空；为空时默认使用“当前发起调用的智能体 role_key”。
        sys_prompt: str. 新的完整 prompt 文本。约束：不能为空/不能全空白。
        apply_runtime: bool. 是否尝试对运行中的 agent 立即生效（仅影响后续轮次）。默认 True。

    Returns:
        ToolResponse: 返回文本为执行摘要（OK: ... 或 Error: ...）；metadata 包含 role_key、persisted、applied、ok、runtime_contains 等校验字段及 prompt_len/prompt_sha256。
    """
    rk = _effective_role_key(role_key)
    prompt = str(sys_prompt or "")
    apply_rt = bool(apply_runtime)
    try:
        result = _update_system_prompt_value(role_key=rk, sys_prompt=prompt, apply_runtime=apply_rt)
        text = f"OK: role_key={result.get('role_key')} persisted={result.get('persisted')} applied={result.get('applied')} ok={result.get('ok')}"
        meta = dict(result)
        meta["prompt_len"] = len(prompt)
        meta["prompt_sha256"] = _sha256_text(prompt)
        return _make_text_response(text, meta)
    except Exception as e:
        _log_event({"action": "update_error", "role_key": rk, "ok": False, "error": str(e)})
        return _make_text_response(f"Error: {e}", {"role_key": rk, "ok": False, "apply_runtime": apply_rt, "prompt_len": len(prompt), "prompt_sha256": _sha256_text(prompt)})


def append_system_prompt(*, role_key: str = "", appendix: str = "", apply_runtime: bool = True) -> ToolResponse:
    """
    [一句话总结]
    在指定角色的现有 system prompt 末尾追加一段补充文本（追加写入）。

    [场景描述：什么时候该调用这个技能/什么时候不该调用]
    - 何时调用：你只想“在原 prompt 上加一小段补充/规则/补丁段落”，并保留原有内容。
    - 何时不该调用：appendix 本身就是一整份完整提示词；这种情况应改用 update_system_prompt 做全覆盖更新，否则容易出现重复内容。

    Args:
        role_key: str. 目标角色键（如 queen_sera/king_tru/...）。可为空；为空时默认使用“当前发起调用的智能体 role_key”。
        appendix: str. 要追加的文本（建议为短补充段落）。约束：不能为空/不能全空白。
        apply_runtime: bool. 是否尝试对运行中的 agent 立即生效（仅影响后续轮次）。默认 True。

    Returns:
        ToolResponse: 返回文本为执行摘要（OK: ... 或 Error: ...）；metadata 包含 role_key、persisted、applied、ok 及 appendix_len/appendix_sha256 等字段。
    """
    rk = _effective_role_key(role_key)
    add = str(appendix or "")
    apply_rt = bool(apply_runtime)
    try:
        if not rk:
            raise ValueError("role_key is required")
        if not add.strip():
            raise ValueError("appendix is empty")
        _log_event({"action": "append_start", "role_key": rk, "apply_runtime": apply_rt, "appendix_len": len(add), "appendix_sha256": _sha256_text(add)})
        old = _read_system_prompt_value(role_key=rk)
        sep = "\n\n" if old.strip() and not old.endswith("\n") else ("\n" if old.strip() else "")
        new_prompt = f"{old}{sep}{add}".rstrip() + "\n"
        result = _update_system_prompt_value(role_key=rk, sys_prompt=new_prompt, apply_runtime=apply_rt)
        text = f"OK: role_key={result.get('role_key')} persisted={result.get('persisted')} applied={result.get('applied')} ok={result.get('ok')}"
        meta = dict(result)
        meta["appendix_len"] = len(add)
        meta["appendix_sha256"] = _sha256_text(add)
        meta["prompt_len"] = len(new_prompt)
        meta["prompt_sha256"] = _sha256_text(new_prompt)
        return _make_text_response(text, meta)
    except Exception as e:
        _log_event({"action": "append_error", "role_key": rk, "ok": False, "error": str(e)})
        return _make_text_response(f"Error: {e}", {"role_key": rk, "ok": False, "apply_runtime": apply_rt, "appendix_len": len(add), "appendix_sha256": _sha256_text(add)})


def update_system_prompt_by_agent_name(*, agent_name: str, sys_prompt: str, apply_runtime: bool = True) -> ToolResponse:
    """
    [一句话总结]
    按智能体名称定位角色并全量覆盖更新其 system prompt。

    [场景描述：什么时候该调用这个技能/什么时候不该调用]
    - 何时调用：你只知道智能体 name（例如 UI 显示名“蚁后_瑟拉”），不知道 role_key，但需要做“全覆盖更新”。
    - 何时不该调用：你已知 role_key；这种情况直接调用 update_system_prompt 更直接、错误更少。

    Args:
        agent_name: str. 智能体显示名（例如 “蚁后_瑟拉”）。约束：不能为空/不能全空白。
        sys_prompt: str. 新的完整 prompt 文本。约束：不能为空/不能全空白。
        apply_runtime: bool. 是否尝试对运行中的 agent 立即生效（仅影响后续轮次）。默认 True。

    Returns:
        ToolResponse: 返回文本为执行摘要（OK: ... 或 Error: ...）；metadata 包含 agent_name、role_key（成功时）、persisted、applied、ok 及 prompt_len/prompt_sha256。
    """
    name = str(agent_name or "").strip()
    prompt = str(sys_prompt or "")
    apply_rt = bool(apply_runtime)
    _log_event({"action": "update_by_name_start", "agent_name": name, "apply_runtime": apply_rt, "prompt_len": len(prompt), "prompt_sha256": _sha256_text(prompt)})
    try:
        rk = _resolve_role_key_by_agent_name(agent_name=name)
        result = _update_system_prompt_value(role_key=rk, sys_prompt=prompt, apply_runtime=apply_rt)
        text = f"OK: role_key={result.get('role_key')} persisted={result.get('persisted')} applied={result.get('applied')} ok={result.get('ok')}"
        meta = dict(result)
        meta["agent_name"] = name
        meta["prompt_len"] = len(prompt)
        meta["prompt_sha256"] = _sha256_text(prompt)
        return _make_text_response(text, meta)
    except Exception as e:
        _log_event({"action": "update_by_name_error", "agent_name": name, "ok": False, "error": str(e)})
        return _make_text_response(f"Error: {e}", {"agent_name": name, "ok": False, "apply_runtime": apply_rt, "prompt_len": len(prompt), "prompt_sha256": _sha256_text(prompt)})


def register(toolkit: Toolkit) -> None:
    global _DEFAULT_ROLE_KEY
    _DEFAULT_ROLE_KEY = str(getattr(toolkit, "role_key", "") or "").strip()
    toolkit.register_tool_function(read_system_prompt)
    toolkit.register_tool_function(update_system_prompt)
    toolkit.register_tool_function(append_system_prompt)
    toolkit.register_tool_function(update_system_prompt_by_agent_name)
