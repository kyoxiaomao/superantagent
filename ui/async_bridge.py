"""
UI 与异步运行时桥接。

在后台线程创建 asyncio event loop，初始化 AgentScope 与精简 runtime，
并将 AgentScope 的“打印消息队列”转交给数据调度中心（DataCenter）统一加工与分发。

线程/职责划分：
- Tk 主线程：只做 UI 渲染（见 ui/chat_app.py），通过轮询 DataCenter 的标准事件刷新界面。
- 后台线程：运行 asyncio event loop，负责初始化模型/智能体/运行时，并持续产出消息。

本文件的核心目的：
1）把“后台 asyncio 世界”与“前台 Tk 世界”隔离开（避免跨线程直接操作 Tk 控件）。
2）提供最小接口：start/stop/submit_user_text，供 UI 使用。
3）把 AgentScope 的 print 队列统一转交 DataCenter，保证 UI/Web 只消费一种标准事件流。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from datetime import datetime
from typing import Any

from ui.data_center import DataCenter


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


def _停止打点启用() -> bool:
    return _env_truthy("ANT_STOP_TRACE", False)


def _格式化时间戳(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time())
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _打印停止打点(*, t0: float, 阶段: str, 详情: str | None = None) -> None:
    if not _停止打点启用():
        return
    elapsed = time.perf_counter() - float(t0)
    msg = f"[STOP][{_格式化时间戳()}][+{elapsed:.3f}s] {阶段}"
    if 详情:
        msg = f"{msg} | {详情}"
    print(msg, flush=True)


def _setup_quiet_third_party_logs() -> None:
    if not _env_truthy("ANT_QUIET_THIRD_PARTY_LOGS", True):
        return
    try:
        import logging

        logging.getLogger().setLevel(logging.WARNING)
        for name in ["chromadb", "flowllm", "posthog"]:
            logging.getLogger(name).setLevel(logging.WARNING)
        _redirect_third_party_logs_to_file()
    except Exception:
        pass
    try:
        from loguru import logger

        level = str(os.getenv("ANT_LOGURU_LEVEL", "INFO") or "INFO").strip().upper()
        logger.remove()
        logger.add(_third_party_log_path(), level=level, encoding="utf-8")
    except Exception:
        pass


def _third_party_log_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    logs_dir = os.path.join(base_dir, "memory", "vector_store", "_logs")
    os.makedirs(logs_dir, exist_ok=True)
    from datetime import datetime

    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(logs_dir, f"third_party_{day}.log")


def _redirect_third_party_logs_to_file() -> None:
    import logging

    path = _third_party_log_path()
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")
    handler.setFormatter(formatter)
    for name in [
        "as",
        "reme_ai",
        "flowllm",
        "chromadb",
        "_reme_personal_long_term_memory",
    ]:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
        logger.addHandler(handler)


def _rebind_agentscope_logger_to_file() -> None:
    import logging

    path = _third_party_log_path()
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")
    handler.setFormatter(formatter)
    logger = logging.getLogger("as")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)


class AsyncColonyBridge:
    def __init__(self, *, data_center: DataCenter) -> None:
        self.data_center = data_center
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runtime: Any | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()

    def start(self) -> None:
        """
        启动后台线程与 asyncio loop。

        - 该方法会短暂等待后台启动信号，避免 UI 被长时间阻塞
        - 若超时，会向数据中心投递一条提示事件，后台仍会继续初始化
        """
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=False)
        self._thread.start()
        ok = self._started.wait(timeout=0.2)
        if not ok:
            self.data_center.push_runtime_message(
                name="ui",
                role="system",
                text="后台运行时仍在初始化，界面已打开，请稍候。",
                metadata={"ui_event": "runtime_init", "status": "pending", "info": "后台运行时仍在初始化"},
            )

    def stop(self) -> None:
        """
        请求停止后台 runtime（精简版）。

        通过 `run_coroutine_threadsafe` 将 stop 请求投递到后台 asyncio loop，
        并等待后台线程做清理（最多 10s）。
        """
        t0 = time.perf_counter()
        _打印停止打点(t0=t0, 阶段="桥接 stop 进入")
        if not self._loop or not self._runtime:
            _打印停止打点(t0=t0, 阶段="桥接 stop 退出", 详情="loop/runtime 不存在")
            return
        if self._loop.is_closed():
            _打印停止打点(t0=t0, 阶段="桥接 stop 退出", 详情="loop 已关闭")
            return
        coro = self._runtime.stop()
        try:
            _打印停止打点(t0=t0, 阶段="桥接投递 runtime.stop 开始")
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            _打印停止打点(t0=t0, 阶段="桥接投递 runtime.stop 结束", 详情=f"future_done={bool(fut.done())}")
        except Exception:
            # 极端情况下（loop 已关闭/线程异常）避免协程泄漏
            try:
                coro.close()
            except Exception:
                pass
            _打印停止打点(t0=t0, 阶段="桥接投递 runtime.stop 异常")
            _打印停止打点(t0=t0, 阶段="桥接 stop 退出", 详情="投递失败")
            return
        timeout_s = float(os.getenv("ANT_STOP_TIMEOUT_S") or "60")
        _打印停止打点(t0=t0, 阶段="桥接等待 runtime.stop 完成 开始", 详情=f"timeout={timeout_s:.0f}s")
        try:
            fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            _打印停止打点(t0=t0, 阶段="桥接等待 runtime.stop 超时")
            raise
        _打印停止打点(t0=t0, 阶段="桥接等待 runtime.stop 完成 结束")
        _打印停止打点(t0=t0, 阶段="桥接等待后台主协程退出 开始", 详情="timeout=30s")
        self._stopped.wait(timeout=30)
        _打印停止打点(t0=t0, 阶段="桥接等待后台主协程退出 结束", 详情=f"is_set={bool(self._stopped.is_set())}")
        th = self._thread
        if th is not None:
            _打印停止打点(t0=t0, 阶段="桥接等待后台线程 join 开始", 详情="timeout=10s")
            th.join(timeout=10)
            _打印停止打点(t0=t0, 阶段="桥接等待后台线程 join 结束", 详情=f"is_alive={bool(th.is_alive())}")

    def submit_user_text(self, text: str) -> None:
        """
        UI -> runtime：提交用户输入。

        注意：UI 线程不得阻塞等待模型输出；这里仅做“跨线程投递”，后续输出由 print 队列回传。
        """
        if not self._loop or not self._runtime:
            return
        asyncio.run_coroutine_threadsafe(self._runtime.submit_user_text(text), self._loop)

    def refresh_vector_store(self) -> bool:
        if not self._loop or not self._runtime:
            return False
        if self._loop.is_closed():
            return False
        asyncio.run_coroutine_threadsafe(self._runtime.refresh_vector_store(), self._loop)
        return True

    def reload_role_utils(self, *, role_key: str) -> concurrent.futures.Future | None:
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if not self._loop or not self._runtime:
            return None
        if self._loop.is_closed():
            return None
        fn = getattr(self._runtime, "reload_role_utils", None)
        if fn is None or not callable(fn):
            raise AttributeError("runtime.reload_role_utils not available")
        return asyncio.run_coroutine_threadsafe(fn(role_key=rk), self._loop)

    def _log_startup(self, text: str) -> None:
        print(text, flush=True)

    def _run_loop(self) -> None:
        """
        后台线程入口：创建并绑定一个新的 asyncio event loop。

        Tk 主线程不能复用该 loop；因此这里显式 new_event_loop，并在该线程内 set_event_loop。
        """
        self._log_startup("正在启动后台线程与异步事件循环（asyncio event loop）")
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run_async())

    async def _run_async(self) -> None:
        """
        后台异步初始化与主任务编排。

        主要步骤：
        1）启动清理：可选清理旧日志/缓存等；
        2）初始化 AgentScope；
        3）加载模型配置、创建 colony；
        4）创建 ColonyRuntime（精简版），并开启：
           - runtime.start()：打开群聊会话并启动各角色 update 调度与心跳监控
           - _forward_printing_messages()：把 AgentScope print 队列转成 UIMessage
        """
        root = os.path.dirname(os.path.dirname(__file__))
        forward_task: asyncio.Task | None = None
        warmup_task: asyncio.Task | None = None
        try:
            _setup_quiet_third_party_logs()
            import agentscope
            import traceback

            from services import load_model_bundles
            from services.event_logger import init_event_logger, log_event
            from services.startup_cleanup import maybe_cleanup_on_start
            from services.workflow import create_colony
            from services.runtime import ColonyRuntime

            self._log_startup("正在清理启动残留")
            maybe_cleanup_on_start(base_dir=root)
            self._log_startup("正在读取全局配置")
            global_cfg = _load_global_config(root)
            self._log_startup("正在初始化事件日志")
            event_log_path = init_event_logger(base_dir=root, run_name="ui")
            log_event(event_type="runtime_start", agent="system", payload={"mode": "ui", "event_log": event_log_path})
            self._log_startup("正在初始化 agentscope（多智能体框架）")
            agentscope.init(
                project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
                name=os.getenv("AGENTSCOPE_NAME", "DesktopPetUI"),
                logging_level=str(global_cfg.get("logging_level") or "INFO"),
            )
            _rebind_agentscope_logger_to_file()

            self._log_startup("正在读取配置并加载模型")
            model_bundles, default_provider = load_model_bundles(os.path.join(root, "configs", "model_configs.yaml"))
            self._log_startup("正在创建智能体群体")
            colony = create_colony(model_bundles=model_bundles, default_provider=default_provider, base_dir=root)
            colony.disable_console_output()

            self._log_startup("正在启动运行时与心跳调度")
            runtime = ColonyRuntime(colony=colony, enable_ui_events=True)
            self._runtime = runtime
            from services.runtime_context import set_current_runtime

            set_current_runtime(runtime)

            print_queue: asyncio.Queue[Any] = asyncio.Queue()
            for agent in runtime.participants:
                if hasattr(agent, "set_msg_queue_enabled"):
                    agent.set_msg_queue_enabled(True, print_queue)

            forward_task = asyncio.create_task(self._forward_printing_messages(print_queue))
            await runtime.start()
            self._log_startup("运行时基础能力已就绪，正在后台预热向量数据库")
            await runtime.ui_event("runtime_init", status="ok")

            self._started.set()

            warmup_task = asyncio.create_task(runtime.warmup_long_term_memory())
            await runtime.wait_stopped()
        except Exception:
            err = traceback.format_exc()
            log_event(event_type="runtime_crash", agent="system", payload={"error": err}, level="ERROR")
            self.data_center.push_runtime_message(name="ui", role="system", text=err, metadata={"ui_event": "error", "error": err})
        finally:
            from services.runtime_context import set_current_runtime

            set_current_runtime(None)
            if warmup_task is not None:
                warmup_task.cancel()
                await asyncio.gather(warmup_task, return_exceptions=True)
            if forward_task is not None:
                forward_task.cancel()
                await asyncio.gather(forward_task, return_exceptions=True)
            self._stopped.set()

    async def _forward_printing_messages(self, print_queue: "asyncio.Queue[Any]") -> None:
        """
        runtime/AgentScope -> 数据中心：转发打印队列中的消息。

        说明：
        - AgentScope 可能把流式输出拆成多段（item=(msg, last)）；
        - 是否做“流式聚合/去重/最终段过滤”由数据中心统一处理，UI 不再关心原始结构。
        """
        while True:
            from agentscope.message import Msg

            from message import msg_to_text

            item = await print_queue.get()
            msg = None
            last = None
            if isinstance(item, tuple) and len(item) >= 2:
                msg = item[0]
                last = bool(item[1])
            elif isinstance(item, Msg):
                msg = item
                last = None
            if msg is None:
                continue

            self.data_center.push_runtime_message(
                name=str(msg.name or ""),
                role=str(getattr(msg, "role", "") or ""),
                text=msg_to_text(msg),
                metadata=getattr(msg, "metadata", None) or None,
                msg_id=str(getattr(msg, "id", "") or "") or None,
                last=last,
            )


def _load_global_config(root: str) -> dict:
    """读取 configs/global_config.yaml。"""
    import yaml

    path = os.path.join(root, "configs", "global_config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("global_config.yaml 内容格式不正确，应为字典结构")
    return data
