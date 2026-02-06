"""
UI 与异步运行时桥接。

在后台线程创建 asyncio event loop，初始化 AgentScope 与 colony runtime，
并将 AgentScope 的“打印消息队列”转换为线程安全队列消息供 Tk 主线程消费。
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import agentscope
import yaml
import requests
import traceback
from agentscope.message import Msg

from message import msg_to_text
from services import load_glm_bundle
from services.event_logger import init_event_logger, log_event
from services.startup_cleanup import maybe_cleanup_on_start
from services.workflow import create_colony
from services.runtime import ColonyRuntime


@dataclass(frozen=True)
class UIMessage:
    name: str
    text: str
    role: str
    metadata: dict[str, Any] | None = None


class AsyncColonyBridge:
    def __init__(self, *, ui_queue: "queue.Queue[UIMessage]") -> None:
        self.ui_queue = ui_queue
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runtime: ColonyRuntime | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        ok = self._started.wait(timeout=15)
        if not ok:
            self.ui_queue.put(
                UIMessage(
                    name="ui",
                    text="后台运行时启动超时：请检查虚拟环境依赖、模型配置与网络连接。",
                    role="system",
                    metadata={"ui_event": "error", "error": "后台运行时启动超时"},
                ),
            )

    def stop(self) -> None:
        if not self._loop or not self._runtime:
            return
        if self._loop.is_closed():
            return
        coro = self._runtime.request_stop()
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception:
            try:
                coro.close()
            except Exception:
                pass
        self._stopped.wait(timeout=10)

    def submit_user_text(self, text: str) -> None:
        if not self._loop or not self._runtime:
            return
        asyncio.run_coroutine_threadsafe(self._runtime.submit_user_text(text), self._loop)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run_async())

    async def _run_async(self) -> None:
        root = os.path.dirname(os.path.dirname(__file__))
        maybe_cleanup_on_start(base_dir=root)
        global_cfg = _load_global_config(root)
        studio_url = str(global_cfg.get("studio_url") or "") or None
        event_log_path = init_event_logger(base_dir=root, run_name="ui")
        log_event(event_type="runtime_start", agent="system", payload={"mode": "ui", "event_log": event_log_path})
        try:
            agentscope.init(
                project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
                name=os.getenv("AGENTSCOPE_NAME", "DesktopPetUI"),
                logging_level=str(global_cfg.get("logging_level") or "INFO"),
                studio_url=studio_url,
            )
        except requests.exceptions.RequestException:
            agentscope.init(
                project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
                name=os.getenv("AGENTSCOPE_NAME", "DesktopPetUI"),
                logging_level=str(global_cfg.get("logging_level") or "INFO"),
            )
        try:
            model_bundle = load_glm_bundle(os.path.join(root, "configs", "model_configs.yaml"))
            colony = create_colony(model_bundle)
            colony.disable_console_output()

            runtime = ColonyRuntime(colony=colony, enable_ui_events=True)
            self._runtime = runtime

            print_queue: asyncio.Queue[Any] = asyncio.Queue()
            for agent in runtime.participants:
                if hasattr(agent, "set_msg_queue_enabled"):
                    agent.set_msg_queue_enabled(True, print_queue)

            self._started.set()

            forward_task = asyncio.create_task(self._forward_printing_messages(print_queue))
            runtime_task = asyncio.create_task(runtime.run_forever())
            watchdog_task = asyncio.create_task(self._watch_runtime_startup(runtime))

            done, pending = await asyncio.wait(
                {runtime_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in {forward_task, watchdog_task}:
                t.cancel()
            await asyncio.gather(forward_task, watchdog_task, return_exceptions=True)

            for t in done:
                err = t.exception()
                if err:
                    msg = f"后台运行时异常退出：{err}"
                    log_event(event_type="runtime_task_error", agent="system", payload={"error": str(err)}, level="ERROR")
                    self.ui_queue.put(UIMessage(name="ui", text=msg, role="system", metadata={"ui_event": "error", "error": msg}))
        except Exception:
            err = traceback.format_exc()
            log_event(event_type="runtime_crash", agent="system", payload={"error": err}, level="ERROR")
            self.ui_queue.put(
                UIMessage(
                    name="ui",
                    text=err,
                    role="system",
                    metadata={"ui_event": "error", "error": err},
                ),
            )
        finally:
            self._stopped.set()

    async def _watch_runtime_startup(self, runtime: ColonyRuntime) -> None:
        start = time.time()
        warned = False
        while True:
            await asyncio.sleep(1.0)
            if self._stopped.is_set():
                return
            hub = getattr(runtime, "_hub", None)
            if hub is not None:
                return
            if warned:
                continue
            if time.time() - start >= 10.0:
                warned = True
                msg = "后台运行时尚未进入群聊（MsgHub）阶段：可能卡在初始化/模型连接/AgentScope内部。请查看日志 events_ui_*.jsonl 与控制台输出。"
                log_event(event_type="runtime_startup_stall", agent="system", payload={"after_s": 10})
                self.ui_queue.put(UIMessage(name="ui", text=msg, role="system", metadata={"ui_event": "error", "error": msg}))

    async def _forward_printing_messages(self, print_queue: "asyncio.Queue[Any]") -> None:
        while True:
            item = await print_queue.get()
            msg = None
            last = True
            if isinstance(item, tuple) and len(item) >= 2:
                msg = item[0]
                last = bool(item[1])
            elif isinstance(item, Msg):
                msg = item
                last = True
            if not last or msg is None:
                continue
            self.ui_queue.put(
                UIMessage(
                    name=str(msg.name or ""),
                    text=msg_to_text(msg),
                    role=str(getattr(msg, "role", "") or ""),
                    metadata=getattr(msg, "metadata", None) or None,
                ),
            )


def _load_global_config(root: str) -> dict:
    path = os.path.join(root, "configs", "global_config.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
