"""
工蚁模块导出。

对外提供工蚁_诺瓦、工蚁_里德、工蚁_莱特的创建入口。
"""

from agents.worker.Worker_Nova.agent import create_browser_worker_agent
from agents.worker.Worker_Reed.agent import create_doc_worker_agent
from agents.worker.Worker_Light.agent import create_emotion_worker_agent

__all__ = [
    "create_browser_worker_agent",
    "create_doc_worker_agent",
    "create_emotion_worker_agent",
]
