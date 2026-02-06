from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass
class RoleInfo:
    role_key: str
    name: str
    max_iters: int
    heartbeat: dict[str, Any]
    sys_prompt: str
    raw_agent_cfg: dict[str, Any]


def get_base_dir() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def load_roles(base_dir: str | None = None) -> dict[str, RoleInfo]:
    base_dir = base_dir or get_base_dir()
    agent_cfg_path = os.path.join(base_dir, "configs", "agent_configs.yaml")
    prompt_cfg_path = os.path.join(base_dir, "configs", "prompts", "system_prompts.yaml")

    agent_cfg = _load_yaml(agent_cfg_path)
    prompt_cfg = _load_yaml(prompt_cfg_path)

    roles: dict[str, RoleInfo] = {}

    for role_key, role_cfg in (agent_cfg or {}).items():
        if not isinstance(role_key, str) or not role_key.strip():
            continue
        rcfg = role_cfg if isinstance(role_cfg, dict) else {}
        name = str(rcfg.get("name") or role_key)
        max_iters = int(rcfg.get("max_iters") or 8)
        heartbeat = rcfg.get("heartbeat")
        hb = heartbeat if isinstance(heartbeat, dict) else {}
        sys_prompt = str(prompt_cfg.get(role_key) or "")
        roles[role_key] = RoleInfo(
            role_key=role_key,
            name=name,
            max_iters=max_iters,
            heartbeat=dict(hb),
            sys_prompt=sys_prompt,
            raw_agent_cfg=dict(rcfg),
        )

    for role_key, prompt in (prompt_cfg or {}).items():
        if not isinstance(role_key, str) or not role_key.strip():
            continue
        if role_key in roles:
            continue
        roles[role_key] = RoleInfo(
            role_key=role_key,
            name=role_key,
            max_iters=8,
            heartbeat={},
            sys_prompt=str(prompt or ""),
            raw_agent_cfg={},
        )

    return roles


def validate_roles(roles: dict[str, RoleInfo]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for role_key, role in roles.items():
        rk = str(role_key or "").strip()
        if not rk:
            errors.append("存在空的 role_key。")
            continue
        if rk in seen:
            errors.append(f"role_key 重复：{rk}")
        seen.add(rk)

        if not str(role.name or "").strip():
            errors.append(f"{rk} 的 name 不能为空。")

        try:
            if int(role.max_iters) <= 0:
                errors.append(f"{rk} 的 max_iters 必须为正整数。")
        except Exception:
            errors.append(f"{rk} 的 max_iters 不是有效整数。")

        if role.heartbeat is not None and not isinstance(role.heartbeat, dict):
            errors.append(f"{rk} 的 heartbeat 必须为对象。")
        else:
            try:
                _normalize_heartbeat(role.heartbeat or {})
            except Exception as e:
                errors.append(f"{rk} 的 heartbeat 无效：{e}")

        if not str(role.sys_prompt or "").strip():
            errors.append(f"{rk} 的 system prompt 不能为空。")

    return errors


def save_roles(roles: dict[str, RoleInfo], base_dir: str | None = None) -> None:
    base_dir = base_dir or get_base_dir()
    errors = validate_roles(roles)
    if errors:
        raise ValueError("\n".join(errors))

    agent_cfg_path = os.path.join(base_dir, "configs", "agent_configs.yaml")
    prompt_cfg_path = os.path.join(base_dir, "configs", "prompts", "system_prompts.yaml")

    agent_data: dict[str, Any] = {}
    prompt_data: dict[str, Any] = {}

    for role_key, role in roles.items():
        rk = str(role_key).strip()
        if not rk:
            continue

        rcfg = dict(role.raw_agent_cfg or {})
        rcfg["name"] = str(role.name)
        rcfg["max_iters"] = int(role.max_iters)
        if role.heartbeat is None:
            rcfg.pop("heartbeat", None)
        else:
            rcfg["heartbeat"] = _normalize_heartbeat(dict(role.heartbeat or {}))
        agent_data[rk] = rcfg
        prompt_data[rk] = str(role.sys_prompt or "")

    _safe_write_yaml(agent_cfg_path, agent_data)
    _safe_write_yaml(prompt_cfg_path, prompt_data)


def _load_yaml(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


_HB_BOOL_KEYS: set[str] = {"enabled"}
_HB_INT_KEYS: set[str] = {"history_window_n"}
_HB_FLOAT_KEYS: set[str] = {
    "interval_s",
    "jitter_s",
    "idle_no_increment_s",
    "topic_cooldown_s",
    "topic_active_s",
    "topic_decision_min_gap_s",
    "topic_turn_interval_s",
}


def _normalize_heartbeat(hb: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (hb or {}).items():
        if not isinstance(k, str) or not k.strip():
            continue
        key = k.strip()
        if key in _HB_BOOL_KEYS:
            out[key] = _parse_bool(v)
            continue
        if key in _HB_INT_KEYS:
            out[key] = int(v)
            continue
        if key in _HB_FLOAT_KEYS:
            out[key] = float(v)
            continue
        out[key] = v
    return out


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _safe_write_yaml(path: str, data: dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(
                data,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=1000,
            )
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

