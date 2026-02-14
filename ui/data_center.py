"""
数据调度中心（面向 UI/Web 的统一数据源）。

职责：
- 启动时采集静态数据（角色名称、头像映射、初始状态）；
- 接收后台运行时的动态事件（print 队列/自定义 ui_event）；
- 做去重与流式聚合，统一输出标准事件供 UI/Web 消费；
- 提供快照拉取接口，便于状态面板与初始化渲染。
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from message.group_chat import MessageCenter


@dataclass(frozen=True)
class DataEvent:
    """
    数据中心输出的标准事件。

    type 约定（当前实现用到的）：
    - static_ready：静态数据已准备（角色列表/映射）
    - agent_status：忙闲状态更新
    - heartbeat：心跳计数更新
    - agent_health：健康状态更新
    - user_reply_stream：用户回复的流式增量
    - user_reply：用户回复最终文本
    - group_message：群聊追加消息
    - agent_message：指定智能体视图追加消息
    - error：错误提示
    """

    type: str
    payload: Dict[str, Any]


@dataclass
class DataSnapshot:
    agent_status: Dict[str, Dict[str, Any]]
    last_error: str | None


class DataCenter:
    """
    数据调度中心。

    线程模型：
    - 后台线程（asyncio）调用 push_runtime_message/push_ui_user_text 投递“原始事件”；
    - UI 线程周期性 poll_events()，获得整理后的标准事件列表并渲染；
    - 订阅者 subscribe() 可用于未来 WebSocket 等推送场景（可选）。
    """

    def __init__(self, *, base_dir: str) -> None:
        self.base_dir = base_dir

        self.agent_names: List[str] = []
        self.role_to_name: Dict[str, str] = {}
        self.蚁后名 = "蚁后_瑟拉"

        self._subscribers: List[Callable[[DataEvent], None]] = []
        self._raw_queue: "queue.Queue[Tuple[str, Dict[str, Any]]]" = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot = DataSnapshot(agent_status={}, last_error=None)
        self._stream_active = False
        self._stream_key: str | None = None
        self._stream_last_text: str = ""
        self._recent_dedupe: Dict[str, float] = {}
        self._dedupe_ttl_s = 2.0
        self._startup_events: List[DataEvent] = []
        self._message_center = MessageCenter(base_dir=self.base_dir)

    def start(self) -> None:
        try:
            self.role_to_name, self.agent_names = self._load_static_agents(base_dir=self.base_dir)
        except Exception as e:
            err = f"加载静态智能体配置失败：{e}"
            with self._lock:
                self._snapshot.last_error = err
            self._emit(DataEvent(type="error", payload={"error": err}))
            raise
        self.蚁后名 = self.role_to_name["queen"]
        self._emit(DataEvent(type="static_ready", payload={"agent_names": list(self.agent_names), "role_to_name": dict(self.role_to_name)}))
        self._startup_events = self._load_history_events()

    def stop(self) -> None:
        self._stream_active = False
        self._stream_key = None
        self._stream_last_text = ""

    def subscribe(self, callback: Callable[[DataEvent], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def _emit(self, ev: DataEvent) -> None:
        subs: List[Callable[[DataEvent], None]] = []
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass

    def push_ui_user_text(self, *, text: str) -> None:
        """
        UI 侧发送用户输入时调用，用于开启“蚁后回复流式聚合”窗口。

        说明：
        - 后台模型如果是流式输出，会多次打印同一条消息的递增文本；
        - 数据中心通过该信号，决定将蚁后的普通打印消息视作“用户回复流式片段”，并只输出增量。
        """

        _ = text
        self._raw_queue.put(("ui_user_text", {"ts": time.time()}))

    def push_runtime_message(
        self,
        *,
        name: str,
        role: str,
        text: str,
        metadata: Optional[dict] = None,
        msg_id: Optional[str] = None,
        last: Optional[bool] = None,
    ) -> None:
        """
        后台线程投递原始消息。

        - msg_id/last 是为了支持流式聚合（如果后台能提供更好）；缺失也能工作，但去重会更弱。
        """

        self._raw_queue.put(
            (
                "runtime_msg",
                {
                    "name": name,
                    "role": role,
                    "text": text,
                    "metadata": metadata or None,
                    "msg_id": msg_id,
                    "last": last,
                    "ts": time.time(),
                },
            ),
        )

    def poll_events(self, *, max_items: int = 200) -> List[DataEvent]:
        """
        UI 线程拉取事件：将 raw_queue 中的原始消息加工为标准事件。
        """

        out: List[DataEvent] = []
        while self._startup_events and len(out) < int(max_items):
            ev = self._startup_events.pop(0)
            out.append(ev)
            self._emit(ev)
        if len(out) >= int(max_items):
            return out
        for _ in range(max(1, int(max_items))):
            try:
                kind, payload = self._raw_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "ui_user_text":
                self._stream_active = True
                self._stream_key = None
                self._stream_last_text = ""
                continue

            if kind != "runtime_msg":
                continue

            evs = self._process_runtime_msg(payload)
            for ev in evs:
                out.append(ev)
                self._emit(ev)
        return out

    def _load_history_events(self) -> List[DataEvent]:
        events: List[DataEvent] = []
        try:
            uq = self._message_center.load_user_queen_history(days_back=1)
            for it in uq:
                role = str(it.get("role") or "")
                name = str(it.get("name") or "")
                text = str(it.get("content") or "")
                if not text.strip():
                    continue
                if role == "user":
                    events.append(DataEvent(type="user_message", payload={"text": text}))
                else:
                    agent_name = name or self.蚁后名
                    events.append(DataEvent(type="user_reply", payload={"agent_name": agent_name, "text": text}))
        except Exception:
            pass
        try:
            gc = self._message_center.load_group_chat_history(days_back=1)
            for it in gc:
                name = str(it.get("name") or "")
                content = it.get("content")
                if isinstance(content, str):
                    text = content
                else:
                    try:
                        text = json.dumps(content, ensure_ascii=False)
                    except Exception:
                        text = str(content)
                if not text.strip():
                    continue
                events.append(DataEvent(type="group_message", payload={"agent_name": name, "text": text}))
                events.append(DataEvent(type="agent_message", payload={"agent_name": name, "text": text}))
        except Exception:
            pass
        return events

    def _process_runtime_msg(self, payload: Dict[str, Any]) -> List[DataEvent]:
        name = str(payload.get("name") or "")
        role = str(payload.get("role") or "")
        text = str(payload.get("text") or "")
        md = payload.get("metadata") or {}
        msg_id = payload.get("msg_id")
        last = payload.get("last")

        ui_event = str(md.get("ui_event") or "")
        if ui_event:
            return self._process_ui_event(ui_event=ui_event, text=text, md=md)

        if self._stream_active and name == self.蚁后名 and role == "assistant":
            key = str(msg_id or self._fallback_stream_key(name=name, role=role))
            delta, full = self._stream_delta(key=key, full_text=text)
            if delta:
                return [DataEvent(type="user_reply_stream", payload={"agent_name": name, "delta": delta, "full_text": full, "last": bool(last) if last is not None else None})]
            return []

        if not text.strip():
            return []
        if self._is_duplicate_message(name=name, role=role, text=text):
            return []

        return [
            DataEvent(type="group_message", payload={"agent_name": name, "text": text}),
            DataEvent(type="agent_message", payload={"agent_name": name, "text": text}),
        ]

    def _process_ui_event(self, *, ui_event: str, text: str, md: Dict[str, Any]) -> List[DataEvent]:
        if ui_event == "agent_status":
            agent_name = str(md.get("agent_name") or "")
            status_raw = str(md.get("status") or "")
            status = "忙碌" if status_raw == "busy" else "空闲"
            with self._lock:
                st = self._snapshot.agent_status.get(agent_name) or {}
                st["status"] = status
                st["count"] = int(st.get("count") or 0)
                self._snapshot.agent_status[agent_name] = st
            return [DataEvent(type="agent_status", payload={"agent_name": agent_name, "status": status})]

        if ui_event == "heartbeat":
            agent_name = str(md.get("agent_name") or "")
            with self._lock:
                st = self._snapshot.agent_status.get(agent_name) or {}
                st["count"] = int(st.get("count") or 0) + 1
                self._snapshot.agent_status[agent_name] = st
            return [DataEvent(type="heartbeat", payload={"agent_name": agent_name})]

        if ui_event == "agent_health":
            agent_name = str(md.get("agent_name") or "")
            unhealthy = bool(md.get("unhealthy"))
            status = "异常" if unhealthy else "空闲"
            with self._lock:
                st = self._snapshot.agent_status.get(agent_name) or {}
                st["status"] = status
                self._snapshot.agent_status[agent_name] = st
            return [DataEvent(type="agent_health", payload={"agent_name": agent_name, "status": status})]

        if ui_event == "error":
            err = str(md.get("error") or "") or text
            with self._lock:
                self._snapshot.last_error = err
            return [DataEvent(type="error", payload={"error": err})]

        if ui_event == "runtime_init":
            status = str(md.get("status") or "").strip().lower()
            info = str(md.get("info") or "").strip() or text
            if status == "ok":
                msg = "运行时已就绪。"
            else:
                msg = info or "后台运行时仍在初始化，请稍候。"
            return [DataEvent(type="group_message", payload={"agent_name": "系统", "text": msg})]

        if ui_event == "memory_warmup":
            status = str(md.get("status") or "").strip().lower()
            url = str(md.get("url") or "").strip()
            reason = str(md.get("reason") or "").strip().lower()
            detail = str(md.get("detail") or "").strip() or str(md.get("error") or "").strip() or text
            if status == "start":
                msg = "向量数据库预热中（后台执行，不影响界面）。" if not url else f"向量数据库预热中（{url}）"
            elif status == "ok":
                cost_ms = md.get("cost_ms")
                try:
                    ms = float(cost_ms) if cost_ms is not None else None
                except Exception:
                    ms = None
                msg = "向量数据库预热完成。" if ms is None else f"向量数据库预热完成，耗时 {ms:.0f}ms。"
                if url:
                    msg = f"{msg}（{url}）"
            elif status == "skip":
                if reason == "not_configured":
                    msg = "向量数据库未配置（configs/vector_server.yaml: url），已跳过预热。"
                elif reason == "service_not_running":
                    msg = f"未检测到本地向量库服务（{url}），已跳过预热。"
                else:
                    msg = "向量数据库未就绪，已跳过预热。"
                    if url:
                        msg = f"{msg}（{url}）"
                if detail:
                    msg = f"{msg} 详情：{detail}"
            elif status == "error":
                if reason == "not_configured":
                    msg = "向量数据库未配置（configs/vector_server.yaml: url），预热失败。"
                elif reason == "service_not_running":
                    msg = "向量数据库服务不可达，预热失败。"
                else:
                    msg = "向量数据库预热失败。"
                if url:
                    msg = f"{msg}（{url}）"
                if detail:
                    msg = f"{msg} 详情：{detail}"
            else:
                msg = str(text or "").strip() or "向量数据库预热状态更新。"
            ev = DataEvent(type="memory_warmup", payload={"status": status, "url": url, "reason": reason, "detail": detail, "message": msg})
            if status in {"skip", "error"}:
                with self._lock:
                    self._snapshot.last_error = msg
                return [ev, DataEvent(type="error", payload={"error": msg})]
            return [ev, DataEvent(type="group_message", payload={"agent_name": "系统", "text": msg})]

        if ui_event == "user_reply":
            agent_name = str(md.get("agent_name") or "蚂蚁")
            reply_text = str(md.get("text") or "") or text
            streamed = str(self._stream_last_text or "").strip()
            if streamed and len(streamed) > len(reply_text) + 20:
                reply_text = streamed
            try:
                self._message_center.append_user_queen(role="assistant", name=agent_name, content=str(reply_text or "").strip())
            except Exception:
                pass
            self._stream_active = False
            self._stream_key = None
            self._stream_last_text = ""
            return [DataEvent(type="user_reply", payload={"agent_name": agent_name, "text": reply_text})]

        return []

    def _is_duplicate_message(self, *, name: str, role: str, text: str) -> bool:
        now = time.time()
        key = f"{name}|{role}|{hash(text[:400])}"
        cutoff = now - self._dedupe_ttl_s
        drop_keys = [k for k, ts in self._recent_dedupe.items() if ts < cutoff]
        for k in drop_keys:
            self._recent_dedupe.pop(k, None)
        if key in self._recent_dedupe:
            return True
        self._recent_dedupe[key] = now
        return False

    def _fallback_stream_key(self, *, name: str, role: str) -> str:
        return f"{name}|{role}|{int(time.time() * 1000)}"

    def _stream_delta(self, *, key: str, full_text: str) -> tuple[str, str]:
        if self._stream_key != key:
            self._stream_key = key
            self._stream_last_text = ""

        prev = self._stream_last_text
        if full_text.startswith(prev):
            delta = full_text[len(prev) :]
        else:
            delta = full_text
        self._stream_last_text = full_text
        return delta, full_text

    def get_snapshot(self) -> DataSnapshot:
        with self._lock:
            return DataSnapshot(agent_status=dict(self._snapshot.agent_status), last_error=self._snapshot.last_error)

    def _load_static_agents(self, *, base_dir: str) -> tuple[Dict[str, str], List[str]]:
        """
        从配置加载用于 UI 展示的智能体名称列表。

        - 优先读取 configs/agent_configs.yaml 中各 role 的 `name`
        - 若读取失败则抛出异常
        """

        role_order = ["queen_sera", "king_tru", "soldier_ares", "worker_light", "worker_nova", "worker_reed"]
        path = os.path.join(base_dir, "configs", "agent_configs.yaml")
        if not os.path.exists(path):
            raise FileNotFoundError(f"未找到配置文件：{path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("agent_configs.yaml 内容格式不正确，应为字典结构")

        role_to_name: Dict[str, str] = {}
        names: List[str] = []
        for k in role_order:
            if k not in data or not isinstance(data.get(k), dict):
                raise KeyError(f"缺少角色配置：{k}")
            cfg = data.get(k) or {}
            n = str(cfg.get("name") or "").strip()
            if not n:
                raise ValueError(f"角色 {k} 缺少名称配置")
            role_to_name[k] = n
            names.append(role_to_name[k])
        return role_to_name, names
