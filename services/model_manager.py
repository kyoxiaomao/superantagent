"""
模型构建与配置加载。

读取 `configs/model_configs.yaml`（支持 ${ENV:-default} 替换），并构造 AgentScope 可用的 ModelBundle。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import yaml

from agentscope.formatter import OpenAIChatFormatter

from services.glm_chat_model import GLMChatModel


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _resolve_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        def _repl(match: re.Match[str]) -> str:
            key = match.group(1)
            default_value = match.group(2) if match.group(2) is not None else ""
            return os.getenv(key, default_value)

        return _ENV_PATTERN.sub(_repl, value)

    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]

    return value


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _resolve_env_vars(data)


@dataclass(frozen=True)
class ModelBundle:
    model: GLMChatModel
    formatter: OpenAIChatFormatter


def load_glm_bundle(config_path: str) -> ModelBundle:
    cfg = _load_yaml(config_path)
    glm_cfg = cfg.get("glm") or {}

    api_key_env = str(glm_cfg.get("api_key_env") or "GLM_API_KEY")
    api_key = os.getenv(api_key_env)
    include_thinking_raw = glm_cfg.get("include_thinking", os.getenv("GLM_INCLUDE_THINKING", "false"))
    include_thinking = _parse_bool(include_thinking_raw)

    model = GLMChatModel(
        model_name=str(glm_cfg.get("model_name") or "glm-4.5-air"),
        api_key=api_key,
        base_url=str(glm_cfg.get("base_url") or "https://open.bigmodel.cn/api/paas/v4"),
        stream=bool(glm_cfg.get("stream", True)),
        timeout_s=float(glm_cfg.get("timeout_s", 120.0)),
        include_thinking=include_thinking,
    )
    formatter = OpenAIChatFormatter()
    return ModelBundle(model=model, formatter=formatter)


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}
