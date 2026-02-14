"""
群聊会话封装（面向多群聊扩展）。

现阶段 runtime 只使用“默认全员群聊”这一种会话；该模块把 MsgHub 的生命周期、群聊历史维护等
通用能力收敛起来，便于后续由蚁王按任务动态创建“任务群聊”并复用相同的群聊基础设施。
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable

from agentscope.message import Msg
from agentscope.pipeline import MsgHub

HistoryDropCallback = Callable[[int], None]


def resolve_min_post_interval_s(*, chat_id: str, participant_count: int) -> float:
    """
    计算群聊的“最小发言间隔”（秒）。

    约束：
    - 全员群聊（chat_id=main）：固定 10 秒（避免刷屏）
    - 其他群聊：按人数动态配置，人数越少间隔越短（通常意味着更重要、更聚焦）
    """

    cid = str(chat_id or "").strip() or "main"
    if cid == "main":
        return 10.0
    n = max(1, int(participant_count))
    return max(2.0, min(30.0, 2.0 * float(n)))


@dataclass
class GroupChat:
    chat_id: str
    participants: list[Any]
    hub: MsgHub
    history_max_len: int = 2000
    on_history_dropped: HistoryDropCallback | None = None
    persist_dir: str | None = None

    history: list[Msg] = field(default_factory=list)
    history_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    post_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_post_ts: float = 0.0
    busy: bool = False
    busy_agent_name: str = ""

    async def broadcast(self, msg: Msg) -> None:
        """向当前群聊广播一条消息。"""
        await self.hub.broadcast(msg)

    async def append_history(self, msg: Msg) -> int:
        """
        追加群聊历史，并在超出上限时裁剪。

        这里使用 del 原地裁剪，确保 history 的 list 对象身份不变，便于外部持有引用时保持一致。
        """
        dropped = 0
        async with self.history_lock:
            self.history.append(msg)
            if len(self.history) > int(self.history_max_len):
                dropped = len(self.history) - int(self.history_max_len)
                if dropped > 0:
                    del self.history[:dropped]

        if dropped and self.on_history_dropped is not None:
            try:
                self.on_history_dropped(int(dropped))
            except Exception:
                pass
        self._append_jsonl(msg)
        return int(dropped)

    async def get_window(self, n: int) -> list[Msg]:
        """获取最近 n 条群聊消息的快照（返回新列表，避免外部误改内部历史）。"""
        n = max(1, int(n))
        async with self.history_lock:
            return list(self.history[-n:])

    def _append_jsonl(self, msg: Msg) -> None:
        if not self.persist_dir:
            return
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            file_path = os.path.join(self.persist_dir, f"group_chat_{date_str}.jsonl")
            payload = self._msg_payload(chat_id=self.chat_id, msg=msg)
            line = self._safe_json_dumps(payload)
            with open(file_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        except Exception:
            return

    @staticmethod
    def _msg_payload(*, chat_id: str, msg: Msg) -> dict[str, Any]:
        try:
            base = msg.to_dict() if hasattr(msg, "to_dict") else {"msg": str(msg)}
        except Exception:
            base = {"msg": str(msg)}
        return {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chat_id": str(chat_id or ""),
            "name": str(getattr(msg, "name", "") or ""),
            "role": str(getattr(msg, "role", "") or ""),
            "content": base.get("content", base),
            "metadata": base.get("metadata", None),
        }

    @staticmethod
    def _safe_json_dumps(payload: dict[str, Any]) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return json.dumps({"raw": str(payload)}, ensure_ascii=False)

    @asynccontextmanager
    async def acquire_post(self, *, agent_name: str, min_interval_s: float | None = None) -> AsyncIterator[None]:
        """
        获取“发言闸门”：确保同一时刻同一群聊最多只有一个智能体在准备/执行发言。

        语义：
        - 进入 gate：群聊置 busy，并执行最小发言间隔节流（必要时等待）
        - 退出 gate：更新 last_post_ts，并解除 busy

        注意：
        - 这里不做任何 LLM 决策，也不负责“要不要发言”；只保证并发与节流。
        - 智能体自身 busy 状态应由调用方在 gate 外/内自行维护。
        """

        name = str(agent_name or "").strip()
        interval = float(min_interval_s) if min_interval_s is not None else resolve_min_post_interval_s(chat_id=self.chat_id, participant_count=len(self.participants))
        async with self.post_lock:
            self.busy = True
            self.busy_agent_name = name
            try:
                now = asyncio.get_running_loop().time()
                last = float(self.last_post_ts or 0.0)
                wait_s = max(0.0, interval - (now - last)) if last else 0.0
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                yield
                self.last_post_ts = asyncio.get_running_loop().time()
            finally:
                self.busy = False
                self.busy_agent_name = ""


@dataclass
class GroupChatRegistry:
    """
    多群聊的占位式注册表。

    目前仅用于预留接口：后续可以在运行时创建多个 chat，并在不同 chat 之间路由消息。
    """

    chats: dict[str, GroupChat] = field(default_factory=dict)

    def get(self, chat_id: str) -> GroupChat | None:
        return self.chats.get(str(chat_id))

    def set(self, chat: GroupChat) -> None:
        self.chats[str(chat.chat_id)] = chat

    def remove(self, chat_id: str) -> GroupChat | None:
        return self.chats.pop(str(chat_id), None)


@dataclass
class MessageCenter:
    base_dir: str

    def __post_init__(self) -> None:
        base = os.path.abspath(self.base_dir)
        self._data_dir = os.path.join(base, "message", "chatdata")
        os.makedirs(self._data_dir, exist_ok=True)

    def append_user_queen(self, *, role: str, name: str, content: str) -> None:
        payload = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": str(role or ""),
            "name": str(name or ""),
            "content": str(content or ""),
        }
        self._append_jsonl(self._user_queen_file_path(), payload)

    def load_user_queen_history(self, *, days_back: int = 1) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for date_str in self._iter_date_strs(days_back=days_back):
            items.extend(self._load_jsonl(self._user_queen_file_path(date_str)))
        return items

    def load_group_chat_history(self, *, days_back: int = 1) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for date_str in self._iter_date_strs(days_back=days_back):
            items.extend(self._load_jsonl(self._group_chat_file_path(date_str)))
        return items

    def _user_queen_file_path(self, date_str: str | None = None) -> str:
        date_str = date_str or datetime.now().strftime("%Y%m%d")
        return os.path.join(self._data_dir, f"user_queen_{date_str}.jsonl")

    def _group_chat_file_path(self, date_str: str | None = None) -> str:
        date_str = date_str or datetime.now().strftime("%Y%m%d")
        return os.path.join(self._data_dir, f"group_chat_{date_str}.jsonl")

    @staticmethod
    def _iter_date_strs(*, days_back: int) -> list[str]:
        days = max(0, int(days_back))
        today = datetime.now().date()
        out: list[str] = []
        for i in range(days, -1, -1):
            out.append((today - timedelta(days=i)).strftime("%Y%m%d"))
        return out

    def _append_jsonl(self, file_path: str, payload: dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            line = self._safe_json_dumps(payload)
            with open(file_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        except Exception:
            return

    def _load_jsonl(self, file_path: str) -> list[dict[str, Any]]:
        if not os.path.exists(file_path):
            return []
        items: list[dict[str, Any]] = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return items

    @staticmethod
    def _safe_json_dumps(payload: dict[str, Any]) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return json.dumps({"raw": str(payload)}, ensure_ascii=False)


@asynccontextmanager
async def open_group_chat(
    *,
    chat_id: str,
    participants: list[Any],
    history_max_len: int = 2000,
    on_history_dropped: HistoryDropCallback | None = None,
    persist_dir: str | None = None,
) -> AsyncIterator[GroupChat]:
    async with MsgHub(participants=participants) as hub:
        yield GroupChat(
            chat_id=str(chat_id),
            participants=list(participants),
            hub=hub,
            history_max_len=int(history_max_len),
            on_history_dropped=on_history_dropped,
            persist_dir=persist_dir,
        )
