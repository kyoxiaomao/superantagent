"""
智能体 update() 调度器（每个智能体独立异步 task）。

定位：
- 负责为每个智能体创建独立 asyncio task，按配置周期调用 `await agent.update(ctx)`；\n
- 不负责心跳检测；心跳检测由 HeartbeatCenter 统一接收上报并判断是否正常；\n
- update() 内业务策略（事务清单、群聊增量读取、是否发言、是否写入记忆等）由各角色自行实现。\n
"""

from __future__ import annotations

import asyncio
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agentscope.message import Msg

from services.agent_heartbeat import HeartbeatCenter, HeartbeatProfile
from services.event_logger import log_event


@dataclass
class AgentLocalState:
    """
    单个智能体的本地运行态（属于 update 调度层，不属于 HeartbeatCenter）。\n

    说明：
    - HeartbeatCenter 关心的是“是否按频率上报/是否超时/年龄/忙闲”等跨智能体可比较指标。\n
    - 智能体仍需要一些“只与自己相关”的本地状态，例如：每个群聊的增量读取游标。\n
    """

    tick_count: int = 0
    busy: bool = False
    last_seen_idx_by_chat: dict[str, int] = field(default_factory=dict)
    last_post_ts_by_chat: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateContext:
    """
    传入各角色 update() 的上下文对象。\n

    设计意图：
    - 把“心跳中心、群聊注册表、运行时 UI 输出口”等依赖收敛到 ctx；\n
    - 让 update() 只关心业务：扫描事务/读取群聊/更新忙闲/决定是否发言。\n
    """

    role_key: str
    agent: Any
    agent_name: str
    now: float
    tick_count: int
    local_state: AgentLocalState
    heartbeat_center: HeartbeatCenter
    runtime: Any | None = None
    chat_registry: Any | None = None


@dataclass(frozen=True)
class UpdateTarget:
    """
    update 调度目标。\n

    - role_key：角色键（king/queen/...）\n
    - agent：智能体对象（通常为 AgentScope 的 ReActAgent 或 wrapper）\n
    - update：该角色的 update 函数（必须是 async）\n
    """

    role_key: str
    agent: Any
    update: Callable[[UpdateContext], Awaitable[None]]


async def read_chat_delta(*, ctx: UpdateContext, chat_id: str) -> list[Msg]:
    """
    从指定群聊读取“增量消息列表”。\n

    约定：
    - 每个智能体在 ctx.local_state.last_seen_idx_by_chat 里记录自己读到的历史游标；\n
    - 该函数只做“增量切片 + 游标推进”，不做记忆写入、也不做消息过滤。\n
    """

    if ctx.chat_registry is None:
        return []
    if not hasattr(ctx.chat_registry, "get"):
        return []
    chat = ctx.chat_registry.get(str(chat_id))
    if chat is None:
        return []

    start = int(ctx.local_state.last_seen_idx_by_chat.get(str(chat_id), 0) or 0)
    async with chat.history_lock:
        hist = chat.history
        if start > len(hist):
            start = len(hist)
        delta = list(hist[start:])
        ctx.local_state.last_seen_idx_by_chat[str(chat_id)] = len(hist)
    return delta


