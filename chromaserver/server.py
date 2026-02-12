from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dotenv import load_dotenv
from agentscope.message import Msg

from chromaserver.logger import append_chromaserver_event, third_party_log_path
from chromaserver.protocol import RpcMethod, VectorStoreSpec
from chromaserver.service import ServerConfig, VectorDbAdmin, VectorStoreWorker


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    s = str(v if v is not None else default).strip()
    return s or default


def _setup_third_party_logs(*, base_dir: str) -> None:
    path = third_party_log_path(base_dir=base_dir)
    try:
        import logging

        handler = logging.FileHandler(path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")
        handler.setFormatter(formatter)
        for name in ["as", "reme_ai", "flowllm", "chromadb", "_reme_personal_long_term_memory"]:
            logger = logging.getLogger(name)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            for h in list(logger.handlers):
                logger.removeHandler(h)
            logger.addHandler(handler)
    except Exception:
        pass
    try:
        from loguru import logger

        logger.remove()
        logger.add(path, level="INFO", encoding="utf-8")
    except Exception:
        pass


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    n = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(n) if n > 0 else b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("请求体不是合法 JSON。")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象。")
    return data


def _write_json(handler: BaseHTTPRequestHandler, code: int, data: dict[str, Any]) -> None:
    b = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(b)))
    handler.end_headers()
    handler.wfile.write(b)


def _tool_response_to_dict(resp: Any) -> dict[str, Any]:
    if resp is None:
        return {"content": [], "metadata": None, "stream": False, "is_last": True, "is_interrupted": False, "id": ""}
    content = list(getattr(resp, "content", []) or [])
    metadata = getattr(resp, "metadata", None)
    stream = bool(getattr(resp, "stream", False))
    is_last = bool(getattr(resp, "is_last", True))
    is_interrupted = bool(getattr(resp, "is_interrupted", False))
    rid = str(getattr(resp, "id", "") or "")
    return {
        "content": content,
        "metadata": metadata,
        "stream": stream,
        "is_last": is_last,
        "is_interrupted": is_interrupted,
        "id": rid,
    }


def _decode_msg(obj: Any) -> Msg | None:
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise ValueError("Msg 必须是 dict。")
    return Msg.from_dict(obj)


def _decode_msg_or_list(obj: Any) -> Msg | list[Msg] | None:
    if obj is None:
        return None
    if isinstance(obj, list):
        out: list[Msg] = []
        for it in obj:
            m = _decode_msg(it)
            if m is not None:
                out.append(m)
        return out
    return _decode_msg(obj)


def _decode_call_payload(method: RpcMethod, args: list[Any]) -> list[Any]:
    if method == "record":
        msgs_raw = args[0] if len(args) > 0 else []
        if not isinstance(msgs_raw, list):
            raise ValueError("record.msgs 必须是列表。")
        msgs: list[Msg | None] = []
        for it in msgs_raw:
            msgs.append(_decode_msg(it))
        return [msgs] + args[1:]
    if method == "retrieve":
        msg_raw = args[0] if len(args) > 0 else None
        return [_decode_msg_or_list(msg_raw)] + args[1:]
    return args


class _State:
    def __init__(self, *, base_dir: str) -> None:
        self.base_dir = base_dir
        self.cfg = ServerConfig.from_repo(base_dir=base_dir)
        self.worker = VectorStoreWorker(cfg=self.cfg)
        self.admin = VectorDbAdmin(cfg=self.cfg, worker=self.worker)
        self.httpd: ThreadingHTTPServer | None = None


