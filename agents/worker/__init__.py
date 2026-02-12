"""
工蚁模块导出。

对外提供浏览器工蚁、文档工蚁、情感工蚁的创建入口。
"""

from agents.worker.browser_worker import create_browser_worker_agent
from agents.worker.doc_worker import create_doc_worker_agent
from agents.worker.emotion_worker import create_emotion_worker_agent

__all__ = [
    "create_browser_worker_agent",
    "create_doc_worker_agent",
    "create_emotion_worker_agent",
]
