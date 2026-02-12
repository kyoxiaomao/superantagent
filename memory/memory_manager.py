"""
记忆组合构建。

为每个智能体组装短期记忆（InMemory）与长期记忆（ReMe + Chroma + JSONL 回退），并配置触发式压缩策略。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.token import CharTokenCounter

from memory.lazy_reme_memory import LazyReMeConfig, LazyReMeUnifiedLongTermMemory


@dataclass(frozen=True)
class MemoryBundle:
    short_term: InMemoryMemory
    long_term: LazyReMeUnifiedLongTermMemory
    compression_config: ReActAgent.CompressionConfig
    default_memory_type: str


def _default_memory_type_by_role(role_key: str) -> str:
    k = str(role_key or "").strip().lower()
    if k == "queen":
        return "personal"
    if k == "king":
        return "task"
    return "tool"


def build_memory_bundle(
    *,
    role_key: str,
    agent_name: str,
    user_id: str | None = None,
    jsonl_storage_dir: str | None = None,
    vector_store_dir: str | None = None,
    reme_config_path: str | None = None,
) -> MemoryBundle:
    root = os.path.dirname(os.path.dirname(__file__))
    jsonl_dir = jsonl_storage_dir or os.path.join(root, "chromaserver", "data", "jsonl_storage")
    vector_dir = vector_store_dir or os.path.join(root, "chromaserver", "data", "chroma_vector_store")
    default_memory_type = _default_memory_type_by_role(role_key)

    uid = str(user_id or os.getenv("ANT_USER_ID") or "local_user").strip() or "local_user"
    base_workspace_id = f"{uid}:{agent_name}"

    cfg_path = reme_config_path or os.path.join(root, "configs", "reme_config.yaml")
    long_term = LazyReMeUnifiedLongTermMemory(
        cfg=LazyReMeConfig(
            agent_name=agent_name,
            base_workspace_id=base_workspace_id,
            default_memory_type=default_memory_type,
            vector_store_dir=vector_dir,
            jsonl_storage_dir=jsonl_dir,
            reme_config_path=cfg_path if os.path.exists(cfg_path) else None,
        )
    )
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
        default_memory_type=default_memory_type,
    )
