"""
记忆组合构建。

为每个智能体组装短期记忆（InMemory）与长期记忆（JSONL 文件），并配置触发式压缩策略。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.token import CharTokenCounter

from memory.long_term_memory import FileLongTermMemory


@dataclass(frozen=True)
class MemoryBundle:
    short_term: InMemoryMemory
    long_term: FileLongTermMemory
    compression_config: ReActAgent.CompressionConfig


def build_memory_bundle(*, agent_name: str, storage_dir: str | None = None) -> MemoryBundle:
    base_dir = storage_dir or os.path.join(os.path.dirname(__file__), "storage")
    long_term = FileLongTermMemory(agent_name=agent_name, storage_dir=base_dir)
    short_term = InMemoryMemory()

    compression_config = ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=CharTokenCounter(),
        trigger_threshold=12000,
        keep_recent=6,
    )

    return MemoryBundle(
        short_term=short_term,
        long_term=long_term,
        compression_config=compression_config,
    )

