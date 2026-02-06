"""
Tkinter UI 入口。

启动后台异步桥接（AsyncColonyBridge）运行常驻 colony runtime，并在 Tk 主线程中渲染群聊消息与 agent 状态卡片。
"""

from __future__ import annotations

import os
import queue

from dotenv import load_dotenv

try:
    import yaml
except Exception:
    yaml = None

from ui.async_bridge import AsyncColonyBridge, UIMessage
from ui.avatar_store import AvatarStore, default_avatar_map, resolve_avatar_key
from ui.chat_dialog import ChatDialog


def _load_agent_names(base_dir: str) -> list[str]:
    if yaml is None:
        return ["蚁王", "兵蚁", "情感工蚁", "浏览器工蚁", "文档工蚁"]
    path = os.path.join(base_dir, "configs", "agent_configs.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return ["蚁王", "兵蚁", "情感工蚁", "浏览器工蚁", "文档工蚁"]

    role_order = ["king", "soldier", "emotion_worker", "browser_worker", "doc_worker"]
    names: list[str] = []
    for k in role_order:
        cfg = data.get(k) or {}
        n = str(cfg.get("name") or "").strip()
        if n:
            names.append(n)
    return names or ["蚁王", "兵蚁", "情感工蚁", "浏览器工蚁", "文档工蚁"]


def main() -> None:
    load_dotenv()
    msg_queue: "queue.Queue[UIMessage]" = queue.Queue()
    bridge = AsyncColonyBridge(ui_queue=msg_queue)
    bridge.start()

    root = os.path.dirname(__file__)
    base_dir = os.path.dirname(root)
    avatar_dir = os.path.join(root, "resources", "animations")
    avatar_files = default_avatar_map()
    avatars = AvatarStore(avatar_dir, size=44)

    dialog = ChatDialog(title="蚂蚁桌宠群聊")

    agent_names = _load_agent_names(base_dir)
    group_avatar = avatars.get(avatar_files["worker"])
    dialog.ensure_group_item(avatar=group_avatar, agent_count=len(agent_names))

    for agent_name in agent_names:
        key = resolve_avatar_key(agent_name)
        file_name = avatar_files.get(key, avatar_files["worker"])
        avatar = avatars.get(file_name)
        dialog.ensure_agent_item(name=agent_name, avatar=avatar)

    def on_send(text: str) -> None:
        dialog.add_user_message(text=text)
        bridge.submit_user_text(text)

    dialog.set_on_send(on_send)

    def pump_messages() -> None:
        try:
            while True:
                item = msg_queue.get_nowait()
                md = item.metadata or {}
                ui_event = str(md.get("ui_event") or "")
                if ui_event == "agent_status":
                    agent_name = str(md.get("agent_name") or "")
                    status_raw = str(md.get("status") or "")
                    status = "忙碌" if status_raw == "busy" else "空闲"
                    key = resolve_avatar_key(agent_name)
                    file_name = avatar_files.get(key, avatar_files["worker"])
                    avatar = avatars.get(file_name)
                    dialog.ensure_agent_item(name=agent_name, avatar=avatar)
                    dialog.update_agent_status(name=agent_name, status=status)
                    continue

                if ui_event == "heartbeat":
                    agent_name = str(md.get("agent_name") or "")
                    key = resolve_avatar_key(agent_name)
                    file_name = avatar_files.get(key, avatar_files["worker"])
                    avatar = avatars.get(file_name)
                    dialog.ensure_agent_item(name=agent_name, avatar=avatar)
                    dialog.update_agent_status(name=agent_name, heartbeat="")
                    continue

                if ui_event == "user_reply":
                    reply_text = str(md.get("text") or "").strip()
                    if reply_text:
                        ant_avatar = avatars.get(avatar_files["king"])
                        dialog.add_user_reply(text=reply_text, avatar=ant_avatar)
                    continue

                if ui_event == "error":
                    dialog.status_var.set("异常")
                    err = str(md.get("error") or item.text or "").strip()
                    if err:
                        dialog.add_group_message(name="系统", text=err, avatar=None)
                    continue

                name = item.name or "assistant"
                key = resolve_avatar_key(name)
                file_name = avatar_files.get(key, avatar_files["worker"])
                avatar = avatars.get(file_name)
                dialog.add_group_message(name=name, text=item.text, avatar=avatar)
                dialog.add_agent_message(name=name, text=item.text, avatar=avatar)
        except queue.Empty:
            pass
        except Exception as e:
            dialog.status_var.set("异常")
            dialog.add_group_message(name="系统", text=f"UI渲染异常：{e}", avatar=None)
        dialog.root.after(120, pump_messages)

    def on_close() -> None:
        dialog.status_var.set("停止中")
        bridge.stop()
        dialog.root.destroy()

    dialog.root.protocol("WM_DELETE_WINDOW", on_close)
    pump_messages()
    dialog.run()


if __name__ == "__main__":
    main()
