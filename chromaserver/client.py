from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import urllib.error
from typing import Any

import yaml

from agentscope.message import Msg
from agentscope.tool import ToolResponse

from chromaserver.protocol import RpcMethod, VectorStoreSpec


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def _config_path() -> str:
    return os.path.join(_repo_root(), "configs", "vector_server.yaml")


def load_server_url() -> str:
    path = _config_path()
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return ""
    return str(data.get("url") or "").strip()


def save_server_url(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        raise ValueError("url 不能为空。")
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(yaml.safe_dump({"url": u}, allow_unicode=True, sort_keys=False))
    return path


def _join(base: str, path: str) -> str:
    b = str(base or "").rstrip("/")
    p = str(path or "").lstrip("/")
    return f"{b}/{p}"


def _encode_msg(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Msg):
        return obj.to_dict()
    if isinstance(obj, dict):
        return obj
    raise ValueError("Msg 序列化失败：类型不支持。")


def _encode_msg_or_list(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_encode_msg(x) for x in obj]
    return _encode_msg(obj)


def _decode_tool_response(data: dict[str, Any]) -> ToolResponse:
    content = list(data.get("content") or [])
    metadata = data.get("metadata", None)
    stream = bool(data.get("stream", False))
    is_last = bool(data.get("is_last", True))
    is_interrupted = bool(data.get("is_interrupted", False))
    rid = str(data.get("id") or "").strip()
    resp = ToolResponse(content=content, metadata=metadata, stream=stream, is_last=is_last, is_interrupted=is_interrupted)
    if rid:
        resp.id = rid
    return resp


class RemoteVectorStoreClient:
    def __init__(self, *, base_url: str) -> None:
        u = str(base_url or "").strip()
        if not u:
            raise ValueError("未配置向量库服务地址（configs/vector_server.yaml: url）。")
        self.base_url = u.rstrip("/")

    async def health(self) -> dict[str, Any]:
        return await self._get("/health")

    async def info(self) -> dict[str, Any]:
        return await self._get("/info")

    async def init_db(self, *, preload_system_prompts: bool = False) -> dict[str, Any]:
        return await self._post("/init", {"preload_system_prompts": bool(preload_system_prompts)})

    async def shutdown(self) -> dict[str, Any]:
        return await self._post("/shutdown", {})

    async def call(self, method: RpcMethod, spec: VectorStoreSpec, *args: Any, **kwargs: Any) -> Any:
        encoded_args: list[Any] = []
        if method == "record":
            msgs = args[0] if len(args) > 0 else []
            if not isinstance(msgs, list):
                raise ValueError("record.msgs 必须是列表。")
            encoded_args.append([_encode_msg(x) for x in msgs])
            encoded_args.extend(list(args[1:]))
        elif method == "retrieve":
            msg = args[0] if len(args) > 0 else None
            encoded_args.append(_encode_msg_or_list(msg))
            encoded_args.extend(list(args[1:]))
        else:
            encoded_args = list(args)
        payload = {
            "method": str(method),
            "spec": spec.to_dict(),
            "args": encoded_args,
            "kwargs": dict(kwargs),
        }
        data = await self._post("/call", payload)
        if method in {"record", "ensure_ready"}:
            return None
        result = data.get("result")
        if method == "retrieve":
            return str(result or "")
        if not isinstance(result, dict):
            raise ValueError("服务返回的 ToolResponse 格式不正确。")
        return _decode_tool_response(result)

    async def _get(self, path: str) -> dict[str, Any]:
        url = _join(self.base_url, path)

        def _do() -> dict[str, Any]:
            req = urllib.request.Request(url=url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                raw = e.read() or b""
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    raise ValueError(f"HTTP {int(getattr(e, 'code', 0) or 0)}: {str(e)}")
                if isinstance(data, dict) and data.get("error"):
                    raise ValueError(str(data.get("error")))
                raise
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("服务返回不是对象。")
            if not bool(data.get("ok", False)):
                raise ValueError(str(data.get("error") or "服务返回 ok=false"))
            return data

        return await asyncio.to_thread(_do)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = _join(self.base_url, path)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def _do() -> dict[str, Any]:
            req = urllib.request.Request(
                url=url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                raw = e.read() or b""
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    raise ValueError(f"HTTP {int(getattr(e, 'code', 0) or 0)}: {str(e)}")
                if isinstance(data, dict) and data.get("error"):
                    raise ValueError(str(data.get("error")))
                raise
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("服务返回不是对象。")
            if not bool(data.get("ok", False)):
                raise ValueError(str(data.get("error") or "服务返回 ok=false"))
            return data

        return await asyncio.to_thread(_do)
