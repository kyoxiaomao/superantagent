"""
智能体心跳检测中心（Heartbeat Center）。

定位：
- 不负责调度 update()，只负责“接收心跳上报、检测是否正常、汇总状态并向 UI 同步（推送或拉取）”。\n
- 每个智能体的 update() 由独立异步 task 执行；update() 内部主动上报心跳。\n
- 心跳累计计数可作为智能体“年龄”（age）。\n
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import yaml

from services.event_logger import log_event


def load_heartbeat_configs() -> dict[str, dict[str, Any]]:
    """
    从 configs/agent_configs.yaml 读取各角色 heartbeat 配置块。

    返回结构：{role_key: heartbeat_cfg_dict}
    """

    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "configs", "agent_configs.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("agent_configs.yaml 内容格式不正确，应为字典结构")
    out: dict[str, dict[str, Any]] = {}
    for role_key, role_cfg in (data or {}).items():
        if not isinstance(role_key, str) or not role_key.strip():
            continue
        if isinstance(role_cfg, dict) and isinstance(role_cfg.get("heartbeat"), dict):
            out[role_key.strip()] = dict(role_cfg["heartbeat"])
    return out


@dataclass(frozen=True)
class HeartbeatProfile:
    """
    单个智能体的心跳期望参数（用于健康判定）。

    - interval_s：期望心跳周期（秒）
    - unhealthy_factor：超时倍数阈值，超过 interval*factor 判定异常
    """

    interval_s: float
    unhealthy_factor: float = 2.0

    def deadline_s(self) -> float:
        return max(0.1, float(self.interval_s))


@dataclass
class HeartbeatRecord:
    """
    心跳中心内部记录：每个智能体一条。

    - age：年龄（累计心跳计数）
    - last_tick_ts：最近一次上报时间戳（time.time）
    - busy：智能体自报忙闲（策略在智能体内部，不由中心推断）
    - unhealthy：中心检测到的健康状态
    """

    role_key: str
    agent_name: str
    profile: HeartbeatProfile
    age: int = 0
    last_tick_ts: float = 0.0
    busy: bool = False
    unhealthy: bool = False
    last_status_change_ts: float = 0.0


@dataclass(frozen=True)
class HeartbeatSnapshot:
    """
    对外快照（可给 UI 拉取或调试打印）。
    """

    role_key: str
    agent_name: str
    age: int
    busy: bool
    last_tick_ts: float
    unhealthy: bool
    overdue_s: float
    expected_deadline_s: float


class HeartbeatCenter:
    """
    心跳检测中心：接收上报 + 健康检测 + 状态汇总。

    线程模型：
    - 预期运行在同一个 asyncio event loop 内；\n
    - 依然使用 asyncio.Lock 保护记录表，避免多 task 并发更新产生竞态。\n
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records_by_name: dict[str, HeartbeatRecord] = {}
        self._monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def register_agent(self, *, role_key: str, agent_name: str, profile: HeartbeatProfile) -> None:
        """
        注册/更新一个智能体的心跳期望参数。
        """

        name = str(agent_name or "").strip()
        rk = str(role_key or "").strip()
        if not name or not rk:
            return
        async with self._lock:
            rec = self._records_by_name.get(name)
            if rec is None:
                self._records_by_name[name] = HeartbeatRecord(role_key=rk, agent_name=name, profile=profile, last_status_change_ts=time.time())
            else:
                rec.role_key = rk
                rec.profile = profile
        log_event(event_type="heartbeat_register", agent=name, payload={"role_key": rk, "interval_s": profile.interval_s})

    async def report_tick(
        self,
        *,
        role_key: str,
        agent_name: str,
        busy: bool = False,
        now: float | None = None,
        age_inc: bool = True,
    ) -> None:
        """
        接收智能体心跳上报。

        约定：
        - 一次 update tick 对应一次 report_tick；若智能体选择“本次不算年龄”，可传 age_inc=False。\n
        - now 允许注入，便于测试；正常运行使用 time.time()。\n
        """

        ts = float(now) if now is not None else time.time()
        name = str(agent_name or "").strip()
        rk = str(role_key or "").strip()
        if not name or not rk:
            return

        async with self._lock:
            rec = self._records_by_name.get(name)
            if rec is None:
                profile = HeartbeatProfile(interval_s=5.0)
                rec = HeartbeatRecord(role_key=rk, agent_name=name, profile=profile, last_status_change_ts=ts)
                self._records_by_name[name] = rec
            rec.role_key = rk
            rec.last_tick_ts = ts
            rec.busy = bool(busy)
            if age_inc:
                rec.age = int(rec.age) + 1

        log_event(event_type="heartbeat_tick", agent=name, payload={"role_key": rk, "age": int(rec.age), "busy": bool(busy)})

    async def get_snapshot(self, *, now: float | None = None) -> dict[str, HeartbeatSnapshot]:
        """
        获取所有智能体的心跳快照。

        - 返回 key 为 agent_name，方便 UI 侧按名称渲染；\n
        - now 允许注入，便于测试/回放；正常运行使用 time.time()。\n
        """

        ts = float(now) if now is not None else time.time()
        async with self._lock:
            out: dict[str, HeartbeatSnapshot] = {}
            for name, rec in self._records_by_name.items():
                deadline = rec.profile.deadline_s()
                overdue = max(0.0, ts - float(rec.last_tick_ts or 0.0)) if rec.last_tick_ts else ts
                unhealthy = bool(rec.last_tick_ts) and overdue > deadline * float(rec.profile.unhealthy_factor)
                out[name] = HeartbeatSnapshot(
                    role_key=str(rec.role_key),
                    agent_name=str(name),
                    age=int(rec.age),
                    busy=bool(rec.busy),
                    last_tick_ts=float(rec.last_tick_ts or 0.0),
                    unhealthy=bool(unhealthy),
                    overdue_s=float(overdue),
                    expected_deadline_s=float(deadline),
                )
            return out

    async def start_monitor(self, *, runtime: Any | None = None, check_interval_s: float = 1.0) -> None:
        """
        启动心跳监控循环（可选）。

        - 监控 loop 只做健康检查；检测到状态变化时可向 UI 推送（runtime.ui_event）。\n
        - UI 推送是“增量”的：只在 healthy/unhealthy 发生变化时推送一次，避免刷屏。\n
        """

        if self._monitor_task is not None:
            return
        self._stop_event.clear()
        self._monitor_task = asyncio.create_task(self._monitor_loop(runtime=runtime, check_interval_s=float(check_interval_s)))
        log_event(event_type="heartbeat_center_started", agent="system", payload={"check_interval_s": float(check_interval_s)})

    async def stop_monitor(self) -> None:
        """
        停止心跳监控循环。
        """

        self._stop_event.set()
        t = self._monitor_task
        self._monitor_task = None
        if t is not None:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        log_event(event_type="heartbeat_center_stopped", agent="system", payload={})

    async def _monitor_loop(self, *, runtime: Any | None, check_interval_s: float) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(max(0.2, float(check_interval_s)))
            now = time.time()

            updates: list[tuple[str, str, bool, float, float]] = []
            async with self._lock:
                for name, rec in self._records_by_name.items():
                    deadline = rec.profile.deadline_s()
                    overdue = max(0.0, now - float(rec.last_tick_ts or 0.0)) if rec.last_tick_ts else now
                    unhealthy = bool(rec.last_tick_ts) and overdue > deadline * float(rec.profile.unhealthy_factor)
                    if unhealthy != bool(rec.unhealthy):
                        rec.unhealthy = bool(unhealthy)
                        rec.last_status_change_ts = now
                        updates.append((str(rec.role_key), name, bool(unhealthy), float(overdue), float(deadline)))

            if not updates:
                continue

            for role_key, name, unhealthy, overdue_s, deadline_s in updates:
                log_event(
                    event_type="heartbeat_health_change",
                    agent=str(name),
                    payload={
                        "role_key": str(role_key),
                        "unhealthy": bool(unhealthy),
                        "overdue_s": float(overdue_s),
                        "deadline_s": float(deadline_s),
                    },
                    level="ERROR" if unhealthy else "INFO",
                )
                if runtime is not None and hasattr(runtime, "ui_event"):
                    await runtime.ui_event(
                        "agent_health",
                        agent_name=str(name),
                        unhealthy=bool(unhealthy),
                        overdue_s=float(overdue_s),
                        deadline_s=float(deadline_s),
                    )
                    if unhealthy:
                        await runtime.ui_event(
                            "error",
                            error=f"心跳超时：{name}（{role_key}），超时 {overdue_s:.2f}s，阈值 {deadline_s:.2f}s",
                        )