class ChromaServerHandler(BaseHTTPRequestHandler):
    server_version = "ChromaServer/1.0"

    def _state(self) -> _State:
        s = getattr(self.server, "_state", None)
        if not isinstance(s, _State):
            raise RuntimeError("服务状态未初始化。")
        return s

    def do_GET(self) -> None:
        try:
            append_chromaserver_event(base_dir=self._state().cfg.base_dir, event_type="http_request", payload={"method": "GET", "path": self.path})
        except Exception:
            pass
        try:
            if self.path == "/health":
                st = self._state()
                _write_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "base_dir": st.cfg.base_dir,
                        "vector_store_dir": st.cfg.vector_store_dir,
                        "jsonl_storage_dir": st.cfg.jsonl_storage_dir,
                        "reme_config_path": st.cfg.reme_config_path,
                        "thread_alive": bool(st.worker._thread is not None and st.worker._thread.is_alive()),
                        "pid": os.getpid(),
                        "build": "chromaserver-logs-v1",
                    },
                )
                return
            if self.path == "/info":
                st = self._state()
                _write_json(self, 200, st.admin.get_info())
                return
            _write_json(self, 404, {"ok": False, "error": f"未知路径：{self.path}"})
        except Exception as e:
            try:
                append_chromaserver_event(base_dir=self._state().cfg.base_dir, event_type="http_error", level="ERROR", payload={"path": self.path, "error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass
            _write_json(self, 500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def do_POST(self) -> None:
        try:
            append_chromaserver_event(base_dir=self._state().cfg.base_dir, event_type="http_request", payload={"method": "POST", "path": self.path})
        except Exception:
            pass
        try:
            if self.path == "/init":
                payload = _read_json(self)
                preload = bool(payload.get("preload_system_prompts", False))
                st = self._state()
                append_chromaserver_event(base_dir=st.cfg.base_dir, event_type="http_init", payload={"preload_system_prompts": preload})
                out = st.admin.init_db(preload_system_prompts=preload)
                _write_json(self, 200, out)
                return
            if self.path == "/call":
                payload = _read_json(self)
                method = str(payload.get("method") or "").strip()
                if method not in {"ensure_ready", "record", "retrieve", "record_to_memory", "retrieve_from_memory"}:
                    raise ValueError(f"method 不支持：{method}")
                spec_raw = payload.get("spec")
                if not isinstance(spec_raw, dict):
                    raise ValueError("spec 必须是对象。")
                spec = VectorStoreSpec.from_dict(spec_raw)
                args = payload.get("args") or []
                kwargs = payload.get("kwargs") or {}
                if not isinstance(args, list):
                    raise ValueError("args 必须是列表。")
                if not isinstance(kwargs, dict):
                    raise ValueError("kwargs 必须是对象。")

                decoded_args = _decode_call_payload(method, args)
                st = self._state()
                append_chromaserver_event(base_dir=st.cfg.base_dir, event_type="http_call", payload={"method": method})
                result = asyncio.run(st.worker.call(method, spec, *decoded_args, **kwargs))
                if method in {"record", "ensure_ready"}:
                    _write_json(self, 200, {"ok": True, "result": None})
                    return
                if method == "retrieve":
                    _write_json(self, 200, {"ok": True, "result": str(result or "")})
                    return
                _write_json(self, 200, {"ok": True, "result": _tool_response_to_dict(result)})
                return
            if self.path == "/shutdown":
                st = self._state()
                _write_json(self, 200, {"ok": True})

                def _stop() -> None:
                    try:
                        asyncio.run(asyncio.wait_for(st.worker.close(), timeout=10.0))
                    except Exception:
                        pass
                    try:
                        if st.httpd is not None:
                            st.httpd.shutdown()
                    except Exception:
                        pass
                    time.sleep(0.1)

                threading.Thread(target=_stop, daemon=True).start()
                return
            _write_json(self, 404, {"ok": False, "error": f"未知路径：{self.path}"})
        except Exception as e:
            try:
                append_chromaserver_event(base_dir=self._state().cfg.base_dir, event_type="http_error", level="ERROR", payload={"path": self.path, "error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass
            _write_json(self, 500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    load_dotenv(os.path.join(base_dir, ".env"))
    _setup_third_party_logs(base_dir=base_dir)
    host = _env_str("ANT_VECTOR_SERVER_HOST", "127.0.0.1")
    port = _env_int("ANT_VECTOR_SERVER_PORT", 8765)
    append_chromaserver_event(base_dir=base_dir, event_type="server_start", payload={"host": host, "port": port})
    state = _State(base_dir=base_dir)
    httpd = ThreadingHTTPServer((host, port), ChromaServerHandler)
    setattr(httpd, "_state", state)
    state.httpd = httpd
    httpd.serve_forever()


if __name__ == "__main__":
    main()