class UpdateScheduler:
    """
    update 调度器：为多个智能体创建并管理 update task。\n

    - 每个 role_key 一个 task；\n
    - task 崩溃时会自动退避重启；\n
    - 只负责“调度 update()”，不做心跳检测；心跳检测由 HeartbeatCenter 负责。\n
    """

    def __init__(
        self,
        *,
        targets: list[UpdateTarget],
        heartbeat_cfg: dict[str, dict[str, Any]] | None,
        heartbeat_center: HeartbeatCenter,
        runtime: Any | None = None,
        chat_registry: Any | None = None,
    ) -> None:
        self._targets = list(targets)
        self._cfg = heartbeat_cfg or {}
        self._heartbeat_center = heartbeat_center
        self._runtime = runtime
        self._chat_registry = chat_registry

        self._stop_event = asyncio.Event()
        self._tasks_by_role: dict[str, asyncio.Task] = {}
        self._restart_attempts: dict[str, int] = {}
        self._local_state_by_role: dict[str, AgentLocalState] = {}

    def tasks(self) -> list[asyncio.Task]:
        return list(self._tasks_by_role.values())

    async def start(self) -> None:
        """
        启动所有启用的 update 任务。\n

        注意：这里是 async，因为需要注册 HeartbeatCenter 的期望频率参数。\n
        """

        if self._tasks_by_role:
            return

        for t in self._targets:
            cfg = self._cfg.get(t.role_key) or {}
            if not bool(cfg.get("enabled", True)):
                continue

            interval_s = float(cfg.get("interval_s", 5.0))
            unhealthy_factor = float(cfg.get("unhealthy_factor", 2.0))
            agent_name = str(getattr(t.agent, "name", "") or t.role_key).strip()

            await self._heartbeat_center.register_agent(
                role_key=str(t.role_key),
                agent_name=agent_name,
                profile=HeartbeatProfile(interval_s=interval_s, unhealthy_factor=unhealthy_factor),
            )

            self._local_state_by_role[t.role_key] = AgentLocalState()
            task = asyncio.create_task(self._loop(target=t))
            self._tasks_by_role[t.role_key] = task
            task.add_done_callback(lambda done, rk=t.role_key, tg=t: self._on_done(done, rk, tg))

        log_event(event_type="update_scheduler_started", agent="system", payload={"roles": list(self._tasks_by_role.keys())})

    async def stop(self) -> None:
        """停止并等待所有 update task 退出（cancel + gather）。"""

        self._stop_event.set()
        for task in self._tasks_by_role.values():
            task.cancel()
        if self._tasks_by_role:
            await asyncio.gather(*self._tasks_by_role.values(), return_exceptions=True)
        self._tasks_by_role = {}
        log_event(event_type="update_scheduler_stopped", agent="system", payload={})

    async def _loop(self, *, target: UpdateTarget) -> None:
        cfg = self._cfg.get(target.role_key) or {}
        interval_s = float(cfg.get("interval_s", 5.0))
        first = True

        while not self._stop_event.is_set():
            try:
                if first:
                    first = False
                    sleep_s = random.uniform(0.0, min(2.0, max(0.1, float(interval_s))))
                else:
                    sleep_s = max(0.1, float(interval_s))
                await asyncio.sleep(sleep_s)

                st = self._local_state_by_role.get(target.role_key)
                if st is None:
                    st = AgentLocalState()
                    self._local_state_by_role[target.role_key] = st
                st.tick_count += 1
                now = time.time()

                agent_name = str(getattr(target.agent, "name", "") or target.role_key).strip()
                ctx = UpdateContext(
                    role_key=str(target.role_key),
                    agent=target.agent,
                    agent_name=agent_name,
                    now=now,
                    tick_count=int(st.tick_count),
                    local_state=st,
                    heartbeat_center=self._heartbeat_center,
                    runtime=self._runtime,
                    chat_registry=self._chat_registry,
                )
                await target.update(ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                tb = traceback.format_exc()
                log_event(
                    event_type="update_tick_error",
                    agent=str(getattr(target.agent, "name", target.role_key)),
                    payload={"role_key": target.role_key, "error": str(e), "traceback": tb[:1000]},
                    level="ERROR",
                )
                await asyncio.sleep(0.5)

    def _on_done(self, task: asyncio.Task, role_key: str, target: UpdateTarget) -> None:
        if self._stop_event.is_set():
            return
        if task.cancelled():
            return
        err = task.exception()
        if err is None:
            log_event(event_type="update_task_done", agent=str(getattr(target.agent, "name", role_key)), payload={"role_key": role_key})
            return

        attempt = int(self._restart_attempts.get(role_key, 0)) + 1
        self._restart_attempts[role_key] = attempt
        backoff_s = min(30.0, 2.0 * float(attempt))
        log_event(
            event_type="update_task_crashed",
            agent=str(getattr(target.agent, "name", role_key)),
            payload={"role_key": role_key, "attempt": attempt, "backoff_s": backoff_s, "error": str(err)},
            level="ERROR",
        )
        loop = asyncio.get_running_loop()
        loop.create_task(self._restart_after(role_key=role_key, target=target, delay_s=backoff_s))

    async def _restart_after(self, *, role_key: str, target: UpdateTarget, delay_s: float) -> None:
        await asyncio.sleep(max(0.5, float(delay_s)))
        if self._stop_event.is_set():
            return
        task = asyncio.create_task(self._loop(target=target))
        self._tasks_by_role[role_key] = task
        task.add_done_callback(lambda done, rk=role_key, tg=target: self._on_done(done, rk, tg))
        log_event(
            event_type="update_task_restarted",
            agent=str(getattr(target.agent, "name", role_key)),
            payload={"role_key": role_key, "delay_s": float(delay_s), "attempt": int(self._restart_attempts.get(role_key, 0))},
        )
