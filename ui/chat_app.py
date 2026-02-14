"""
Tkinter UI 入口。

启动后台异步桥接（AsyncColonyBridge）运行常驻 colony runtime，并在 Tk 主线程中渲染群聊消息与 agent 状态卡片。

本文件关注两件事：
1）Tk 主线程：负责窗口/控件生命周期与渲染；通过 `root.after(...)` 周期性轮询数据中心并刷新界面。
2）后台运行时：由 `AsyncColonyBridge` 在后台线程启动 asyncio event loop，产出消息（群聊文本、状态、心跳等）并交给数据中心统一加工。

关键数据通道：
- UI -> runtime：`on_send()` 调 `bridge.submit_user_text()`，跨线程投递到后台 asyncio loop。
- runtime -> UI：后台把 Msg/事件推入数据中心；UI 通过 `pump_events()` 拉取标准事件并渲染。
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from dotenv import load_dotenv

from ui.async_bridge import AsyncColonyBridge
from ui.avatar_store import AvatarStore, default_avatar_map, resolve_avatar_key
from ui.chat_dialog import ChatDialog
from ui.data_center import DataCenter, DataEvent


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


def _stop_trace_enabled() -> bool:
    return _env_truthy("ANT_STOP_TRACE", False)


def _format_timestamp(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time())
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _print_stop_trace(*, t0: float, stage: str, detail: str | None = None) -> None:
    if not _stop_trace_enabled():
        return
    elapsed = time.perf_counter() - float(t0)
    msg = f"[STOP][{_format_timestamp()}][+{elapsed:.3f}s] {stage}"
    if detail:
        msg = f"{msg} | {detail}"
    print(msg, flush=True)


def _purge_queen_chat_logs(*, base_dir: str) -> int:
    data_dir = os.path.join(os.path.abspath(base_dir), "message", "chatdata")
    os.makedirs(data_dir, exist_ok=True)
    removed = 0
    for name in os.listdir(data_dir):
        if not name.endswith(".jsonl"):
            continue
        if not (name.startswith("user_queen_") or name.startswith("group_chat_")):
            continue
        path = os.path.join(data_dir, name)
        os.remove(path)
        removed += 1
    return removed


def main() -> None:
    """
    UI 主入口。

    UI 的“主循环”是 Tk 的 `mainloop()`；但界面内容的持续更新依赖 `pump_events()`：
    - `pump_events()` 通过 `root.after(80, pump_events)` 以固定频率运行在 Tk 主线程；
    - 每轮尽量把数据中心的事件取空，避免 UI 被事件堆积拖慢；
    - UI 只处理数据中心输出的少量标准事件，渲染逻辑保持简单可读。
    """
    ui_root_dir = os.path.dirname(__file__)
    project_root_dir = os.path.dirname(ui_root_dir)
    load_dotenv(os.path.join(project_root_dir, ".env"))
    _purge_queen_chat_logs(base_dir=project_root_dir)
    # 数据调度中心：统一接收后台动态消息 + 提供 UI 事件流与快照
    data_center = DataCenter(base_dir=project_root_dir)
    data_center.start()

    bridge = AsyncColonyBridge(data_center=data_center)
    bridge.start()

    # 资源路径与头像资源初始化：只在启动时做一次，避免渲染时频繁 IO
    avatar_dir = os.path.join(ui_root_dir, "resources", "animations")
    avatar_files = default_avatar_map()
    avatars = AvatarStore(avatar_dir, size=44)

    # 组装 UI：主对话窗体 + 群聊条目 + 各智能体条目（包含头像与状态）
    dialog = ChatDialog(title="蚂蚁桌宠群聊", queen_name=data_center.queen_name, data_center=data_center)
    close_trace_t0: float | None = None

    agent_names = list(data_center.agent_names or [])
    group_avatar = avatars.get(avatar_files["worker"])
    dialog.ensure_group_item(avatar=group_avatar, agent_count=len(agent_names))

    for agent_name in agent_names:
        key = resolve_avatar_key(agent_name)
        file_name = avatar_files.get(key, avatar_files["worker"])
        avatar = avatars.get(file_name)
        dialog.ensure_agent_item(name=agent_name, avatar=avatar)

    def on_send(text: str) -> None:
        # UI 侧先把用户消息追加到界面，再异步投递给后台 runtime
        # 这样就算后台模型响应慢，用户也能即时看到“已发送”的反馈
        dialog.add_user_message(text=text)
        data_center.push_ui_user_text(text=text)
        bridge.submit_user_text(text)

    dialog.set_on_send(on_send)

    def on_refresh_db() -> None:
        ok = bridge.refresh_vector_store()
        if not ok:
            dialog.update_system_message(text="后台运行时仍在初始化，暂无法刷新数据库连接。")
            return
        dialog.update_system_message(text="已发起向量数据库连接刷新（后台执行）。")

    dialog.set_on_refresh_db(on_refresh_db)

    def _get_avatar(agent_name: str) -> object:
        key = resolve_avatar_key(agent_name)
        file_name = avatar_files.get(key, avatar_files["worker"])
        return avatars.get(file_name)

    def _render_event(ev: DataEvent) -> None:
        t = ev.type
        p = ev.payload or {}

        if t == "system_message":
            msg_text = str(p.get("text") or "").strip()
            if msg_text:
                dialog.update_system_message(text=msg_text)
            return

        if t == "agent_status":
            agent_name = str(p.get("agent_name") or "")
            status = str(p.get("status") or "")
            dialog.ensure_agent_item(name=agent_name, avatar=_get_avatar(agent_name))
            dialog.update_agent_status(name=agent_name, status=status)
            return

        if t == "heartbeat":
            agent_name = str(p.get("agent_name") or "")
            dialog.ensure_agent_item(name=agent_name, avatar=_get_avatar(agent_name))
            dialog.update_agent_status(name=agent_name, heartbeat="")
            return

        if t == "agent_health":
            agent_name = str(p.get("agent_name") or "")
            status = str(p.get("status") or "")
            dialog.ensure_agent_item(name=agent_name, avatar=_get_avatar(agent_name))
            dialog.update_agent_status(name=agent_name, status=status)
            return

        if t == "user_reply_stream":
            agent_name = str(p.get("agent_name") or "蚂蚁")
            delta = str(p.get("delta") or "")
            if delta:
                dialog.add_user_reply_stream(name=agent_name, delta=delta, avatar=_get_avatar(agent_name))
            return

        if t == "user_message":
            msg_text = str(p.get("text") or "").strip()
            if msg_text:
                dialog.add_user_message(text=msg_text)
            return

        if t == "user_reply":
            agent_name = str(p.get("agent_name") or "蚂蚁")
            reply_text = str(p.get("text") or "").strip()
            if reply_text:
                dialog.add_user_reply(name=agent_name, text=reply_text, avatar=_get_avatar(agent_name))
            return

        if t == "error":
            dialog.status_var.set("异常")
            err = str(p.get("error") or "").strip()
            if err:
                dialog.update_system_message(text=err)
            return

        if t == "memory_warmup":
            status = str(p.get("status") or "").strip().lower()
            if status == "start":
                dialog.status_var.set("预热中")
                return
            if status == "ok":
                dialog.status_var.set("运行中")
                return
            if status in {"skip", "error"}:
                dialog.status_var.set("异常")
                return
            return

        if t == "group_message":
            agent_name = str(p.get("agent_name") or "assistant")
            msg_text = str(p.get("text") or "")
            if msg_text.strip():
                if agent_name == "系统":
                    dialog.update_system_message(text=msg_text)
                else:
                    dialog.add_group_message(name=agent_name, text=msg_text, avatar=_get_avatar(agent_name))
            return

        if t == "agent_message":
            agent_name = str(p.get("agent_name") or "assistant")
            msg_text = str(p.get("text") or "")
            if msg_text.strip():
                dialog.add_agent_message(name=agent_name, text=msg_text, avatar=_get_avatar(agent_name))
            return

    def pump_events() -> None:
        """
        UI 事件泵：从数据中心批量取出事件并渲染。

        重要约束：Tk 控件只能在主线程操作，所以后台线程只能把消息交给数据中心，
        UI 主线程通过轮询“标准事件流”更新控件。
        """
        events = data_center.poll_events()
        for ev in events:
            _render_event(ev)
        # 使用 Tk 定时器实现“刷新节拍”；80ms 更适合流式增量展示
        dialog.root.after(80, pump_events)

    def on_close() -> None:
        nonlocal close_trace_t0
        close_trace_t0 = time.perf_counter()
        _print_stop_trace(t0=close_trace_t0, stage="UI关闭开始")
        dialog.status_var.set("停止中")
        _print_stop_trace(t0=close_trace_t0, stage="UI销毁窗口 root.destroy 开始")
        dialog.root.destroy()
        _print_stop_trace(t0=close_trace_t0, stage="UI销毁窗口 root.destroy 结束")

    dialog.root.protocol("WM_DELETE_WINDOW", on_close)
    pump_events()
    try:
        dialog.run()
    finally:
        t0 = close_trace_t0 or time.perf_counter()
        _print_stop_trace(t0=t0, stage="UI退出主循环 mainloop 返回")
        _print_stop_trace(t0=t0, stage="UI调用 bridge.stop 开始")
        bridge.stop()
        _print_stop_trace(t0=t0, stage="UI调用 bridge.stop 结束")


if __name__ == "__main__":
    main()
