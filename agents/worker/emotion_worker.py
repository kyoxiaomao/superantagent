"""
情感工蚁智能体工厂。

创建“情感工蚁” ReActAgent：专注陪伴式回应与情绪化表达，不注册工具函数。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle


def create_emotion_worker_agent(model_bundle: ModelBundle) -> object:
    return create_react_ant_agent(
        role_key="emotion_worker",
        model_bundle=model_bundle,
        toolkit=Toolkit(),
    )

