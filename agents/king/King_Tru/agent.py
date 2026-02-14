"""
蚁王_特鲁智能体工厂。

创建“蚁王_特鲁” ReActAgent：负责面向用户对话与任务调度（输出调度 JSON 供运行时解析）。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def create_king_agent(model_bundle: ModelBundle) -> object:
    """
    创建蚁王_特鲁智能体，并挂载蚁王_特鲁专属 update() 心跳。

    约定：
    - 用户输入不再由蚁王_特鲁直接承接（改为直达蚁后_瑟拉）；
    - 若需要把信息转发到某个群聊，或按任务创建群聊并群发，由蚁王_特鲁在 update() 内决定并执行。
    """

    agent = create_react_ant_agent(
        role_key="king_tru",
        model_bundle=model_bundle,
        toolkit=Toolkit(),
    )

    async def update(ctx: UpdateContext) -> None:
        """
        蚁王_特鲁心跳（5秒级）。

        当前版本只做基础设施动作：
        - 发送 heartbeat UI 事件；
        - 自主读取群聊增量（后续可据此做“群发/调度/话题管理”等策略）。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent

