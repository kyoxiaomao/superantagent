from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal


JsonObject = dict[str, Any]
JsonValue = Any


def logical_workspace_id(*, base_workspace_id: str, memory_type: str) -> str:
    b = str(base_workspace_id or "").strip()
    t = str(memory_type or "").strip().lower()
    return f"{b}:{t}"


def collection_name_from_workspace_id(workspace_id: str) -> str:
    raw = str(workspace_id or "").strip()
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"ws_{h}"


@dataclass(frozen=True)
class VectorStoreSpec:
    agent_name: str
    base_workspace_id: str
    default_memory_type: str
    vector_store_dir: str
    jsonl_storage_dir: str
    reme_config_path: str | None

    def to_dict(self) -> JsonObject:
        return {
            "agent_name": self.agent_name,
            "base_workspace_id": self.base_workspace_id,
            "default_memory_type": self.default_memory_type,
            "vector_store_dir": self.vector_store_dir,
            "jsonl_storage_dir": self.jsonl_storage_dir,
            "reme_config_path": self.reme_config_path,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "VectorStoreSpec":
        return cls(
            agent_name=str(data.get("agent_name") or ""),
            base_workspace_id=str(data.get("base_workspace_id") or ""),
            default_memory_type=str(data.get("default_memory_type") or ""),
            vector_store_dir=str(data.get("vector_store_dir") or ""),
            jsonl_storage_dir=str(data.get("jsonl_storage_dir") or ""),
            reme_config_path=(str(data.get("reme_config_path")) if data.get("reme_config_path") is not None else None),
        )


RpcMethod = Literal[
    "ensure_ready",
    "record",
    "retrieve",
    "record_to_memory",
    "retrieve_from_memory",
]
