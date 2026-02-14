"""
工蚁_里德智能体工厂。

在创建时扫描并加载该 Agent 的个人装备库（`skills/` + `tools/`），并提供 reload/list 工具便于运行时更新装备。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.skill_loader import list_skill_artifacts, load_utils
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def create_doc_worker_agent(model_bundle: ModelBundle) -> object:
    toolkit = Toolkit()
    load_utils(toolkit, role_key="worker_reed")

    def reload_skills() -> dict[str, list[str]]:
        return load_utils(toolkit, role_key="worker_reed")

    def list_skills() -> dict[str, list[str]]:
        return list_skill_artifacts(role_key="worker_reed")

    toolkit.register_tool_function(reload_skills)
    toolkit.register_tool_function(list_skills)

    agent = create_react_ant_agent(
        role_key="worker_reed",
        model_bundle=model_bundle,
        toolkit=toolkit,
    )

    async def update(ctx: UpdateContext) -> None:
        """
        工蚁_里德心跳（5秒级）。

        工蚁_里德未来常见扩展：文档生成队列、任务进度汇总、群聊需求识别与结构化等；
        当前先实现基础心跳：UI 心跳 + 群聊增量读取。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent
