from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine

import yaml

from chromaserver.logger import append_chromaserver_event
from chromaserver.protocol import RpcMethod, VectorStoreSpec, collection_name_from_workspace_id, logical_workspace_id


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass(frozen=True)
class ServerConfig:
    base_dir: str
    vector_store_dir: str
    jsonl_storage_dir: str
    reme_config_path: str | None
    user_id: str

    @classmethod
    def from_repo(cls, *, base_dir: str) -> "ServerConfig":
        base = os.path.abspath(base_dir)
        user_id = str(os.getenv("ANT_USER_ID") or "local_user").strip() or "local_user"
        default_vector_dir = os.path.join(base, "chromaserver", "data", "chroma_vector_store")
        default_jsonl_dir = os.path.join(base, "chromaserver", "data", "jsonl_storage")
        vector_store_dir = str(os.getenv("ANT_VECTOR_STORE_DIR") or default_vector_dir).strip()
        jsonl_storage_dir = str(os.getenv("ANT_JSONL_STORAGE_DIR") or default_jsonl_dir).strip()
        reme_cfg = os.path.join(base, "configs", "reme_config.yaml")
        reme_config_path = reme_cfg if os.path.exists(reme_cfg) else None
        os.makedirs(vector_store_dir, exist_ok=True)
        os.makedirs(jsonl_storage_dir, exist_ok=True)
        return cls(
            base_dir=base,
            vector_store_dir=vector_store_dir,
            jsonl_storage_dir=jsonl_storage_dir,
            reme_config_path=reme_config_path,
            user_id=user_id,
        )


