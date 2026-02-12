"""
情感工蚁智能体工厂。

创建“情感工蚁” ReActAgent：专注陪伴式回应与情绪化表达，不注册工具函数。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def create_emotion_worker_agent(model_bundle: ModelBundle) -> object:
    agent = create_react_ant_agent(
        role_key="emotion_worker",
        model_bundle=model_bundle,
        toolkit=Toolkit(),
    )

    async def update(ctx: UpdateContext) -> None:
        """
        情感工蚁心跳（5秒级）。

        情感工蚁未来可在心跳中做：情绪陪伴状态、群聊情绪摘要、提醒/安抚策略等；
        当前先实现最小心跳：UI 心跳 + 群聊增量读取。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent
