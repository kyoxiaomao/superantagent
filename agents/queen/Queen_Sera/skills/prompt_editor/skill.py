from __future__ import annotations

from typing import Any

from agentscope.tool import Toolkit


def _base_dir() -> str:
    from services.role_config_store import get_base_dir

    return get_base_dir()


def _load_roles() -> dict[str, Any]:
    from services.role_config_store import load_roles

    return load_roles(_base_dir())


def _save_roles(roles: dict[str, Any]) -> None:
    from services.role_config_store import save_roles

    save_roles(roles, _base_dir())


def read_system_prompt(*, role_key: str) -> str:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key is required")
    roles = _load_roles()
    if rk not in roles:
        raise KeyError(f"unknown role_key: {rk}")
    role = roles[rk]
    return str(getattr(role, "sys_prompt", "") or "")


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


def update_system_prompt(*, role_key: str, sys_prompt: str, apply_runtime: bool = True) -> dict[str, Any]:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key is required")
    prompt = str(sys_prompt or "")
    if not prompt.strip():
        raise ValueError("sys_prompt is empty")

    roles = _load_roles()
    if rk not in roles:
        raise KeyError(f"unknown role_key: {rk}")
    role = roles[rk]
    setattr(role, "sys_prompt", prompt)
    _save_roles(roles)

    applied = False
    if bool(apply_runtime):
        applied = _apply_runtime_prompt(role_key=rk)
    return {"role_key": rk, "persisted": True, "applied": bool(applied)}


def append_system_prompt(*, role_key: str, appendix: str, apply_runtime: bool = True) -> dict[str, Any]:
    rk = str(role_key or "").strip()
    if not rk:
        raise ValueError("role_key is required")
    add = str(appendix or "")
    if not add.strip():
        raise ValueError("appendix is empty")

    old = read_system_prompt(role_key=rk)
    sep = "\n\n" if old.strip() and not old.endswith("\n") else ("\n" if old.strip() else "")
    new_prompt = f"{old}{sep}{add}".rstrip() + "\n"
    return update_system_prompt(role_key=rk, sys_prompt=new_prompt, apply_runtime=apply_runtime)


def update_system_prompt_by_agent_name(*, agent_name: str, sys_prompt: str, apply_runtime: bool = True) -> dict[str, Any]:
    rk = _resolve_role_key_by_agent_name(agent_name=agent_name)
    return update_system_prompt(role_key=rk, sys_prompt=sys_prompt, apply_runtime=apply_runtime)


def register(toolkit: Toolkit) -> None:
    toolkit.register_tool_function(read_system_prompt)
    toolkit.register_tool_function(update_system_prompt)
    toolkit.register_tool_function(append_system_prompt)
    toolkit.register_tool_function(update_system_prompt_by_agent_name)

