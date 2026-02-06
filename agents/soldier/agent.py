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


def save_skill_script(file_name: str, python_code: str) -> str:
    """保存技能脚本到 agents/skills/。

    参数:
        file_name: 文件名（必须以 .py 结尾，不允许带路径）
        python_code: 完整python源码，必须包含 register(toolkit: Toolkit) 以注册工具

    返回:
        实际保存路径
    """
    return safe_write_skill_file(file_name=file_name, content=python_code)


def save_skill_doc(file_name: str, markdown: str) -> str:
    """保存技能使用文档到 agents/skills/。

    参数:
        file_name: 文件名（必须以 .md 结尾，不允许带路径）
        markdown: 文档内容（建议包含工具名、参数说明、调用示例、注意事项）

    返回:
        实际保存路径
    """
    return safe_write_skill_doc(file_name=file_name, content=markdown)


def list_skills() -> dict[str, list[str]]:
    """列出当前已存在的技能脚本与文档。"""
    return list_skill_artifacts()


def create_soldier_agent(model_bundle: ModelBundle) -> object:
    toolkit = Toolkit()
    toolkit.register_tool_function(save_skill_script)
    toolkit.register_tool_function(save_skill_doc)
    toolkit.register_tool_function(list_skills)
    return create_react_ant_agent(
        role_key="soldier",
        model_bundle=model_bundle,
        toolkit=toolkit,
    )

