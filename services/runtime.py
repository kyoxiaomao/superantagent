"""
常驻群聊运行时（ColonyRuntime）。

负责维护 MsgHub 群聊、接收用户输入、按心跳调度各角色发言，并执行蚁王调度 JSON 的 dispatch/tool_create 闭环；
可选将关键事件转为 UI 可消费的“打印消息/状态事件”。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Literal
import traceback

import yaml

from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from message import extract_first_json_obj, extract_first_topic_tag, is_valid_topic_tag, make_msg, msg_to_text
from services.event_logger import log_event


_TOPIC_TURN_ORDER: list[str] = ["king", "soldier", "emotion_worker", "browser_worker", "doc_worker"]


@dataclass(frozen=True)
class RuntimeEvent:
    type: Literal["user_text", "stop"]
    payload: Any = None


@dataclass
class AgentSyncState:
    last_seen_idx: int = 0
    last_increment_ts: float = 0.0
    busy: bool = False
    last_topic_decision_ts: float = 0.0
    last_topic_gate_ts: float = 0.0


class ColonyRuntime:
    def __init__(self, *, colony: Any, max_steps: int = 20, enable_ui_events: bool = False) -> None:
        self.colony = colony
        self.max_steps = max_steps
        self.queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._hub: MsgHub | None = None
        self._last_user_ts = 0.0
        self._last_speak_ts: dict[str, float] = {}
        self._heartbeat_tasks: list[asyncio.Task] = []
        self._heartbeat_tasks_by_role: dict[str, asyncio.Task] = {}
        self._heartbeat_restart_attempts: dict[str, int] = {}
        self._last_heartbeat_ts_by_role: dict[str, float] = {}
        self._call_sem = asyncio.Semaphore(2)
        self._heartbeat_cfg = _load_heartbeat_configs()
        self._enable_ui_events = bool(enable_ui_events)
        self._chat_history: list[Msg] = []
        self._history_max_len = 2000
        self._history_lock = asyncio.Lock()
        self._agent_state: dict[str, AgentSyncState] = {}
        self._topic_lock = asyncio.Lock()
        self._active_topic_tag: str | None = None
        self._active_topic_ts: float = 0.0
        self._last_topic_initiated_ts: float = 0.0
        self._topic_turn_role_key: str = "king"
        self._topic_turn_next_ts: float = 0.0
        self._topic_turn_stall_s: float = 20.0

    @property
    def participants(self) -> list[Any]:
        return self.colony.participants

    async def submit_user_text(self, text: str) -> None:
        log_event(event_type="user_text", agent="user", payload={"text": str(text)})
        await self.queue.put(RuntimeEvent(type="user_text", payload=text))

    async def request_stop(self) -> None:
        await self.queue.put(RuntimeEvent(type="stop"))

    async def run_forever(self) -> None:
        async with MsgHub(participants=self.participants) as hub:
            self._hub = hub
            if self._enable_ui_events:
                for _, agent in _iter_role_agents(self.colony):
                    name = getattr(agent, "name", "")
                    if name:
                        await self._ui_event("agent_status", agent_name=name, status="idle")
                        log_event(event_type="agent_status", agent=name, payload={"status": "idle"})
            now = time.time()
            for _, agent in _iter_role_agents(self.colony):
                name = str(getattr(agent, "name", "") or "").strip()
                if not name:
                    continue
                self._agent_state[name] = AgentSyncState(last_seen_idx=0, last_increment_ts=now, busy=False)
            self._topic_turn_role_key = "king"
            self._topic_turn_next_ts = 0.0
            log_event(event_type="heartbeat_cfg_snapshot", agent="system", payload=self._heartbeat_cfg)
            self._heartbeat_tasks = self._start_heartbeats()
            while not self._stop_event.is_set():
                event = await self.queue.get()
                if event.type == "stop":
                    self._stop_event.set()
                    break
                if event.type == "user_text":
                    await self._handle_user_text(str(event.payload or ""))
            await self._stop_heartbeats()

    async def _handle_user_text(self, text: str) -> None:
        if not self._hub:
            return
        text = text.strip()
        if not text:
            return

        self._last_user_ts = time.time()
        user_msg = Msg(name="user", role="user", content=text)
        await self._hub.broadcast(user_msg)
        await self._append_history(user_msg)

        current_msg: Msg | None = user_msg
        planned = False
        for _ in range(self.max_steps):
            king_msg: Msg = await self._call_agent(self.colony.king, current_msg, track_speak_ts=True, append_history=True)
            king_text = msg_to_text(king_msg).strip()
            cmd = extract_first_json_obj(king_text)
            if not isinstance(cmd, dict):
                if self._enable_ui_events and king_text:
                    await self._ui_event("user_reply", text=king_text, agent_name=getattr(self.colony.king, "name", "蚁族"))
                return

            task_type = str(cmd.get("task_type") or "").strip()
            target_ant = str(cmd.get("target_ant") or "none").strip()
            task_params = cmd.get("task_params") or {}

            if task_type in {"ask", "final"}:
                reply_text = ""
                if isinstance(task_params, dict):
                    reply_text = str(task_params.get("reply") or task_params.get("answer") or task_params.get("question") or "")
                reply_text = (reply_text or king_text).strip()
                if self._enable_ui_events and reply_text:
                    await self._ui_event("user_reply", text=reply_text, agent_name=getattr(self.colony.king, "name", "蚁族"))
                return

            if task_type == "tool_create":
                await self._broadcast_step(f"蚁王调度：tool_create → 兵蚁，参数：{json.dumps(task_params, ensure_ascii=False)}")
                log_event(event_type="dispatch_tool_create", agent=getattr(self.colony.king, "name", "蚁王"), payload={"task_params": task_params})
                current_msg = await self._call_soldier(task_params)
                continue

            if task_type == "dispatch":
                agent = self.colony.agent_map.get(target_ant)
                if agent is None:
                    return
                await self._broadcast_step(
                    f"蚁王调度：dispatch → {target_ant}，参数：{json.dumps(task_params, ensure_ascii=False)}",
                )
                log_event(event_type="dispatch", agent=getattr(self.colony.king, "name", "蚁王"), payload={"target_ant": target_ant, "task_params": task_params})
                if not planned:
                    planned = True
                    await self._broadcast_step("开始群聊规划：收集各工蚁的关键信息/约束与风险点。")
                    log_event(event_type="planning_start", agent=getattr(self.colony.king, "name", "蚁王"), payload={"target_ant": target_ant})
                    current_msg = await self._planning_round(target_ant=target_ant, task_params=task_params)
                    await self._broadcast_step("群聊规划完成：已反馈蚁王，继续调度。")
                    log_event(event_type="planning_done", agent=getattr(self.colony.king, "name", "蚁王"), payload={"target_ant": target_ant})
                    continue
                current_msg = await self._call_worker(target_ant, task_params)
                continue

            return

    async def _call_worker(self, target_ant: str, task_params: Any) -> Msg:
        if not isinstance(task_params, dict):
            task_params = {"task": str(task_params)}

        if target_ant == "browser_worker":
            await self._ensure_tool_ready(
                worker_agent=self.colony.browser_worker,
                worker_type="browser_worker",
                tool_name=str(task_params.get("tool_name") or "open_browser_search_image"),
            )

        if target_ant == "doc_worker":
            await self._ensure_tool_ready(
                worker_agent=self.colony.doc_worker,
                worker_type="doc_worker",
                tool_name=str(task_params.get("tool_name") or "write_and_save_doc"),
            )
            if "save_path" not in task_params:
                root = os.path.dirname(os.path.dirname(__file__))
                task_params["save_path"] = os.path.join(root, "docs", "generated")

        payload = json.dumps(task_params, ensure_ascii=False)
        msg = make_msg(
            role="user",
            name="user",
            content=payload,
            metadata={"task_params": task_params, "target_ant": target_ant},
        )
        return await self._call_agent(self.colony.agent_map[target_ant], msg, track_speak_ts=True, append_history=True)

    async def _call_soldier(self, task_params: Any) -> Msg:
        if not isinstance(task_params, dict):
            task_params = {"task": str(task_params)}
        payload = json.dumps(task_params, ensure_ascii=False)
        msg = make_msg(
            role="user",
            name="user",
            content=payload,
            metadata={"task_type": "tool_create", **task_params},
        )
        return await self._call_agent(self.colony.soldier, msg, track_speak_ts=True, append_history=True)

    async def _call_agent(self, agent: Any, msg: Msg | None, *, track_speak_ts: bool, append_history: bool) -> Msg:
        name = getattr(agent, "name", "")
        if name and name in self._agent_state:
            self._agent_state[name].busy = True
        if self._enable_ui_events and name:
            await self._ui_event("agent_status", agent_name=name, status="busy")
        if name:
            log_event(event_type="agent_status", agent=name, payload={"status": "busy"})
        try:
            async with self._call_sem:
                reply: Msg = await agent(msg)
        except Exception as e:
            tb = traceback.format_exc()
            if self._enable_ui_events and name:
                await self._ui_event("agent_status", agent_name=name, status="idle")
                await self._ui_event("error", agent_name=name, error=str(e), traceback=tb)
            await self._broadcast_step(f"{name or '智能体'}执行失败：{e}")
            if name:
                log_event(event_type="error", agent=name, payload={"error": str(e), "traceback": tb}, level="ERROR")
            if name and name in self._agent_state:
                self._agent_state[name].busy = False
            return Msg(name="系统", role="assistant", content=f"{name or '智能体'}执行失败：{e}", metadata={"error": True})
        if name and track_speak_ts:
            self._last_speak_ts[name] = time.time()
        if append_history:
            await self._append_history(reply)
        if self._enable_ui_events and name:
            await self._ui_event("agent_status", agent_name=name, status="idle")
        if name:
            log_event(event_type="agent_status", agent=name, payload={"status": "idle"})
        if name and name in self._agent_state:
            self._agent_state[name].busy = False
        return reply

    def _start_heartbeats(self) -> list[asyncio.Task]:
        tasks: list[asyncio.Task] = []
        for role_key, agent in _iter_role_agents(self.colony):
            cfg = self._heartbeat_cfg.get(role_key) or {}
            if not bool(cfg.get("enabled", True)):
                continue
            t = asyncio.create_task(self._heartbeat_loop(role_key, agent, cfg))
            self._heartbeat_tasks_by_role[role_key] = t
            t.add_done_callback(lambda done, rk=role_key, ag=agent, c=cfg: self._on_heartbeat_done(done, rk, ag, c))
            tasks.append(t)
        return tasks

    async def _stop_heartbeats(self) -> None:
        for t in self._heartbeat_tasks:
            t.cancel()
        if self._heartbeat_tasks:
            await asyncio.gather(*self._heartbeat_tasks, return_exceptions=True)
        self._heartbeat_tasks = []
        self._heartbeat_tasks_by_role = {}

    def _on_heartbeat_done(self, task: asyncio.Task, role_key: str, agent: Any, cfg: dict[str, Any]) -> None:
        if self._stop_event.is_set():
            return
        if task.cancelled():
            return
        err = task.exception()
        if err is None:
            log_event(event_type="heartbeat_task_done", agent=str(getattr(agent, "name", role_key)), payload={"role_key": role_key})
            return
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        log_event(
            event_type="heartbeat_task_crashed",
            agent=str(getattr(agent, "name", role_key)),
            payload={"role_key": role_key, "error": str(err), "traceback": tb},
            level="ERROR",
        )
        attempt = int(self._heartbeat_restart_attempts.get(role_key, 0)) + 1
        self._heartbeat_restart_attempts[role_key] = attempt
        backoff_s = min(30.0, 2.0 * float(attempt))
        loop = asyncio.get_running_loop()
        loop.create_task(self._restart_heartbeat_after(role_key=role_key, agent=agent, cfg=cfg, delay_s=backoff_s))

    async def _restart_heartbeat_after(self, *, role_key: str, agent: Any, cfg: dict[str, Any], delay_s: float) -> None:
        await asyncio.sleep(max(0.5, float(delay_s)))
        if self._stop_event.is_set():
            return
        t = asyncio.create_task(self._heartbeat_loop(role_key, agent, cfg))
        self._heartbeat_tasks_by_role[role_key] = t
        t.add_done_callback(lambda done, rk=role_key, ag=agent, c=cfg: self._on_heartbeat_done(done, rk, ag, c))
        self._heartbeat_tasks.append(t)
        log_event(event_type="heartbeat_task_restarted", agent=str(getattr(agent, "name", role_key)), payload={"role_key": role_key, "delay_s": float(delay_s), "attempt": int(self._heartbeat_restart_attempts.get(role_key, 0))})

    async def _heartbeat_loop(self, role_key: str, agent: Any, cfg: dict[str, Any]) -> None:
        interval_s = float(cfg.get("interval_s", 5.0))
        jitter_s = float(cfg.get("jitter_s", 0.6))
        idle_no_increment_s = _get_float_from_env_or_cfg("ANT_TOPIC_IDLE_S", cfg, "idle_no_increment_s", 60.0)
        topic_cooldown_s = float(cfg.get("topic_cooldown_s", 180.0))
        topic_active_s = float(cfg.get("topic_active_s", 300.0))
        topic_decision_min_gap_s = float(cfg.get("topic_decision_min_gap_s", 30.0))
        topic_turn_interval_s = float(cfg.get("topic_turn_interval_s", 4.0))
        history_window_n = int(cfg.get("history_window_n", 30))
        first = True

        while not self._stop_event.is_set():
            try:
                if first:
                    first = False
                    sleep_s = random.uniform(2.0, 5.0)
                else:
                    sleep_s = max(1.0, interval_s + random.uniform(-jitter_s, jitter_s))
                await asyncio.sleep(sleep_s)

                now = time.time()
                name = getattr(agent, "name", role_key)
                self._last_heartbeat_ts_by_role[role_key] = now

                await self._sync_agent_memory(agent=agent, agent_name=str(name))

                if self._enable_ui_events and name:
                    await self._ui_event("heartbeat", agent_name=name, role_key=role_key)
                if name:
                    log_event(event_type="heartbeat", agent=str(name), payload={"role_key": role_key})

                self._topic_turn_watchdog(now=now)

                state = self._agent_state.get(str(name))
                if state is None:
                    continue
                if float(idle_no_increment_s) > 0 and state.last_increment_ts and now - state.last_increment_ts < idle_no_increment_s:
                    if role_key == self._topic_turn_role_key:
                        self._log_topic_gate(
                            state=state,
                            agent_name=str(name),
                            role_key=role_key,
                            reason="idle_not_reached",
                            extra={"since_s": now - float(state.last_increment_ts), "threshold_s": float(idle_no_increment_s)},
                        )
                    continue

                active_tag = self._active_topic_tag if (self._active_topic_tag and now - self._active_topic_ts <= topic_active_s) else None
                await self._maybe_post_topic(
                    agent=agent,
                    role_key=role_key,
                    agent_name=str(name),
                    active_topic_tag=active_tag,
                    topic_cooldown_s=topic_cooldown_s,
                    topic_decision_min_gap_s=topic_decision_min_gap_s,
                    topic_turn_interval_s=topic_turn_interval_s,
                    history_window_n=history_window_n,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                tb = traceback.format_exc()
                log_event(
                    event_type="heartbeat_tick_error",
                    agent=str(getattr(agent, "name", role_key)),
                    payload={"role_key": role_key, "error": str(e), "traceback": tb},
                    level="ERROR",
                )
                await asyncio.sleep(1.0)

    def _topic_turn_watchdog(self, *, now: float) -> None:
        turn = str(self._topic_turn_role_key or "")
        last = float(self._last_heartbeat_ts_by_role.get(turn, 0.0) or 0.0)
        if not last:
            return
        if now - last < float(self._topic_turn_stall_s):
            return
        self._advance_topic_turn()
        self._topic_turn_next_ts = 0.0
        log_event(event_type="topic_gate", agent="system", payload={"reason": "turn_stall", "turn_role_key": turn, "stalled_s": now - last})

    async def _planning_round(self, *, target_ant: str, task_params: Any) -> Msg:
        if not isinstance(task_params, dict):
            task_params = {"task": str(task_params)}

        payload = json.dumps(task_params, ensure_ascii=False)
        prompt = (
            "蚁王准备派发任务，请你先做规划与提问（不要执行工具）。\n"
            f"目标工蚁：{target_ant}\n"
            f"任务参数：{payload}\n"
            "请用两段输出：\n"
            "1）你需要的关键信息/约束（若无则写“无”）\n"
            "2）你的执行计划或风险点（尽量具体）"
        )

        replies: list[Msg] = []
        for role_key, agent in _iter_role_agents(self.colony):
            if role_key == "king":
                continue
            planning_msg = make_msg(
                role="user",
                name="system",
                content=prompt,
                metadata={"planning": True, "target_ant": target_ant},
            )
            replies.append(await self._call_agent(agent, planning_msg, track_speak_ts=False, append_history=False))

        summary = "\n\n".join([f"{r.name}：{msg_to_text(r)}" for r in replies])
        return make_msg(
            role="user",
            name="system",
            content="已收到各智能体规划/疑问，请根据群聊内容更新下一步调度JSON。\n\n" + summary,
            metadata={"planning_summary": True, "target_ant": target_ant},
        )

    async def _ensure_tool_ready(self, *, worker_agent: Any, worker_type: str, tool_name: str) -> None:
        if not self._hub:
            return
        if hasattr(worker_agent, "toolkit") and tool_name in getattr(worker_agent.toolkit, "tools", {}):
            return

        tool_missing_msg = make_msg(
            role="assistant",
            name=getattr(worker_agent, "name", worker_type),
            content=f"缺少工具：{tool_name}",
            metadata={"tool_missing": True, "tool_name": tool_name, "worker_type": worker_type},
        )
        await self._hub.broadcast(tool_missing_msg)
        await self._ui_emit_group_msg(tool_missing_msg)
        await self._append_history(tool_missing_msg)

        king_msg: Msg = await self._call_agent(self.colony.king, tool_missing_msg, track_speak_ts=True, append_history=True)
        cmd = extract_first_json_obj(msg_to_text(king_msg)) or {}
        params = cmd.get("task_params") if isinstance(cmd, dict) else None
        if not isinstance(params, dict):
            params = {"worker_type": worker_type, "tool_name": tool_name, "skill_desc": "自动补齐缺失工具。"}

        await self._call_soldier(params)

        if hasattr(worker_agent, "toolkit"):
            from services.skill_loader import load_skills

            load_skills(worker_agent.toolkit)

    async def _append_history(self, msg: Msg) -> None:
        async with self._history_lock:
            self._chat_history.append(msg)
            if len(self._chat_history) > self._history_max_len:
                dropped = len(self._chat_history) - self._history_max_len
                self._chat_history = self._chat_history[dropped:]
                for st in self._agent_state.values():
                    st.last_seen_idx = max(0, st.last_seen_idx - dropped)

        text = msg_to_text(msg).strip()
        tag = extract_first_topic_tag(text)
        if tag:
            self._active_topic_tag = tag
            self._active_topic_ts = time.time()

    async def _sync_agent_memory(self, *, agent: Any, agent_name: str) -> None:
        agent_name = (agent_name or "").strip()
        if not agent_name:
            return
        state = self._agent_state.get(agent_name)
        if state is None:
            state = AgentSyncState(last_seen_idx=0, last_increment_ts=time.time())
            self._agent_state[agent_name] = state

        mem = getattr(agent, "long_term_memory", None)
        if mem is None or not hasattr(mem, "record"):
            return

        async with self._history_lock:
            start = int(state.last_seen_idx)
            delta = list(self._chat_history[start:])
            end = len(self._chat_history)

        if not delta:
            return

        try:
            await mem.record(delta)
            state.last_seen_idx = end
            state.last_increment_ts = time.time()
            log_event(event_type="memory_sync", agent=agent_name, payload={"delta": len(delta), "end": end})
        except Exception as e:
            log_event(event_type="memory_sync_error", agent=agent_name, payload={"error": str(e)}, level="ERROR")

    async def _maybe_post_topic(
        self,
        *,
        agent: Any,
        role_key: str,
        agent_name: str,
        active_topic_tag: str | None,
        topic_cooldown_s: float,
        topic_decision_min_gap_s: float,
        topic_turn_interval_s: float,
        history_window_n: int,
    ) -> None:
        if not self._hub:
            return
        agent_name = (agent_name or "").strip()
        if not agent_name:
            return
        state = self._agent_state.get(agent_name)
        if state is None:
            return
        now = time.time()
        if now < self._topic_turn_next_ts:
            if role_key == self._topic_turn_role_key:
                self._log_topic_gate(
                    state=state,
                    agent_name=agent_name,
                    role_key=role_key,
                    reason="turn_wait",
                    extra={"wait_s": float(self._topic_turn_next_ts - now)},
                )
            return
        if role_key != self._topic_turn_role_key:
            if role_key == "king":
                self._log_topic_gate(
                    state=state,
                    agent_name=agent_name,
                    role_key=role_key,
                    reason="turn_not_match",
                    extra={"turn_role_key": str(self._topic_turn_role_key)},
                )
            return
        if state.last_topic_decision_ts and time.time() - state.last_topic_decision_ts < float(topic_decision_min_gap_s):
            self._log_topic_gate(
                state=state,
                agent_name=agent_name,
                role_key=role_key,
                reason="decision_gap",
                extra={"min_gap_s": float(topic_decision_min_gap_s), "since_s": now - float(state.last_topic_decision_ts)},
            )
            return

        want_initiate = active_topic_tag is None
        if want_initiate:
            if now - self._last_topic_initiated_ts < topic_cooldown_s:
                self._log_topic_gate(
                    state=state,
                    agent_name=agent_name,
                    role_key=role_key,
                    reason="cooldown",
                    extra={"cooldown_s": float(topic_cooldown_s), "since_s": now - float(self._last_topic_initiated_ts)},
                )
                self._advance_topic_turn()
                self._topic_turn_next_ts = now + float(topic_turn_interval_s)
                return
            async with self._topic_lock:
                if now < self._topic_turn_next_ts or role_key != self._topic_turn_role_key:
                    return
                self._topic_turn_next_ts = now + float(topic_turn_interval_s)
                if state.busy:
                    self._log_topic_gate(state=state, agent_name=agent_name, role_key=role_key, reason="busy", extra={})
                    self._advance_topic_turn()
                    return
                await self._run_topic_decision(
                    agent=agent,
                    role_key=role_key,
                    agent_name=agent_name,
                    active_topic_tag=None,
                    history_window_n=history_window_n,
                )
            return

        async with self._topic_lock:
            if now < self._topic_turn_next_ts or role_key != self._topic_turn_role_key:
                return
            self._topic_turn_next_ts = now + float(topic_turn_interval_s)
            if state.busy:
                self._log_topic_gate(state=state, agent_name=agent_name, role_key=role_key, reason="busy", extra={})
                self._advance_topic_turn()
                return
            await self._run_topic_decision(
                agent=agent,
                role_key=role_key,
                agent_name=agent_name,
                active_topic_tag=active_topic_tag,
                history_window_n=history_window_n,
            )

    async def _run_topic_decision(
        self,
        *,
        agent: Any,
        role_key: str,
        agent_name: str,
        active_topic_tag: str | None,
        history_window_n: int,
    ) -> None:
        if not self._hub:
            return
        state = self._agent_state.get(agent_name)
        if state is None:
            return

        async with self._history_lock:
            window = self._chat_history[-max(1, int(history_window_n)) :]
        window_text = "\n".join([f"{m.name}：{msg_to_text(m).strip()}" for m in window if msg_to_text(m).strip()])

        mem = getattr(agent, "long_term_memory", None)
        mem_hint = ""
        if mem is not None and hasattr(mem, "retrieve"):
            try:
                query = Msg(name="system", role="user", content=f"最近群聊窗口：\n{window_text}\n\n请回忆与你最相关的要点。")
                mem_hint = str(await mem.retrieve(query, limit=5) or "").strip()
            except Exception:
                mem_hint = ""

        schema = (
            "你需要做一次群聊话题决策，只输出一个 JSON 对象，禁止输出其它内容。\n"
            "字段：\n"
            "action: init_topic | contribute | silent\n"
            "topic_tag: 话题标签，格式必须是 #标签\n"
            "message: 你要发送到群聊的文本，第一行必须包含 topic_tag\n"
            "要求：\n"
            "1) 有 active_topic_tag 时：可以 contribute 或 silent；只有在能产生新增价值或明确回应时才 contribute。\n"
            "2) contribute 必须满足：明确回应 recent_chat 中最近一条他人发言（点名或引用关键短句），补充新观点/反驳/举例/推演，结尾提出一个追问或反问。\n"
            "3) 找不到可回应点或无法新增价值：必须 silent。\n"
            "4) 无 active_topic_tag 时：可 init_topic 或 silent；init_topic 必须给出可讨论的具体问题。\n"
            "5) 不要执行工具，不要编造外部动作结果。\n"
        )
        ctx = (
            f"role_key={role_key}\n"
            f"agent_name={agent_name}\n"
            f"active_topic_tag={active_topic_tag or ''}\n"
            f"recent_chat=\n{window_text}\n\n"
            f"your_memory=\n{mem_hint}\n"
        )
        raw = await self._call_topic_model(agent=agent, user_content=schema + "\n\n" + ctx)
        cmd = extract_first_json_obj(raw) or {}
        action = str(cmd.get("action") or "").strip()
        topic_tag = str(cmd.get("topic_tag") or "").strip()
        message = str(cmd.get("message") or "").strip()
        state.last_topic_decision_ts = time.time()
        log_event(
            event_type="topic_decision",
            agent=agent_name,
            payload={
                "action": action,
                "topic_tag": topic_tag,
                "has_message": bool(message),
                "active_topic_tag": active_topic_tag or "",
                "raw_len": len(raw or ""),
                "parsed": bool(cmd),
                "streaming": bool(getattr(getattr(agent, "model", None), "stream", False)),
            },
        )

        if action not in {"init_topic", "contribute"}:
            self._advance_topic_turn()
            return

        if not topic_tag:
            topic_tag = active_topic_tag or ""
        if not topic_tag.startswith("#"):
            topic_tag = f"#{topic_tag}" if topic_tag else ""
        if not topic_tag or not is_valid_topic_tag(topic_tag):
            self._advance_topic_turn()
            return

        if not message:
            self._advance_topic_turn()
            return
        if topic_tag not in message.splitlines()[0]:
            message = topic_tag + "\n" + message

        if action == "init_topic":
            self._active_topic_tag = topic_tag
            self._active_topic_ts = time.time()
            self._last_topic_initiated_ts = time.time()

        self._advance_topic_turn()

        post = Msg(
            name=agent_name,
            role="assistant",
            content=message,
            metadata={"topic_tag": topic_tag, "topic_action": action, "role_key": role_key},
        )
        await self._hub.broadcast(post)
        await self._ui_emit_group_msg(post)
        await self._append_history(post)
        log_event(event_type="topic_posted", agent=agent_name, payload={"topic_tag": topic_tag, "action": action})

    async def _call_topic_model(self, *, agent: Any, user_content: str) -> str:
        model = getattr(agent, "model", None)
        sys_prompt = str(getattr(agent, "sys_prompt", "") or "").strip()
        if model is None or not callable(model):
            return ""
        messages: list[dict[str, str]] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.append({"role": "user", "content": str(user_content or "")})
        topic_model = model
        try:
            from services.glm_chat_model import GLMChatModel

            if isinstance(model, GLMChatModel):
                topic_model = GLMChatModel(
                    model_name=model.model_name,
                    api_key=model.api_key,
                    base_url=model.base_url,
                    stream=False,
                    timeout_s=model.timeout_s,
                    include_thinking=True,
                    generate_kwargs=getattr(model, "generate_kwargs", None) or {},
                )
        except Exception:
            topic_model = model
        try:
            resp = await topic_model(messages=messages, temperature=0.2, max_tokens=512)
        except Exception as e:
            log_event(event_type="topic_model_error", agent=str(getattr(agent, "name", "") or ""), payload={"error": str(e)}, level="ERROR")
            return ""

        def _extract_text(obj: Any) -> str:
            content = getattr(obj, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(str(b.get("text") or ""))
                    else:
                        t = getattr(b, "text", None)
                        if t is not None:
                            parts.append(str(t))
                        th = getattr(b, "thinking", None)
                        if th is not None:
                            parts.append(str(th))
                return "".join(parts)
            return str(obj)

        def _is_async_iter(obj: Any) -> bool:
            try:
                object.__getattribute__(obj, "__aiter__")
                return True
            except AttributeError:
                return False
            except Exception:
                return False

        if _is_async_iter(resp):
            chunks: list[str] = []
            try:
                async for chunk in resp:
                    chunks.append(_extract_text(chunk))
            except Exception as e:
                log_event(
                    event_type="topic_model_error",
                    agent=str(getattr(agent, "name", "") or ""),
                    payload={"error": str(e), "stage": "stream_collect"},
                    level="ERROR",
                )
                return ""
            return "".join(chunks).strip()

        return _extract_text(resp).strip()

    def _advance_topic_turn(self) -> None:
        try:
            idx = _TOPIC_TURN_ORDER.index(self._topic_turn_role_key)
        except ValueError:
            idx = 0
        nxt = _TOPIC_TURN_ORDER[(idx + 1) % len(_TOPIC_TURN_ORDER)]
        self._topic_turn_role_key = nxt

    def _log_topic_gate(self, *, state: AgentSyncState, agent_name: str, role_key: str, reason: str, extra: dict[str, Any]) -> None:
        now = time.time()
        if state.last_topic_gate_ts and now - state.last_topic_gate_ts < 15.0:
            return
        state.last_topic_gate_ts = now
        payload = {"reason": str(reason), "role_key": str(role_key), "turn_role_key": str(self._topic_turn_role_key), **(extra or {})}
        log_event(event_type="topic_gate", agent=str(agent_name), payload=payload)

    async def _ui_event(self, ui_event: str, **metadata: Any) -> None:
        if not self._enable_ui_events:
            return
        reporter = getattr(self.colony, "king", None)
        if reporter is None:
            return
        msg = Msg(
            name="ui",
            role="system",
            content="",
            metadata={
                "ui_event": ui_event,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                **metadata,
            },
        )
        await reporter.print(msg)

    async def _broadcast_step(self, text: str) -> None:
        reporter = getattr(self.colony, "king", None)
        if reporter is None:
            return
        await reporter.print(Msg(name=getattr(reporter, "name", "蚁王"), role="assistant", content=text))

    async def _ui_emit_group_msg(self, msg: Msg) -> None:
        if not self._enable_ui_events:
            return
        reporter = getattr(self.colony, "king", None)
        if reporter is None:
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


def _get_float_from_env_or_cfg(env_key: str, cfg: dict[str, Any], cfg_key: str, default: float) -> float:
    raw_env = os.getenv(env_key, "").strip()
    if raw_env:
        try:
            return float(raw_env)
        except Exception:
            return float(default)
    try:
        return float(cfg.get(cfg_key, default))
    except Exception:
        return float(default)


def _iter_role_agents(colony: Any) -> list[tuple[str, Any]]:
    return [
        ("king", colony.king),
        ("soldier", colony.soldier),
        ("emotion_worker", colony.emotion_worker),
        ("browser_worker", colony.browser_worker),
        ("doc_worker", colony.doc_worker),
    ]


def _load_heartbeat_configs() -> dict[str, Any]:
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "configs", "agent_configs.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg: dict[str, Any] = {}
    for role_key, role_cfg in (data or {}).items():
        if isinstance(role_cfg, dict) and "heartbeat" in role_cfg and isinstance(role_cfg["heartbeat"], dict):
            cfg[role_key] = role_cfg["heartbeat"]
    return cfg


def should_send_heartbeat(
    *,
    now: float,
    last_user_ts: float,
    last_spoke_ts: float,
    idle_after_s: float,
    min_gap_s: float,
) -> bool:
    if last_user_ts and now - last_user_ts < idle_after_s:
        return False
    if last_spoke_ts and now - last_spoke_ts < min_gap_s:
        return False
    return True
