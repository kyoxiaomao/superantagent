"""
蚁后智能体工厂。

创建“蚁后” ReActAgent：负责系统拓展与自修复建议，不直接执行工具。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def create_queen_agent(model_bundle: ModelBundle) -> object:
    """
    创建蚁后智能体，并挂载蚁后专属 update() 心跳。

    约定：
    - 用户输入只直达蚁后，由蚁后直接回复；
    - 蚁后心跳主要负责：更新状态/读取群聊增量/维护自己的事务推进（后续扩展）。
    """

    agent = create_react_ant_agent(
        role_key="queen",
        model_bundle=model_bundle,
        toolkit=Toolkit(),
    )

    async def update(ctx: UpdateContext) -> None:
        """
        蚁后心跳（5秒级）。

        目前只做三件事：
        1）给 UI 发 heartbeat 事件（驱动状态卡计数/闪烁）；
        2）读取全员群聊 main 的增量消息（不做推送，仅读取并推进游标）；
        3）保留扩展位：扫描事务清单/更新忙闲标签/决定是否写入增量记忆等。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent
