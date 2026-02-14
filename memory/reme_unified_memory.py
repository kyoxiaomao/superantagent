"""
统一长期记忆：ReMe（三类）+ Chroma 向量库 + JSONL 镜像。
"""

from __future__ import annotations

import json
import hashlib
import os
import time
from typing import Any

from agentscope.memory import LongTermMemoryBase
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

import yaml

from memory.jsonl_store import JsonlItem, append_item, extract_keywords, load_items, make_file_path, now_ts
from chromaserver.protocol import collection_name_from_workspace_id, logical_workspace_id


def _msg_to_plain_text(msg: Msg | None) -> str:
    if msg is None:
        return ""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(c)


def _msgs_to_plain_text(msg: Msg | list[Msg] | None) -> str:
    if msg is None:
        return ""
    if isinstance(msg, list):
        return "\n".join([_msg_to_plain_text(m) for m in msg if m is not None]).strip()
    if isinstance(msg, Msg):
        return _msg_to_plain_text(msg).strip()
    return ""


def _tool_response_text(resp: ToolResponse | None) -> str:
    if resp is None:
        return ""
    texts: list[str] = []
    for block in resp.content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    return " ".join([t for t in texts if t]).strip()


def _norm_memory_type(memory_type: str | None, default_memory_type: str) -> str:
    t = str(memory_type or "").strip().lower()
    if t in {"personal", "task", "tool"}:
        return t
    return default_memory_type


def _workspace_id(base_workspace_id: str, memory_type: str) -> str:
    b = base_workspace_id.strip() or "default"
    logical = logical_workspace_id(base_workspace_id=b, memory_type=memory_type)
    return collection_name_from_workspace_id(logical)


