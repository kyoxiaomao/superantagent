"""
智能体基础工厂。

统一从配置加载角色参数与 system prompt，创建 AgentScope `ReActAgent`，
并为所有角色挂载记忆（短期/长期）与结构化事件日志 hooks。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml

from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit

from memory import build_memory_bundle
from services import ModelBundle
from services.event_logger import log_event, log_msg


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class AntAgentConfig:
    name: str
    sys_prompt: str
    max_iters: int


def load_agent_config(role_key: str) -> AntAgentConfig:
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    agent_cfg = _load_yaml(os.path.join(root, "configs", "agent_configs.yaml"))
    prompts_cfg = _load_yaml(os.path.join(root, "configs", "prompts", "system_prompts.yaml"))

    role_cfg = agent_cfg.get(role_key) or {}
    name = str(role_cfg.get("name") or role_key)
    max_iters = int(role_cfg.get("max_iters") or 8)
    sys_prompt = str(prompts_cfg.get(role_key) or "")

    if not sys_prompt:
        raise ValueError(f"未找到角色 {role_key} 的 system prompt 配置。")

    return AntAgentConfig(name=name, sys_prompt=sys_prompt, max_iters=max_iters)


def create_react_ant_agent(
    *,
    role_key: str,
    model_bundle: ModelBundle,
    toolkit: Toolkit | None = None,
) -> ReActAgent:
    cfg = load_agent_config(role_key)
    mem = build_memory_bundle(agent_name=cfg.name)
    tk = toolkit or Toolkit()

    agent = ReActAgent(
        name=cfg.name,
        sys_prompt=cfg.sys_prompt,
        model=model_bundle.model,
        formatter=model_bundle.formatter,
        toolkit=tk,
        memory=mem.short_term,
        long_term_memory=mem.long_term,
        compression_config=mem.compression_config,
        max_iters=cfg.max_iters,
    )

    def _pre_observe_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            log_msg(event_type="observe", agent=str(getattr(self, "name", "")), msg=msg)
        return kwargs

    def _pre_reply_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            log_msg(event_type="pre_reply", agent=str(getattr(self, "name", "")), msg=msg)
        return kwargs

    def _post_reply_hook(self: Any, kwargs: dict[str, Any], output: Any) -> Any:
        if output is not None:
            log_msg(event_type="post_reply", agent=str(getattr(self, "name", "")), msg=output)
        return output

    def _pre_print_hook(self: Any, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        msg = kwargs.get("msg")
        if msg is not None:
            log_msg(event_type="print", agent=str(getattr(self, "name", "")), msg=msg)
        return kwargs

    agent.register_instance_hook("pre_observe", "antagent_log_pre_observe", _pre_observe_hook)
    agent.register_instance_hook("pre_reply", "antagent_log_pre_reply", _pre_reply_hook)
    agent.register_instance_hook("post_reply", "antagent_log_post_reply", _post_reply_hook)
    agent.register_instance_hook("pre_print", "antagent_log_pre_print", _pre_print_hook)

    log_event(event_type="agent_created", agent=cfg.name, payload={"role_key": role_key})
    return agent

