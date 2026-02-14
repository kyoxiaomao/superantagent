"""
兵蚁智能体工厂与技能工件工具。

兵蚁对外提供“保存技能脚本/保存技能文档/列出技能工件”等工具函数，
为动态技能闭环（缺工具 → 生成脚本 → 工蚁加载）提供落地能力。
"""

from __future__ import annotations

from agentscope.tool import Toolkit

from agents.base import create_react_ant_agent
from services import ModelBundle
from services.skill_loader import list_skill_artifacts, safe_write_skill_doc, safe_write_skill_file
from services.agent_update_scheduler import UpdateContext, read_chat_delta


def save_skill_script(role_key: str, file_name: str, python_code: str) -> str:
    """保存技能脚本到目标 Agent 的个人目录 skills/<skill_key>/skill.py。

    参数:
        role_key: 目标角色标识（如 worker_reed、worker_nova）
        file_name: 文件名（必须以 .py 结尾，不允许带路径）
        python_code: 完整python源码，必须包含 register(toolkit: Toolkit) 以注册工具

    返回:
        实际保存路径
    """
    return safe_write_skill_file(role_key=role_key, file_name=file_name, content=python_code)


def save_skill_doc(role_key: str, file_name: str, markdown: str) -> str:
    """保存技能使用文档到目标 Agent 的个人目录 skills/<skill_key>/skill.md。

    参数:
        role_key: 目标角色标识（如 worker_reed、worker_nova）
        file_name: 文件名（必须以 .md 结尾，不允许带路径）
        markdown: 文档内容（建议包含工具名、参数说明、调用示例、注意事项）

    返回:
        实际保存路径
    """
    return safe_write_skill_doc(role_key=role_key, file_name=file_name, content=markdown)


def list_skills(role_key: str) -> dict[str, list[str]]:
    """列出某个角色已存在的技能脚本与文档。"""
    return list_skill_artifacts(role_key=role_key)


def create_soldier_agent(model_bundle: ModelBundle) -> object:
    toolkit = Toolkit()
    toolkit.register_tool_function(save_skill_script)
    toolkit.register_tool_function(save_skill_doc)
    toolkit.register_tool_function(list_skills)
    agent = create_react_ant_agent(
        role_key="soldier_ares",
        model_bundle=model_bundle,
        toolkit=toolkit,
    )

    async def update(ctx: UpdateContext) -> None:
        """
        兵蚁心跳（5秒级）。

        兵蚁后续的常见职责包括：安全检查、技能/工具工件管理、自主风险巡检等；
        当前先统一实现基础心跳：UI 心跳 + 群聊增量读取。
        """

        await ctx.heartbeat_center.report_tick(role_key=ctx.role_key, agent_name=ctx.agent_name, busy=ctx.local_state.busy)
        if ctx.runtime is not None:
            await ctx.runtime.ui_event("heartbeat", agent_name=ctx.agent_name, role_key=ctx.role_key, tick=ctx.tick_count)
        _ = await read_chat_delta(ctx=ctx, chat_id="main")

    setattr(agent, "update", update)
    return agent
