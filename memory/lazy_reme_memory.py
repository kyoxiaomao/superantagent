from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentscope.memory import LongTermMemoryBase
from agentscope.message import Msg
from agentscope.tool import ToolResponse

from chromaserver.client import RemoteVectorStoreClient, load_server_url
from chromaserver.protocol import VectorStoreSpec


@dataclass(frozen=True)
class LazyReMeConfig:
    agent_name: str
    base_workspace_id: str
    default_memory_type: str
    vector_store_dir: str
    jsonl_storage_dir: str
    reme_config_path: str | None

    def to_spec(self) -> VectorStoreSpec:
        return VectorStoreSpec(
            agent_name=self.agent_name,
            base_workspace_id=self.base_workspace_id,
            default_memory_type=self.default_memory_type,
            vector_store_dir=self.vector_store_dir,
            jsonl_storage_dir=self.jsonl_storage_dir,
            reme_config_path=self.reme_config_path,
        )


class LazyReMeUnifiedLongTermMemory(LongTermMemoryBase):
    def __init__(self, *, cfg: LazyReMeConfig) -> None:
        super().__init__()
        self._cfg = cfg
        self._spec = cfg.to_spec()
        self._client = RemoteVectorStoreClient(base_url=load_server_url())

    async def ensure_ready(self) -> None:
        await self._client.call("ensure_ready", self._spec)

    async def __aenter__(self) -> "LazyReMeUnifiedLongTermMemory":
        await self.ensure_ready()
        return self

    async def __aexit__(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        _ = exc_type, exc_val, exc_tb

    async def record(self, msgs: list[Msg | None], memory_type: str | None = None, score: float | None = None, **kwargs: Any) -> None:
        await self._client.call("record", self._spec, msgs, memory_type, score, **kwargs)

    async def retrieve(self, msg: Msg | list[Msg] | None, memory_type: str | None = None, top_k: int | None = None, **kwargs: Any) -> str:
        return await self._client.call("retrieve", self._spec, msg, memory_type, top_k, **kwargs)

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        memory_type: str | None = None,
        score: float | None = None,
        **kwargs: Any,
    ) -> ToolResponse:
        return await self._client.call("record_to_memory", self._spec, thinking, content, memory_type, score, **kwargs)

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        memory_type: str | None = None,
        limit: int = 5,
        top_k: int | None = None,
        **kwargs: Any,
    ) -> ToolResponse:
        return await self._client.call("retrieve_from_memory", self._spec, keywords, memory_type, limit, top_k, **kwargs)
