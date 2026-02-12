"""
兵蚁模块导出。

对外提供 `create_soldier_agent()` 创建入口。
"""

from agents.soldier.agent import create_soldier_agent

__all__ = ["create_soldier_agent"]
