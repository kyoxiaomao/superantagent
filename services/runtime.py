"""
运行时（精简版）。

设计原则：
- 不再负责“事件队列 + run_forever 消费循环”，也不承载任何调度/话题/增量记忆策略；
- 仅负责“生命周期”：打开/关闭群聊会话、维护群聊注册表、启动/停止各角色心跳调度；
- 用户输入只直达蚁后_瑟拉（queen_sera），蚁后_瑟拉直接回复用户；
- 若需要把信息发送到群聊，由蚁王_特鲁（king_tru）在自己的 update() 中决定并执行发言。
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from datetime import datetime
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agentscope.message import Msg

from message import make_msg, msg_to_text
from message.group_chat import GroupChat, GroupChatRegistry, MessageCenter, open_group_chat
from services.agent_heartbeat import HeartbeatCenter, load_heartbeat_configs
from services.agent_update_scheduler import UpdateScheduler, UpdateTarget
from services.event_logger import log_event


def _环境变量真值(name: str, default: bool) -> bool:
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
    return _环境变量真值("ANT_STOP_TRACE", False)


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


@dataclass(frozen=True)
class RuntimeChatRef:
    """
    运行时持有的群聊引用。

    这里不做“消息推送”，只作为共享状态与历史载体，供各角色 update() 自己增量读取。
    """

    chat_id: str
    chat: GroupChat


def iter_role_agents(colony: Any) -> list[tuple[str, Any]]:
    """
    按固定顺序返回 (role_key, agent) 列表。

    该顺序用于：初始化 UI 状态卡、启动心跳任务等；保持稳定有利于用户理解。
    """

    return [
        ("king_tru", getattr(colony, "king_tru", None)),
        ("queen_sera", getattr(colony, "queen_sera", None)),
        ("soldier_ares", getattr(colony, "soldier_ares", None)),
        ("worker_light", getattr(colony, "worker_light", None)),
        ("worker_nova", getattr(colony, "worker_nova", None)),
        ("worker_reed", getattr(colony, "worker_reed", None)),
    ]


class ColonyRuntime:
    """
    ColonyRuntime（精简版）：

    - start()：打开群聊会话 + 启动各角色心跳调度
    - stop()：停止心跳调度 + 关闭群聊会话
    - submit_user_text()：把用户输入直接交给蚁后处理，并把回复转换成 UI 事件
    """

    def __init__(self, *, colony: Any, enable_ui_events: bool = False) -> None:
        self.colony = colony
        self.participants: list[Any] = list(getattr(colony, "participants", []) or [])
        self._enable_ui_events = bool(enable_ui_events)
        self._message_center = MessageCenter(base_dir=os.path.dirname(os.path.dirname(__file__)))

        self._chat_registry = GroupChatRegistry()
        self._exit_stack = AsyncExitStack()
        self._started = False
        self._stopped = asyncio.Event()
        self._memory_warmed = False

        self._heartbeat_cfg = load_heartbeat_configs()
        self._heartbeat_center = HeartbeatCenter()
        self._update_scheduler: UpdateScheduler | None = None

    @property
    def chat_registry(self) -> GroupChatRegistry:
        return self._chat_registry

    async def start(self) -> None:
        """
        启动运行时：打开默认全员群聊，并启动各角色心跳。

        注意：这里不会启动任何“主循环”；运行时只靠外部调用 submit_user_text/stop 与各角色心跳推进。
        """

        if self._started:
            return

        if str(os.getenv("ANT_RESET_ON_START") or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            _clear_memory_artifacts(base_dir)
            for _, agent in iter_role_agents(self.colony):
                if agent is None:
                    continue
                mem = getattr(agent, "memory", None)
                if mem is not None:
                    await mem.clear()

        chat = await self._exit_stack.enter_async_context(
            open_group_chat(
                chat_id="main",
                participants=self.participants,
                history_max_len=2000,
                on_history_dropped=None,
                persist_dir=os.path.join(os.path.dirname(os.path.dirname(__file__)), "message", "chatdata"),
            )
        )
        self._chat_registry.set(chat)

        targets: list[UpdateTarget] = []
        for role_key, agent in iter_role_agents(self.colony):
            if agent is None:
                continue
            update_fn = getattr(agent, "update", None)
            if update_fn is None:
                continue
            targets.append(UpdateTarget(role_key=role_key, agent=agent, update=update_fn))

        self._update_scheduler = UpdateScheduler(
            targets=targets,
            heartbeat_cfg=self._heartbeat_cfg,
            heartbeat_center=self._heartbeat_center,
            runtime=self,
            chat_registry=self._chat_registry,
        )
        await self._update_scheduler.start()
        await self._heartbeat_center.start_monitor(runtime=self, check_interval_s=1.0)

        if self._enable_ui_events:
            for _, agent in iter_role_agents(self.colony):
                if agent is None:
                    continue
                name = str(getattr(agent, "name", "") or "").strip()
                if name:
                    await self.ui_event("agent_status", agent_name=name, status="idle")

        self._started = True
        self._memory_warmed = False
        log_event(event_type="runtime_started", agent="system", payload={"chats": ["main"], "roles": [rk for rk, _ in iter_role_agents(self.colony)]})

    async def warmup_long_term_memory(self) -> None:
        if self._stopped.is_set():
            return
        if self._memory_warmed:
            return

        from chromaserver.client import load_server_url
        from chromaserver.logger import append_chromaserver_event
        t0 = time.perf_counter()
        base_dir = os.path.dirname(os.path.dirname(__file__))
        url = str(load_server_url() or "").strip()
        append_chromaserver_event(base_dir=base_dir, event_type="warmup_start", payload={"url": url})
        log_event(event_type="vector_store_warmup_start", agent="system", payload={"url": url})
        if self._enable_ui_events:
            await self.ui_event("memory_warmup", status="start", url=url)

        if not url:
            append_chromaserver_event(base_dir=base_dir, event_type="warmup_skip", level="WARN", payload={"reason": "not_configured", "url": url})
            log_event(event_type="vector_store_warmup_skip", agent="system", payload={"reason": "not_configured", "url": url}, level="WARN")
            if self._enable_ui_events:
                await self.ui_event("memory_warmup", status="skip", reason="not_configured", url=url)
            return

        parsed = urlparse(url)
        host = str(parsed.hostname or "").strip().lower()
        port = int(parsed.port or 0)
        if host in {"localhost", "127.0.0.1"} and port > 0:
            try:
                def _probe() -> None:
                    s = socket.create_connection((host, port), timeout=0.3)
                    s.close()

                await asyncio.to_thread(_probe)
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                append_chromaserver_event(
                    base_dir=base_dir,
                    event_type="warmup_skip",
                    level="WARN",
                    payload={"reason": "service_not_running", "url": url, "detail": detail},
                )
                log_event(
                    event_type="vector_store_warmup_skip",
                    agent="system",
                    payload={"reason": "service_not_running", "url": url, "detail": detail},
                    level="WARN",
                )
                if self._enable_ui_events:
                    await self.ui_event("memory_warmup", status="skip", reason="service_not_running", url=url, detail=detail)
                return

        entered: list[str] = []
        try:
            for _, agent in iter_role_agents(self.colony):
                if self._stopped.is_set():
                    return
                if agent is None:
                    continue
                mem = getattr(agent, "_ant_long_term_memory", None)
                if mem is None:
                    continue
                ensure_ready = getattr(mem, "ensure_ready", None)
                if ensure_ready is None:
                    raise TypeError("长期记忆对象缺少 ensure_ready()，无法在工作线程预热。")
                await ensure_ready()
                name = str(getattr(agent, "name", "") or "").strip()
                if name:
                    entered.append(name)
            self._memory_warmed = True
            cost_ms = (time.perf_counter() - t0) * 1000.0
            print(f"向量数据库预热完成：url={url} cost_ms={cost_ms:.0f} agents={len(entered)}", flush=True)
            append_chromaserver_event(base_dir=base_dir, event_type="warmup_ok", payload={"cost_ms": float(cost_ms), "agents": entered})
            log_event(event_type="vector_store_warmup_ok", agent="system", payload={"cost_ms": float(cost_ms), "agents": entered})
            if self._enable_ui_events:
                await self.ui_event("memory_warmup", status="ok", cost_ms=float(cost_ms), agents=entered)
        except Exception as e:
            cost_ms = (time.perf_counter() - t0) * 1000.0
            detail = f"{type(e).__name__}: {e}"
            reason = "unknown"
            if "未配置向量库服务地址" in detail:
                reason = "not_configured"
            elif "WinError 10061" in detail or "积极拒绝" in detail or "Connection refused" in detail:
                reason = "service_not_running"
            append_chromaserver_event(
                base_dir=base_dir,
                event_type="warmup_error",
                level="ERROR",
                payload={"cost_ms": float(cost_ms), "reason": reason, "url": url, "detail": detail},
            )
            log_event(
                event_type="vector_store_warmup_error",
                agent="system",
                payload={"cost_ms": float(cost_ms), "reason": reason, "url": url, "detail": detail},
                level="ERROR",
            )
            if self._enable_ui_events:
                await self.ui_event("memory_warmup", status="error", cost_ms=float(cost_ms), reason=reason, url=url, detail=detail)
            raise

    async def refresh_vector_store(self) -> None:
        self._memory_warmed = False
        await self.warmup_long_term_memory()

    async def stop(self) -> None:
        t0 = time.perf_counter()
        _打印停止打点(t0=t0, 阶段="运行时 stop 进入")
        if self._stopped.is_set():
            _打印停止打点(t0=t0, 阶段="运行时 stop 退出", 详情="已 stopped")
            return

        try:
            try:
                if self._update_scheduler is not None:
                    _打印停止打点(t0=t0, 阶段="停止 update 调度器 开始")
                    await self._update_scheduler.stop()
                    _打印停止打点(t0=t0, 阶段="停止 update 调度器 结束")
                else:
                    _打印停止打点(t0=t0, 阶段="停止 update 调度器 跳过", 详情="未初始化")
            finally:
                _打印停止打点(t0=t0, 阶段="停止心跳监控 开始")
                await self._heartbeat_center.stop_monitor()
                _打印停止打点(t0=t0, 阶段="停止心跳监控 结束")
        finally:
            try:
                _打印停止打点(t0=t0, 阶段="关闭 ExitStack 资源 开始")
                await self._exit_stack.aclose()
                _打印停止打点(t0=t0, 阶段="关闭 ExitStack 资源 结束")
            finally:
                self._stopped.set()
                self._started = False
                log_event(event_type="runtime_stopped", agent="system", payload={})
                _打印停止打点(t0=t0, 阶段="运行时 stop 完成")

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def get_heartbeat_snapshot(self) -> dict[str, Any]:
        return await self._heartbeat_center.get_snapshot()

    def _get_agent_by_role_key(self, *, role_key: str) -> Any:
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        for k, agent in iter_role_agents(self.colony):
            if k == rk:
                if agent is None:
                    raise RuntimeError(f"agent for role_key={rk} is None")
                return agent
        raise KeyError(f"unknown role_key: {rk}")

    def apply_system_prompt(self, *, role_key: str) -> str:
        """
        热应用 system prompt：从配置读取最新 prompt，并更新运行中 Agent 的 sys_prompt。

        注意：仅影响后续轮次；当前正在生成的回复不受影响。
        """
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        base_dir = os.path.dirname(os.path.dirname(__file__))
        from services.role_config_store import load_roles

        roles = load_roles(base_dir)
        if rk not in roles:
            raise KeyError(f"unknown role_key: {rk}")
        new_base = str(getattr(roles[rk], "sys_prompt", "") or "")
        if not new_base.strip():
            raise ValueError("sys_prompt is empty")

        agent = self._get_agent_by_role_key(role_key=rk)
        marker = "\n\n# 长期记忆使用规则\n"
        old_full = str(getattr(agent, "sys_prompt", "") or "")
        if marker in old_full:
            tail = old_full[old_full.index(marker) :]
            new_full = f"{new_base}{tail}"
        else:
            new_full = new_base
        setattr(agent, "sys_prompt", new_full)
        return new_full

    async def submit_user_text(self, text: str) -> str:
        if not self._started:
            return ""
        raw = (text or "").strip()
        if not raw:
            return ""

        queen = getattr(self.colony, "queen_sera", None)
        if queen is None:
            return ""

        user_msg = Msg(name="user", role="user", content=raw)
        log_event(event_type="user_text", agent="user", payload={"text": raw})
        self._message_center.append_user_queen(role="user", name="user", content=raw)
        try:
            reply: Msg = await queen(user_msg)
            reply_text = msg_to_text(reply).strip()
            if reply_text:
                if not self._enable_ui_events:
                    self._message_center.append_user_queen(role="assistant", name=str(getattr(queen, "name", "蚁后_瑟拉")), content=reply_text)
            if self._enable_ui_events and reply_text:
                await self.ui_event("user_reply", text=reply_text, agent_name=str(getattr(queen, "name", "蚁后_瑟拉")))
            return reply_text
        except Exception as e:
            err = f"蚁后_瑟拉回复失败：{e}"
            log_event(event_type="queen_reply_error", agent=str(getattr(queen, "name", "蚁后_瑟拉")), payload={"error": err}, level="ERROR")
            if self._enable_ui_events:
                await self.ui_event("error", error=err)
            return ""
        finally:
            if self._enable_ui_events:
                setattr(queen, "_ui_busy_flag", False)

    async def ui_event(self, ui_event: str, **metadata: Any) -> None:
        if not self._enable_ui_events:
            return
        reporter = getattr(self.colony, "queen_sera", None) or getattr(self.colony, "king_tru", None) or (self.participants[0] if self.participants else None)
        if reporter is None or not hasattr(reporter, "print"):
            return
        msg = make_msg(
            role="system",
            name="ui",
            content="",
            metadata={"ui_event": str(ui_event or ""), **metadata},
        )
        await reporter.print(msg)

    async def ui_emit_group_msg(self, msg: Msg) -> None:
        if not self._enable_ui_events:
            return
        reporter = getattr(self.colony, "queen_sera", None) or getattr(self.colony, "king_tru", None) or (self.participants[0] if self.participants else None)
        if reporter is None or not hasattr(reporter, "print"):
            return
        md = dict(getattr(msg, "metadata", None) or {})
        md.pop("ui_event", None)
        ui_msg = Msg(
            name=str(getattr(msg, "name", "") or ""),
            role=str(getattr(msg, "role", "") or ""),
            content=getattr(msg, "content", ""),
            metadata=md or None,
        )
        await reporter.print(ui_msg)


def _clear_memory_artifacts(base_dir: str) -> None:
    base = os.path.abspath(base_dir)
    storage_dir = os.path.join(base, "memory", "storage")
    if os.path.isdir(storage_dir):
        for name in os.listdir(storage_dir):
            if not name.endswith(".jsonl"):
                continue
            p = os.path.join(storage_dir, name)
            try:
                os.remove(p)
            except Exception:
                pass

    vector_store_dir = os.path.join(base, "memory", "vector_store")
    if os.path.isdir(vector_store_dir):
        _remove_dir_tree(vector_store_dir)


def _remove_dir_tree(path: str) -> None:
    for root, dirs, files in os.walk(path, topdown=False):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                os.remove(p)
            except Exception:
                pass
        for dn in dirs:
            d = os.path.join(root, dn)
            try:
                os.rmdir(d)
            except Exception:
                pass
    try:
        os.rmdir(path)
    except Exception:
        pass