class VectorStoreWorker:
    def __init__(self, *, cfg: ServerConfig) -> None:
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._memories: dict[VectorStoreSpec, Any] = {}
        self._started = threading.Event()

    @staticmethod
    def _norm_memory_type(raw: Any, *, default: str) -> str:
        v = str(raw or "").strip().lower()
        if v in {"personal", "task", "tool"}:
            return v
        d = str(default or "").strip().lower()
        return d if d in {"personal", "task", "tool"} else "personal"

    def _collection_count(self, *, collection_name: str) -> int:
        name = str(collection_name or "").strip()
        if not name:
            return -1
        store_dir = str(self._cfg.vector_store_dir or "").strip()
        if not store_dir:
            return -1
        try:
            from chromadb import PersistentClient
            from chromadb.config import Settings

            client = PersistentClient(path=store_dir, settings=Settings(anonymized_telemetry=False))
            col = client.get_collection(name=name)
            return int(col.count())
        except Exception:
            return -1

    def start(self) -> None:
        th = self._thread
        if th is not None and th.is_alive():
            return
        self._started.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ChromaServerWorker")
        self._thread.start()
        ok = self._started.wait(timeout=10)
        if not ok:
            raise RuntimeError("向量库服务工作线程启动超时。")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._started.set()
        loop.run_forever()

    async def close(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            coro = self._close_all()
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=8.0)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        th = self._thread
        if th is not None:
            await asyncio.to_thread(th.join, 3)

    async def call(self, method: RpcMethod, spec: VectorStoreSpec, *args: Any, **kwargs: Any) -> Any:
        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("向量库服务 loop 未就绪。")

        async def _op() -> Any:
            if method == "ensure_ready":
                await self._ensure_ready(spec)
                return None
            mem = await self._ensure_ready(spec)
            fn = getattr(mem, method, None)
            if fn is None:
                raise AttributeError(f"长期记忆不支持方法：{method}")
            if method not in {"record", "record_to_memory"}:
                return await fn(*args, **kwargs)

            normalized = self._normalize_spec(spec)
            if method == "record":
                mt_raw = args[1] if len(args) > 1 else None
            else:
                mt_raw = args[2] if len(args) > 2 else None
            mt = self._norm_memory_type(mt_raw, default=normalized.default_memory_type)
            wid = logical_workspace_id(base_workspace_id=normalized.base_workspace_id, memory_type=mt)
            cname = collection_name_from_workspace_id(wid)
            before = self._collection_count(collection_name=cname)
            t0 = _now_ms()
            try:
                out = await fn(*args, **kwargs)
            except Exception as e:
                after = self._collection_count(collection_name=cname)
                append_chromaserver_event(
                    base_dir=self._cfg.base_dir,
                    event_type="memory_record",
                    level="ERROR",
                    payload={
                        "method": str(method),
                        "agent_name": normalized.agent_name,
                        "base_workspace_id": normalized.base_workspace_id,
                        "memory_type": mt,
                        "workspace_id": wid,
                        "collection": cname,
                        "count_before": int(before),
                        "count_after": int(after),
                        "delta": int(after) - int(before) if before >= 0 and after >= 0 else None,
                        "cost_ms": float(_now_ms() - t0),
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                raise
            after = self._collection_count(collection_name=cname)
            append_chromaserver_event(
                base_dir=self._cfg.base_dir,
                event_type="memory_record",
                payload={
                    "method": str(method),
                    "agent_name": normalized.agent_name,
                    "base_workspace_id": normalized.base_workspace_id,
                    "memory_type": mt,
                    "workspace_id": wid,
                    "collection": cname,
                    "count_before": int(before),
                    "count_after": int(after),
                    "delta": int(after) - int(before) if before >= 0 and after >= 0 else None,
                    "cost_ms": float(_now_ms() - t0),
                },
            )
            return out

        fut = asyncio.run_coroutine_threadsafe(_op(), loop)
        return await asyncio.wrap_future(fut)

    async def bootstrap_role_cards(self, *, spec: VectorStoreSpec, role_key: str, blocks: list[str], memory_type: str) -> None:
        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("向量库服务 loop 未就绪。")

        async def _op() -> None:
            mem = await self._ensure_ready(spec)
            fn = getattr(mem, "record_role_cards", None)
            if fn is None:
                raise AttributeError("长期记忆不支持 record_role_cards。")
            normalized = self._normalize_spec(spec)
            mt = self._norm_memory_type(memory_type, default=normalized.default_memory_type)
            wid = logical_workspace_id(base_workspace_id=normalized.base_workspace_id, memory_type=mt)
            cname = collection_name_from_workspace_id(wid)
            before = self._collection_count(collection_name=cname)
            t0 = _now_ms()
            try:
                await fn(role_key=str(role_key or "").strip(), blocks=list(blocks or []), memory_type=mt)
            except Exception as e:
                after = self._collection_count(collection_name=cname)
                append_chromaserver_event(
                    base_dir=self._cfg.base_dir,
                    event_type="role_card_record",
                    level="ERROR",
                    payload={
                        "role_key": str(role_key),
                        "agent_name": normalized.agent_name,
                        "base_workspace_id": normalized.base_workspace_id,
                        "memory_type": mt,
                        "workspace_id": wid,
                        "collection": cname,
                        "count_before": int(before),
                        "count_after": int(after),
                        "delta": int(after) - int(before) if before >= 0 and after >= 0 else None,
                        "cost_ms": float(_now_ms() - t0),
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
                raise
            after = self._collection_count(collection_name=cname)
            append_chromaserver_event(
                base_dir=self._cfg.base_dir,
                event_type="role_card_record",
                payload={
                    "role_key": str(role_key),
                    "agent_name": normalized.agent_name,
                    "base_workspace_id": normalized.base_workspace_id,
                    "memory_type": mt,
                    "workspace_id": wid,
                    "collection": cname,
                    "count_before": int(before),
                    "count_after": int(after),
                    "delta": int(after) - int(before) if before >= 0 and after >= 0 else None,
                    "cost_ms": float(_now_ms() - t0),
                },
            )

        fut = asyncio.run_coroutine_threadsafe(_op(), loop)
        await asyncio.wrap_future(fut)

    async def _ensure_ready(self, spec: VectorStoreSpec) -> Any:
        normalized = self._normalize_spec(spec)
        with self._lock:
            existing = self._memories.get(normalized)
        if existing is not None:
            return existing

        mem = _build_reme_memory(normalized)
        await mem.__aenter__()
        self._ensure_chroma_collections(normalized)
        with self._lock:
            self._memories[normalized] = mem
        return mem

    async def _close_all(self) -> None:
        with self._lock:
            items = list(self._memories.items())
            self._memories = {}
        for _, mem in items:
            await mem.__aexit__(None, None, None)

    def _normalize_spec(self, spec: VectorStoreSpec) -> VectorStoreSpec:
        agent_name = str(spec.agent_name or "").strip()
        if not agent_name:
            raise ValueError("spec.agent_name 不能为空。")
        base_workspace_id = str(spec.base_workspace_id or "").strip()
        if not base_workspace_id:
            raise ValueError("spec.base_workspace_id 不能为空。")
        default_memory_type = str(spec.default_memory_type or "").strip().lower()
        if default_memory_type not in {"personal", "task", "tool"}:
            raise ValueError(f"spec.default_memory_type 不正确：{default_memory_type}")

        return VectorStoreSpec(
            agent_name=agent_name,
            base_workspace_id=base_workspace_id,
            default_memory_type=default_memory_type,
            vector_store_dir=self._cfg.vector_store_dir,
            jsonl_storage_dir=self._cfg.jsonl_storage_dir,
            reme_config_path=self._cfg.reme_config_path,
        )

    def _ensure_chroma_collections(self, spec: VectorStoreSpec) -> None:
        store_dir = str(self._cfg.vector_store_dir or "").strip()
        if not store_dir:
            return
        try:
            from chromadb import PersistentClient
            from chromadb.config import Settings

            client = PersistentClient(path=store_dir, settings=Settings(anonymized_telemetry=False))
            base_workspace_id = str(spec.base_workspace_id or "").strip()
            for memory_type in ("personal", "task", "tool"):
                wid = logical_workspace_id(base_workspace_id=base_workspace_id, memory_type=memory_type)
                name = collection_name_from_workspace_id(wid)
                client.get_or_create_collection(name=name, metadata={"workspace_id": wid})
        except Exception as e:
            try:
                append_chromaserver_event(
                    base_dir=self._cfg.base_dir,
                    event_type="ensure_collection_error",
                    level="ERROR",
                    payload={"error": f"{type(e).__name__}: {e}"},
                )
            except Exception:
                pass


class VectorDbAdmin:
    def __init__(self, *, cfg: ServerConfig, worker: VectorStoreWorker) -> None:
        self._cfg = cfg
        self._worker = worker

    def init_db(self, *, preload_system_prompts: bool = False) -> dict[str, Any]:
        started_ms = _now_ms()
        append_chromaserver_event(
            base_dir=self._cfg.base_dir,
            event_type="init_start",
            payload={"preload_system_prompts": bool(preload_system_prompts)},
        )
        agents = _load_agent_infos(self._cfg.base_dir)
        created: list[str] = []
        failed: list[dict[str, Any]] = []
        for role_key, agent_name in agents:
            t0 = _now_ms()
            base_workspace_id = f"{self._cfg.user_id}:{agent_name}"
            default_memory_type = _default_memory_type_by_role(role_key)
            spec = VectorStoreSpec(
                agent_name=agent_name,
                base_workspace_id=base_workspace_id,
                default_memory_type=default_memory_type,
                vector_store_dir=self._cfg.vector_store_dir,
                jsonl_storage_dir=self._cfg.jsonl_storage_dir,
                reme_config_path=self._cfg.reme_config_path,
            )
            append_chromaserver_event(
                base_dir=self._cfg.base_dir,
                event_type="init_agent_start",
                payload={"role_key": role_key, "agent_name": agent_name, "workspace_id": base_workspace_id, "default_memory_type": default_memory_type},
            )
            stage = "ensure_ready"
            try:
                asyncio.run(self._worker.call("ensure_ready", spec))
                append_chromaserver_event(
                    base_dir=self._cfg.base_dir,
                    event_type="init_agent_ready_ok",
                    payload={"role_key": role_key, "agent_name": agent_name, "workspace_id": base_workspace_id, "cost_ms": _now_ms() - t0},
                )
                created.append(base_workspace_id)
                if preload_system_prompts:
                    stage = "role_card"
                    append_chromaserver_event(
                        base_dir=self._cfg.base_dir,
                        event_type="init_agent_role_card_start",
                        payload={"role_key": role_key, "agent_name": agent_name, "workspace_id": base_workspace_id},
                    )
                    blocks = _extract_role_card_blocks(self._cfg.base_dir, role_key)
                    asyncio.run(
                        self._worker.bootstrap_role_cards(
                            spec=spec,
                            role_key=role_key,
                            blocks=blocks,
                            memory_type=spec.default_memory_type,
                        )
                    )
                    append_chromaserver_event(
                        base_dir=self._cfg.base_dir,
                        event_type="init_agent_role_card_ok",
                        payload={"role_key": role_key, "agent_name": agent_name, "workspace_id": base_workspace_id, "cost_ms": _now_ms() - t0},
                    )
            except Exception as e:
                append_chromaserver_event(
                    base_dir=self._cfg.base_dir,
                    event_type="init_agent_error",
                    level="ERROR",
                    payload={
                        "role_key": role_key,
                        "agent_name": agent_name,
                        "workspace_id": base_workspace_id,
                        "stage": stage,
                        "error": f"{type(e).__name__}: {e}",
                        "cost_ms": _now_ms() - t0,
                    },
                )
                failed.append(
                    {
                        "role_key": str(role_key),
                        "agent_name": str(agent_name),
                        "workspace_id": str(base_workspace_id),
                        "stage": str(stage),
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
                continue
        out = {"ok": True, "created": created, "failed": failed, "cost_ms": _now_ms() - started_ms, "ts": _timestamp()}
        append_chromaserver_event(base_dir=self._cfg.base_dir, event_type="init_ok", payload=out)
        return out

    def get_info(self) -> dict[str, Any]:
        store_dir = self._cfg.vector_store_dir
        from chromadb import PersistentClient
        from chromadb.config import Settings

        client = PersistentClient(path=store_dir, settings=Settings(anonymized_telemetry=False))
        rows: list[dict[str, Any]] = []
        for it in client.list_collections():
            name = str(getattr(it, "name", "") or "").strip()
            if not name:
                continue
            wid = ""
            count = -1
            try:
                col = client.get_collection(name=name)
                count = int(col.count())
                meta = getattr(col, "metadata", None)
                if isinstance(meta, dict):
                    wid = str(meta.get("workspace_id") or "").strip()
            except Exception:
                wid = ""
                count = -1
            rows.append({"workspace_id": wid, "collection": name, "count": int(count)})

        rows.sort(key=lambda r: (0 if str(r.get("workspace_id") or "").strip() else 1, str(r.get("workspace_id") or ""), str(r.get("collection") or "")))
        return {
            "ok": True,
            "vector_store_dir": self._cfg.vector_store_dir,
            "jsonl_storage_dir": self._cfg.jsonl_storage_dir,
            "reme_config_path": self._cfg.reme_config_path,
            "collections": rows,
            "ts": _timestamp(),
        }


def _safe_rmtree(path: str) -> None:
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return
    shutil.rmtree(p)


def _default_memory_type_by_role(role_key: str) -> str:
    key = str(role_key or "").strip().lower()
    if key == "queen_sera":
        return "personal"
    if key == "king_tru":
        return "task"
    return "tool"


def _load_agent_infos(base_dir: str) -> list[tuple[str, str]]:
    path = os.path.join(base_dir, "configs", "agent_configs.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("agent_configs.yaml 内容格式不正确，应为对象。")
    out: list[tuple[str, str]] = []
    for role_key, cfg in data.items():
        if not isinstance(role_key, str):
            continue
        if not isinstance(cfg, dict):
            continue
        name = str(cfg.get("name") or "").strip()
        if not name:
            continue
        out.append((role_key.strip(), name))
    if not out:
        raise ValueError("agent_configs.yaml 未找到有效角色。")
    return out


def _split_system_prompts(base_dir: str, role_key: str) -> list[str]:
    path = os.path.join(base_dir, "configs", "prompts", "system_prompts.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("system_prompts.yaml 内容格式不正确，应为对象。")
    text = data.get(role_key)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"system_prompts.yaml 缺少角色：{role_key}")
    blocks = [x.strip() for x in text.split("\n\n") if x.strip()]
    return [f"【系统提示】【{role_key}】\n{b}" for b in blocks]

def _extract_role_card_blocks(base_dir: str, role_key: str) -> list[str]:
    blocks = _split_system_prompts(base_dir, role_key)
    picked: list[str] = []
    for b in blocks:
        body = b
        if b.startswith("【系统提示】【"):
            parts = b.split("\n", 1)
            body = parts[1] if len(parts) > 1 else ""
        head = body.lstrip()
        if head.startswith("# 角色定位") or head.startswith("# 禁止行为"):
            picked.append(b)
    if not picked:
        raise ValueError(f"system_prompts.yaml 角色卡片提取失败（缺少 # 角色定位 或 # 禁止行为）：{role_key}")
    return picked


def _build_reme_memory(spec: VectorStoreSpec) -> Any:
    from agentscope.model import DashScopeChatModel

    from memory.reme_unified_memory import ReMeUnifiedLongTermMemory
    from models.model_manager import load_embedding_bundle

    dashscope_api_key = os.getenv("DASHSCOPE_API_KEY") or ""
    if not dashscope_api_key:
        raise ValueError("未配置 DASHSCOPE_API_KEY（请在 .env 中设置）")
    llm_model_name = os.getenv("QWEN_MODEL") or "qwen3-max"
    project_root = os.path.dirname(os.path.dirname(__file__))
    config_path = os.path.join(project_root, "configs", "model_configs.yaml")

    model = DashScopeChatModel(
        model_name=llm_model_name,
        api_key=dashscope_api_key,
        stream=False,
    )
    embedding_bundle = load_embedding_bundle(config_path)
    embedding_model = embedding_bundle.model
    return ReMeUnifiedLongTermMemory(
        agent_name=spec.agent_name,
        base_workspace_id=spec.base_workspace_id,
        default_memory_type=spec.default_memory_type,
        model=model,
        embedding_model=embedding_model,
        vector_store_dir=spec.vector_store_dir,
        jsonl_storage_dir=spec.jsonl_storage_dir,
        reme_config_path=spec.reme_config_path,
        auto_preload_system_prompts=False,
    )
