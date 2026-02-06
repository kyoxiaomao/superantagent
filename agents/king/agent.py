"""
蚁王智能体工厂。

创建“蚁王” ReActAgent：负责面向用户对话与任务调度（输出调度 JSON 供运行时解析）。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle


def create_king_agent(model_bundle: ModelBundle) -> object:
    return create_react_ant_agent(
        role_key="king",
        model_bundle=model_bundle,
        toolkit=Toolkit(),
    )