class ReMeUnifiedLongTermMemory(LongTermMemoryBase):
    _vector_store_logged = False

    def __init__(
        self,
        *,
        agent_name: str,
        base_workspace_id: str,
        default_memory_type: str,
        model: Any,
        embedding_model: Any,
        vector_store_dir: str,
        jsonl_storage_dir: str,
        reme_config_path: str | None = None,
        auto_preload_system_prompts: bool = True,
    ) -> None:
        super().__init__()
        self.agent_name = agent_name
        self.base_workspace_id = base_workspace_id.strip() or "default"
        self.default_memory_type = _norm_memory_type(default_memory_type, "personal")
        self.vector_store_dir = vector_store_dir
        self.jsonl_storage_dir = jsonl_storage_dir
        self.reme_config_path = reme_config_path
        self.auto_preload_system_prompts = bool(auto_preload_system_prompts)
        self.embedding_model = embedding_model

        from agentscope.memory import ReMePersonalLongTermMemory, ReMeTaskLongTermMemory, ReMeToolLongTermMemory

        base_kwargs: dict[str, Any] = {
            "model": model,
            "embedding_model": embedding_model,
            "vector_store_dir": self.vector_store_dir,
        }
        if self.reme_config_path:
            base_kwargs["reme_config_path"] = self.reme_config_path

        self.personal = ReMePersonalLongTermMemory(
            agent_name=self.agent_name,
            user_name=_workspace_id(self.base_workspace_id, "personal"),
            **base_kwargs,
        )
        self.task = ReMeTaskLongTermMemory(
            agent_name=self.agent_name,
            user_name=_workspace_id(self.base_workspace_id, "task"),
            **base_kwargs,
        )
        self.tool = ReMeToolLongTermMemory(
            agent_name=self.agent_name,
            user_name=_workspace_id(self.base_workspace_id, "tool"),
            **base_kwargs,
        )

        self._started = False

    async def __aenter__(self) -> "ReMeUnifiedLongTermMemory":
        from chromaserver.logger import append_chromaserver_event
        from services.event_logger import log_event

        t0 = time.perf_counter()
        base_dir = os.path.dirname(os.path.dirname(__file__))
        append_chromaserver_event(
            base_dir=base_dir,
            event_type="vector_connect_start",
            payload={"agent_name": self.agent_name, "base_workspace_id": self.base_workspace_id, "vector_store_dir": self.vector_store_dir},
        )
        log_event(
            event_type="vector_store_connect_start",
            agent=str(self.agent_name),
            payload={"base_workspace_id": self.base_workspace_id, "vector_store_dir": self.vector_store_dir},
        )
        try:
            await self.personal.__aenter__()
            await self.task.__aenter__()
            await self.tool.__aenter__()
        except Exception as e:
            cost_ms = (time.perf_counter() - t0) * 1000.0
            if not type(self)._vector_store_logged:
                print(f"向量数据库连接失败：{e}", flush=True)
                type(self)._vector_store_logged = True
            append_chromaserver_event(
                base_dir=base_dir,
                event_type="vector_connect_error",
                level="ERROR",
                payload={
                    "agent_name": self.agent_name,
                    "base_workspace_id": self.base_workspace_id,
                    "vector_store_dir": self.vector_store_dir,
                    "cost_ms": float(cost_ms),
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            log_event(
                event_type="vector_store_connect_error",
                agent=str(self.agent_name),
                payload={"cost_ms": float(cost_ms), "error": f"{type(e).__name__}: {e}"},
                level="ERROR",
            )
            raise
        if not type(self)._vector_store_logged:
            print(f"向量数据库连接成功：{self.vector_store_dir}", flush=True)
            type(self)._vector_store_logged = True
        cost_ms = (time.perf_counter() - t0) * 1000.0
        append_chromaserver_event(
            base_dir=base_dir,
            event_type="vector_connect_ok",
            payload={
                "agent_name": self.agent_name,
                "base_workspace_id": self.base_workspace_id,
                "vector_store_dir": self.vector_store_dir,
                "cost_ms": float(cost_ms),
            },
        )
        log_event(
            event_type="vector_store_connect_ok",
            agent=str(self.agent_name),
            payload={"cost_ms": float(cost_ms), "vector_store_dir": self.vector_store_dir},
        )
        self._started = True
        if self.auto_preload_system_prompts:
            await self._preload_system_prompts()
        return self

    async def __aexit__(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        try:
            await self.tool.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            try:
                await self.task.__aexit__(exc_type, exc_val, exc_tb)
            finally:
                await self.personal.__aexit__(exc_type, exc_val, exc_tb)
        self._started = False

    def _jsonl_path(self, memory_type: str) -> str:
        return make_file_path(self.jsonl_storage_dir, agent_name=self.agent_name, memory_type=memory_type)

    def _append_jsonl(self, *, memory_type: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        c = (content or "").strip()
        if not c:
            return
        item = JsonlItem(
            timestamp=now_ts(),
            agent_name=self.agent_name,
            memory_type=memory_type,
            content=c,
            keywords=extract_keywords(c),
            metadata=dict(metadata or {}),
        )
        append_item(self._jsonl_path(memory_type), item)

    def _logical_workspace_id(self, *, memory_type: str) -> str:
        return logical_workspace_id(base_workspace_id=self.base_workspace_id, memory_type=memory_type)

    def _collection_name(self, *, memory_type: str) -> str:
        return collection_name_from_workspace_id(self._logical_workspace_id(memory_type=memory_type))

    def _get_collection(self, *, memory_type: str):
        from chromadb import PersistentClient
        from chromadb.config import Settings

        logical = self._logical_workspace_id(memory_type=memory_type)
        name = self._collection_name(memory_type=memory_type)
        client = PersistentClient(path=self.vector_store_dir, settings=Settings(anonymized_telemetry=False))
        return client.get_or_create_collection(name=name, metadata={"workspace_id": logical})

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        payload = [str(t or "").strip() for t in texts]
        if not payload or not all(payload):
            raise ValueError("embedding 输入为空。")
        try:
            vecs = self.embedding_model(payload)
            if hasattr(vecs, "__await__"):
                vecs = await vecs
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            if "Unsupported model" in detail and "qwen3-vl-embedding" in detail:
                raise RuntimeError("embedding 模型不可用：OpenAI 兼容 embedding 服务不支持 qwen3-vl-embedding。") from e
            raise
        if not isinstance(vecs, list) and hasattr(vecs, "embeddings"):
            vecs = getattr(vecs, "embeddings")
        if not isinstance(vecs, list) or len(vecs) != len(payload):
            raise TypeError("embedding 输出格式不正确。")
        return vecs

    def _extract_memory_list(self, resp: ToolResponse) -> list[dict[str, Any]]:
        text = _tool_response_text(resp)
        if text.startswith("Error recording memory:"):
            raise RuntimeError(text)
        result = (resp.metadata or {}).get("result")
        if not isinstance(result, dict):
            raise TypeError("ReMe 返回 result 格式不正确。")
        meta = result.get("metadata")
        if not isinstance(meta, dict):
            raise TypeError("ReMe 返回 metadata 格式不正确。")
        memory_list = meta.get("memory_list")
        if not isinstance(memory_list, list):
            raise TypeError("ReMe 返回 memory_list 格式不正确。")
        out: list[dict[str, Any]] = []
        for it in memory_list:
            if isinstance(it, dict):
                out.append(it)
        return out

    def _system_prompts_path(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(base_dir, "configs", "prompts", "system_prompts.yaml")

    def _read_system_prompts(self) -> dict[str, str]:
        path = self._system_prompts_path()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("system_prompts.yaml 内容格式不正确，应为对象。")
        result: dict[str, str] = {}
        for role_key, content in data.items():
            if not isinstance(role_key, str):
                continue
            if not isinstance(content, str):
                continue
            key = role_key.strip()
            value = content.strip()
            if not key or not value:
                continue
            result[key] = value
        if not result:
            raise ValueError("system_prompts.yaml 未找到有效角色内容。")
        return result

    def _system_prompts_exists(self, *, memory_type: str, role_key: str) -> bool:
        marker = f"【系统提示】【{role_key}】"
        items = load_items(self._jsonl_path(memory_type))
        return any(marker in (it.content or "") for it in items)

    async def _preload_system_prompts(self) -> None:
        memory_type = "personal"
        prompts = self._read_system_prompts()
        for role_key, text in prompts.items():
            if self._system_prompts_exists(memory_type=memory_type, role_key=role_key):
                continue
            blocks = [x.strip() for x in (text or "").split("\n\n") if x.strip()]
            if not blocks:
                raise ValueError(f"system_prompts.yaml 角色内容为空：{role_key}")
            payloads = [f"【系统提示】【{role_key}】\n{block}" for block in blocks]
            await self.record_to_memory(thinking="system_prompts_init", content=payloads, memory_type=memory_type)

    async def record_role_cards(self, *, role_key: str, blocks: list[str], memory_type: str | None = None) -> None:
        if not self._started:
            raise RuntimeError("ReMe 上下文未初始化，请使用 async with。")
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key 不能为空。")
        mt = _norm_memory_type(memory_type, self.default_memory_type)
        payload = [str(x or "").strip() for x in (blocks or [])]
        payload = [x for x in payload if x]
        if not payload:
            raise ValueError("角色卡片内容为空。")

        col = self._get_collection(memory_type=mt)
        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for t in payload:
            digest = hashlib.sha1(f"{self.base_workspace_id}:{mt}:{rk}:{t}".encode("utf-8")).hexdigest()
            ids.append(f"role_card_{digest}")
            metadatas.append(
                {
                    "memory_type": mt,
                    "role_key": rk,
                    "source": "system_prompt_role_card",
                    "content": t,
                    "agent_name": self.agent_name,
                    "base_workspace_id": self.base_workspace_id,
                    "time_created": now_ts(),
                },
            )
        vecs = await self._embed_texts(payload)
        col.upsert(ids=ids, embeddings=vecs, documents=payload, metadatas=metadatas)

        for md in metadatas:
            self._append_jsonl(memory_type=mt, content=str(md.get("content") or ""), metadata={"source": "system_prompt_role_card", "role_key": rk})

    async def record(self, msgs: list[Msg | None], memory_type: str | None = None, score: float | None = None, **kwargs: Any) -> None:
        raise RuntimeError("严格模式下禁用 record()，请使用 record_to_memory() 并确保向量库写入成功后再落盘。")

    async def retrieve(self, msg: Msg | list[Msg] | None, memory_type: str | None = None, top_k: int | None = None, **kwargs: Any) -> str:
        if not self._started:
            raise RuntimeError("ReMe 上下文未初始化，请使用 async with。")
        mt = _norm_memory_type(memory_type, self.default_memory_type)
        k = 5 if top_k is None else int(top_k)
        query = _msgs_to_plain_text(msg)
        if not query:
            return ""
        col = self._get_collection(memory_type=mt)
        vec = (await self._embed_texts([query]))[0]
        res = col.query(query_embeddings=[vec], n_results=k, include=["metadatas"])
        metas = res.get("metadatas") if isinstance(res, dict) else None
        if not isinstance(metas, list) or not metas or not isinstance(metas[0], list):
            return ""
        chunks: list[str] = []
        for md in metas[0]:
            if not isinstance(md, dict):
                continue
            c = str(md.get("content") or "").strip()
            if c:
                chunks.append(c)
        return "\n".join(chunks).strip()

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        memory_type: str | None = None,
        score: float | None = None,
        **kwargs: Any,
    ) -> ToolResponse:
        if not self._started:
            raise RuntimeError("ReMe 上下文未初始化，请使用 async with。")
        mt = _norm_memory_type(memory_type, self.default_memory_type)
        if mt == "personal":
            resp = await self.personal.record_to_memory(thinking=thinking, content=content, **kwargs)
            memories = self._extract_memory_list(resp)
            if not memories:
                raise ValueError("memory_list 为空，已拒绝写入。")
            ids: list[str] = []
            texts: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for m in memories:
                mid = str(m.get("memory_id") or "").strip()
                when_to_use = str(m.get("when_to_use") or "").strip()
                body = m.get("content")
                mem_content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                mem_content = str(mem_content or "").strip()
                if not mid or (not when_to_use and not mem_content):
                    raise ValueError("memory_list 条目缺少关键字段。")
                ids.append(mid)
                texts.append(when_to_use or mem_content)
                metadatas.append(
                    {
                        "memory_type": mt,
                        "when_to_use": when_to_use,
                        "content": mem_content,
                        "time_created": str(m.get("time_created") or ""),
                        "time_modified": str(m.get("time_modified") or ""),
                        "author": str(m.get("author") or ""),
                    },
                )
            vecs = await self._embed_texts(texts)
            col = self._get_collection(memory_type=mt)
            col.upsert(ids=ids, embeddings=vecs, documents=texts, metadatas=metadatas)
            for md in metadatas:
                self._append_jsonl(memory_type=mt, content=str(md.get("content") or ""), metadata={"source": "record_to_memory", "thinking": thinking})
            return resp
        if mt == "task":
            s = 1.0 if score is None else float(score)
            resp = await self.task.record_to_memory(thinking=thinking, content=content, score=s, **kwargs)
            memories = self._extract_memory_list(resp)
            if not memories:
                raise ValueError("memory_list 为空，已拒绝写入。")
            ids: list[str] = []
            texts: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for m in memories:
                mid = str(m.get("memory_id") or "").strip()
                when_to_use = str(m.get("when_to_use") or "").strip()
                body = m.get("content")
                mem_content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                mem_content = str(mem_content or "").strip()
                if not mid or (not when_to_use and not mem_content):
                    raise ValueError("memory_list 条目缺少关键字段。")
                ids.append(mid)
                texts.append(when_to_use or mem_content)
                metadatas.append(
                    {
                        "memory_type": mt,
                        "when_to_use": when_to_use,
                        "content": mem_content,
                        "time_created": str(m.get("time_created") or ""),
                        "time_modified": str(m.get("time_modified") or ""),
                        "author": str(m.get("author") or ""),
                        "score": float(m.get("score") or s),
                    },
                )
            vecs = await self._embed_texts(texts)
            col = self._get_collection(memory_type=mt)
            col.upsert(ids=ids, embeddings=vecs, documents=texts, metadatas=metadatas)
            for md in metadatas:
                self._append_jsonl(
                    memory_type=mt,
                    content=str(md.get("content") or ""),
                    metadata={"source": "record_to_memory", "thinking": thinking, "score": float(md.get("score") or s)},
                )
            return resp
        if mt == "tool":
            resp = await self.tool.record_to_memory(thinking=thinking, content=content, **kwargs)
            memories = self._extract_memory_list(resp)
            if not memories:
                raise ValueError("memory_list 为空，已拒绝写入。")
            ids: list[str] = []
            texts: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for m in memories:
                mid = str(m.get("memory_id") or "").strip()
                when_to_use = str(m.get("when_to_use") or "").strip()
                body = m.get("content")
                mem_content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                mem_content = str(mem_content or "").strip()
                if not mid or (not when_to_use and not mem_content):
                    raise ValueError("memory_list 条目缺少关键字段。")
                ids.append(mid)
                texts.append(when_to_use or mem_content)
                metadatas.append(
                    {
                        "memory_type": mt,
                        "when_to_use": when_to_use,
                        "content": mem_content,
                        "time_created": str(m.get("time_created") or ""),
                        "time_modified": str(m.get("time_modified") or ""),
                        "author": str(m.get("author") or ""),
                    },
                )
            vecs = await self._embed_texts(texts)
            col = self._get_collection(memory_type=mt)
            col.upsert(ids=ids, embeddings=vecs, documents=texts, metadatas=metadatas)
            for md in metadatas:
                self._append_jsonl(memory_type=mt, content=str(md.get("content") or ""), metadata={"source": "record_to_memory", "thinking": thinking})
            return resp
        raise ValueError(f"未知 memory_type={mt}")

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        memory_type: str | None = None,
        limit: int = 5,
        top_k: int | None = None,
        **kwargs: Any,
    ) -> ToolResponse:
        if not self._started:
            raise RuntimeError("ReMe 上下文未初始化，请使用 async with。")
        mt = _norm_memory_type(memory_type, self.default_memory_type)
        k = int(top_k) if top_k is not None else int(limit)
        keys = [str(x or "").strip() for x in keywords if str(x or "").strip()]
        if not keys:
            return ToolResponse(content=[TextBlock(type="text", text="未检索：关键词为空。")])
        col = self._get_collection(memory_type=mt)
        vec = (await self._embed_texts([" ".join(keys)]))[0]
        res = col.query(query_embeddings=[vec], n_results=k, include=["metadatas"])
        metas = res.get("metadatas") if isinstance(res, dict) else None
        if not isinstance(metas, list) or not metas or not isinstance(metas[0], list) or not metas[0]:
            return ToolResponse(content=[TextBlock(type="text", text="未找到匹配的长期记忆。")], metadata={"items": []})
        chunks: list[str] = []
        for md in metas[0]:
            if not isinstance(md, dict):
                continue
            c = str(md.get("content") or "").strip()
            if c:
                chunks.append(c)
        if not chunks:
            return ToolResponse(content=[TextBlock(type="text", text="未找到匹配的长期记忆。")], metadata={"items": []})
        return ToolResponse(content=[TextBlock(type="text", text="\n".join(chunks).strip())])
