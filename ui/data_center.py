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
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from message.group_chat import MessageCenter
from services.role_config_store import RoleInfo, load_roles
from utils.agent_home_locator import get_agent_skill_dir, get_agent_tool_dir
from utils.skill_tool_catalog import CompositeToolArtifact, SkillArtifact, SkillToolCatalog, load_catalog


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


@dataclass(frozen=True)
class RoleProfile:
    role_key: str
    name: str
    max_iters: int
    heartbeat: dict[str, Any]
    sys_prompt: str
    tags: list[str]
    installed_skills: list[str]
    installed_tools: list[str]


@dataclass(frozen=True)
class CatalogCard:
    kind: Literal["skill", "tool"]
    key: str
    title: str
    summary: str
    interfaces_or_steps: int
    is_installed: bool


@dataclass(frozen=True)
class ToolLibrarySnapshot:
    mode: Literal["library", "role"]
    role_key: str
    type_filter: Literal["all", "skill", "tool"]
    query: str
    roles: list[RoleProfile]
    cards: list[CatalogCard]


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
        self.queen_name: str = ""
        self.roles: dict[str, RoleInfo] = {}
        self._roster_tags: dict[str, list[str]] = {}
        self._catalog: SkillToolCatalog | None = None
        self._skill_map: dict[str, SkillArtifact] = {}
        self._tool_map: dict[str, CompositeToolArtifact] = {}

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
        self.roles, self.role_to_name, self.agent_names = self._load_static_roles(base_dir=self.base_dir)
        self.queen_name = self.role_to_name["queen_sera"]
        self._roster_tags = self._load_ant_roster_tags(base_dir=self.base_dir)
        self._catalog = load_catalog(repo_root=self.base_dir)
        self._skill_map = {s.key: s for s in self._catalog.skills}
        self._tool_map = {t.key: t for t in self._catalog.tools}
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
            cb(ev)

    def push_ui_user_text(self, *, text: str) -> None:
        """
        UI 侧发送用户输入时调用，用于开启“蚁后回复流式聚合”窗口。

        说明：
        - 后台模型如果是流式输出，会多次打印同一条消息的递增文本；
        - 数据中心通过该信号，决定将蚁后的普通打印消息视作“用户回复流式片段”，并只输出增量。
        """

        if not isinstance(text, str):
            raise TypeError("text must be str")
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
                agent_name = name or self.queen_name
                events.append(DataEvent(type="user_reply", payload={"agent_name": agent_name, "text": text}))

        gc = self._message_center.load_group_chat_history(days_back=1)
        for it in gc:
            name = str(it.get("name") or "")
            content = it.get("content")
            if isinstance(content, str):
                text = content
            else:
                text = json.dumps(content, ensure_ascii=False)
            if not text.strip():
                continue
            events.append(DataEvent(type="group_message", payload={"agent_name": name, "text": text}))
            events.append(DataEvent(type="agent_message", payload={"agent_name": name, "text": text}))
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

        if self._stream_active and name == self.queen_name and role == "assistant":
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
                ms = float(cost_ms) if cost_ms is not None else None
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
            self._message_center.append_user_queen(role="assistant", name=agent_name, content=str(reply_text or "").strip())
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

    def _load_static_roles(self, *, base_dir: str) -> tuple[dict[str, RoleInfo], Dict[str, str], List[str]]:
        roles = load_roles(base_dir)
        role_order = ["queen_sera", "king_tru", "soldier_ares", "worker_light", "worker_nova", "worker_reed"]
        for k in role_order:
            if k not in roles:
                raise KeyError(f"缺少角色配置：{k}")
        role_to_name: Dict[str, str] = {k: str(roles[k].name) for k in role_order}
        agent_names: List[str] = [role_to_name[k] for k in role_order]
        return roles, role_to_name, agent_names

    def _load_ant_roster_tags(self, *, base_dir: str) -> dict[str, list[str]]:
        roster_path = os.path.join(base_dir, "agents", "ant_roster.jsonl")
        if not os.path.exists(roster_path):
            raise FileNotFoundError(f"未找到名册文件：{roster_path}")
        out: dict[str, list[str]] = {}
        with open(roster_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("ant_roster.jsonl 行格式不正确，应为 JSON 对象。")
                role_key = str(obj.get("role_key") or "").strip()
                if not role_key:
                    continue
                tags_raw = obj.get("tags") or []
                if tags_raw is None:
                    tags_raw = []
                if not isinstance(tags_raw, list):
                    raise ValueError(f"{role_key} 的 tags 字段格式不正确，应为数组。")
                tags = [str(t).strip() for t in tags_raw if str(t).strip()]
                out[role_key] = tags
        return out

    def list_installed_skills(self, *, role_key: str) -> set[str]:
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        skills_dir = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
        if not os.path.isdir(skills_dir):
            return set()
        return {name for name in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, name))}

    def list_installed_tools(self, *, role_key: str) -> set[str]:
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        tools_dir = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
        if not os.path.isdir(tools_dir):
            return set()
        return {name for name in os.listdir(tools_dir) if os.path.isdir(os.path.join(tools_dir, name))}

    def get_role_profiles(self) -> list[RoleProfile]:
        out: list[RoleProfile] = []
        for role_key, role in self.roles.items():
            skills = sorted(self.list_installed_skills(role_key=role_key))
            tools = sorted(self.list_installed_tools(role_key=role_key))
            tags = list(self._roster_tags.get(role_key) or [])
            out.append(
                RoleProfile(
                    role_key=str(role_key),
                    name=str(role.name),
                    max_iters=int(role.max_iters),
                    heartbeat=dict(role.heartbeat or {}),
                    sys_prompt=str(role.sys_prompt),
                    tags=tags,
                    installed_skills=skills,
                    installed_tools=tools,
                )
            )
        out.sort(key=lambda r: r.role_key)
        return out

    def _extract_summary(self, md: str) -> str:
        lines = (md or "").splitlines()
        for line in lines:
            s = str(line or "").strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            if s.startswith("-"):
                continue
            return s[:160]
        return ""

    def get_tool_library_snapshot(
        self,
        *,
        mode: Literal["library", "role"],
        role_key: str,
        type_filter: Literal["all", "skill", "tool"],
        query: str,
    ) -> ToolLibrarySnapshot:
        rk = str(role_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if rk not in self.roles:
            raise KeyError(f"unknown role_key: {rk}")
        q = str(query or "").strip().lower()
        installed_skills = self.list_installed_skills(role_key=rk)
        installed_tools = self.list_installed_tools(role_key=rk)

        cards: list[CatalogCard] = []
        if self._catalog is None:
            raise RuntimeError("catalog not loaded")

        if type_filter in {"all", "skill"}:
            for s in self._catalog.skills:
                is_installed = s.key in installed_skills
                if mode == "role" and not is_installed:
                    continue
                hay = f"{s.key} {s.title} {s.doc_markdown}".lower()
                if q and q not in hay:
                    continue
                cards.append(
                    CatalogCard(
                        kind="skill",
                        key=s.key,
                        title=s.title,
                        summary=self._extract_summary(s.doc_markdown),
                        interfaces_or_steps=len(s.interfaces),
                        is_installed=is_installed,
                    )
                )

        if type_filter in {"all", "tool"}:
            for t in self._catalog.tools:
                is_installed = t.key in installed_tools
                if mode == "role" and not is_installed:
                    continue
                hay = f"{t.key} {t.title} {t.doc_markdown}".lower()
                if q and q not in hay:
                    continue
                cards.append(
                    CatalogCard(
                        kind="tool",
                        key=t.key,
                        title=t.title,
                        summary=self._extract_summary(t.doc_markdown),
                        interfaces_or_steps=len(t.steps),
                        is_installed=is_installed,
                    )
                )

        cards.sort(key=lambda c: (c.kind, c.key.lower(), c.title))
        return ToolLibrarySnapshot(
            mode=mode,
            role_key=rk,
            type_filter=type_filter,
            query=str(query or ""),
            roles=self.get_role_profiles(),
            cards=cards,
        )

    def install_skill(self, *, role_key: str, skill_key: str) -> None:
        rk = str(role_key or "").strip()
        sk = str(skill_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if not sk:
            raise ValueError("skill_key is required")
        if rk not in self.roles:
            raise KeyError(f"unknown role_key: {rk}")
        art = self._skill_map.get(sk)
        if art is None:
            raise KeyError(f"unknown skill_key: {sk}")
        dest_root = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
        os.makedirs(dest_root, exist_ok=True)
        dest_dir = os.path.join(dest_root, sk)
        if os.path.exists(dest_dir):
            raise FileExistsError(dest_dir)
        shutil.copytree(os.path.dirname(art.script_path), dest_dir)

    def uninstall_skill(self, *, role_key: str, skill_key: str) -> None:
        rk = str(role_key or "").strip()
        sk = str(skill_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if not sk:
            raise ValueError("skill_key is required")
        if rk not in self.roles:
            raise KeyError(f"unknown role_key: {rk}")
        dest_root = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
        dest_dir = os.path.join(dest_root, sk)
        if not os.path.isdir(dest_dir):
            raise FileNotFoundError(dest_dir)
        shutil.rmtree(dest_dir)

    def install_tool(self, *, role_key: str, tool_key: str) -> None:
        rk = str(role_key or "").strip()
        tk = str(tool_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if not tk:
            raise ValueError("tool_key is required")
        if rk not in self.roles:
            raise KeyError(f"unknown role_key: {rk}")
        art = self._tool_map.get(tk)
        if art is None:
            raise KeyError(f"unknown tool_key: {tk}")
        dest_root = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
        os.makedirs(dest_root, exist_ok=True)
        dest_dir = os.path.join(dest_root, tk)
        if os.path.exists(dest_dir):
            raise FileExistsError(dest_dir)
        shutil.copytree(os.path.dirname(art.spec_path), dest_dir)

    def uninstall_tool(self, *, role_key: str, tool_key: str) -> None:
        rk = str(role_key or "").strip()
        tk = str(tool_key or "").strip()
        if not rk:
            raise ValueError("role_key is required")
        if not tk:
            raise ValueError("tool_key is required")
        if rk not in self.roles:
            raise KeyError(f"unknown role_key: {rk}")
        dest_root = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
        dest_dir = os.path.join(dest_root, tk)
        if not os.path.isdir(dest_dir):
            raise FileNotFoundError(dest_dir)
        shutil.rmtree(dest_dir)
