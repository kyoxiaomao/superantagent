"""
工蚁_诺瓦智能体工厂。

在创建时加载该 Agent 个人目录 `skills/` 下的动态技能，并提供 reload/list 工具便于运行时更新技能。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.skill_loader import list_skill_artifacts, load_skills
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def create_browser_worker_agent(model_bundle: ModelBundle) -> object:
    toolkit = Toolkit()
    load_skills(toolkit, role_key="worker_nova")

    def reload_skills() -> list[str]:
        return load_skills(toolkit, role_key="worker_nova")

    def list_skills() -> dict[str, list[str]]:
        return list_skill_artifacts(role_key="worker_nova")

    toolkit.register_tool_function(reload_skills)
    toolkit.register_tool_function(list_skills)

    agent = create_react_ant_agent(
        role_key="worker_nova",
        model_bundle=model_bundle,
        toolkit=toolkit,
    )

    async def update(ctx: UpdateContext) -> None:
        """
        工蚁_诺瓦心跳（5秒级）。

        工蚁_诺瓦未来常见扩展：监控浏览器/搜索队列、群聊内检索请求识别、工具可用性巡检等；
        当前先实现基础心跳：UI 心跳 + 群聊增量读取。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent
