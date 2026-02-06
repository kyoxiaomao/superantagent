"""
浏览器工蚁智能体工厂。

在创建时加载 `agents/skills/` 下的动态技能，并提供 reload/list 工具便于运行时更新技能。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.skill_loader import list_skill_artifacts, load_skills


def create_browser_worker_agent(model_bundle: ModelBundle) -> object:
    toolkit = Toolkit()
    load_skills(toolkit)

    def reload_skills() -> list[str]:
        return load_skills(toolkit)

    def list_skills() -> dict[str, list[str]]:
        return list_skill_artifacts()

    toolkit.register_tool_function(reload_skills)
    toolkit.register_tool_function(list_skills)

    return create_react_ant_agent(
        role_key="browser_worker",
        model_bundle=model_bundle,
        toolkit=toolkit,
    )

