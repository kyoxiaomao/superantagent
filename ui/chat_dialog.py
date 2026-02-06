"""
Tkinter 对话框组件（群聊 + 单一回复）。

负责渲染用户输入、群聊消息、以及各 agent 的状态卡片（头像/忙碌状态/心跳）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ChatDialog:
    def __init__(self, *, title: str = "蚂蚁桌宠对话框") -> None:
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1220x620")

        self._on_send: Callable[[str], None] | None = None

        self.status_var = tk.StringVar(value="运行中")
        self._agent_rows: dict[str, str] = {}
        self._agent_state: dict[str, dict[str, object]] = {}
        self._view_frames: dict[str, ttk.Frame] = {}
        self._view_texts: dict[str, tk.Text] = {}
        self._view_header_vars: dict[str, dict[str, tk.StringVar]] = {}
        self._view_avatar_labels: dict[str, ttk.Label] = {}
        self._image_refs: dict[str, object] = {}
        self._current_view: str | None = None
        self.entry: ttk.Entry | None = None
        self._build()

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="状态").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=1, sticky="e")

        body = ttk.Frame(self.root)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew")

        sidebar = ttk.Frame(paned)
        content = ttk.Frame(paned)
        paned.add(sidebar, weight=1)
        paned.add(content, weight=4)

        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        ttk.Label(sidebar, text="列表").grid(row=0, column=0, sticky="w", pady=(0, 6))

        style = ttk.Style(self.root)
        style.configure("Ant.Treeview", rowheight=52)

        self.tree = ttk.Treeview(
            sidebar,
            columns=("status", "count"),
            show="tree headings",
            selectmode="browse",
            height=18,
            style="Ant.Treeview",
        )
        self.tree.heading("#0", text="名称")
        self.tree.heading("status", text="状态")
        self.tree.heading("count", text="计数")
        self.tree.column("#0", width=160, stretch=True, anchor="w")
        self.tree.column("status", width=60, stretch=False, anchor="center")
        self.tree.column("count", width=90, stretch=False, anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self._content_stack = ttk.Frame(content)
        self._content_stack.grid(row=0, column=0, sticky="nsew")
        self._content_stack.columnconfigure(0, weight=1)
        self._content_stack.rowconfigure(0, weight=1)

        self._ensure_group_view()

    def set_on_send(self, callback: Callable[[str], None]) -> None:
        self._on_send = callback

    def ensure_group_item(self, *, avatar: object | None = None, agent_count: int | None = None) -> None:
        iid = "group"
        if not self.tree.exists(iid):
            self.tree.insert("", "end", iid=iid, text="全员群聊", image=avatar, values=("", ""))
        if avatar is not None:
            self.tree.item(iid, image=avatar)
            self._image_refs["group"] = avatar
        if agent_count is not None:
            status, _ = self.tree.item(iid, "values") or ("", "")
            self.tree.item(iid, values=(status, f"{int(agent_count)}个agent"))
        if not self.tree.selection():
            self.tree.selection_set(iid)
            self._show_view("group")

    def ensure_agent_item(self, *, name: str, avatar: object | None = None) -> None:
        if not name:
            return
        iid = f"agent:{name}"
        self._agent_rows[name] = iid
        if not self.tree.exists(iid):
            self.tree.insert("", "end", iid=iid, text=name, image=avatar, values=("空闲", "#0"))
        if avatar is not None:
            self.tree.item(iid, image=avatar)
            self._image_refs[iid] = avatar
        self._ensure_agent_view(name=name)
        if avatar is not None:
            self._update_agent_view_avatar(name=name, avatar=avatar)
        if self._current_view is None:
            if self.tree.exists("group"):
                self.tree.selection_set("group")
                self._show_view("group")
            else:
                self.tree.selection_set(iid)
                self._show_view(iid)

    def update_agent_status(self, *, name: str, status: str | None = None, heartbeat: str | None = None) -> None:
        if not name:
            return
        self.ensure_agent_item(name=name, avatar=None)
        iid = self._agent_rows.get(name) or f"agent:{name}"
        st = self._agent_state.get(name)
        if st is None:
            st = {"status": "空闲", "count": 0}
            self._agent_state[name] = st
        if status is not None:
            st["status"] = status
        if heartbeat is not None:
            st["count"] = int(st.get("count") or 0) + 1

        self.tree.item(iid, values=(st["status"], f"#{st['count']}"))

        view_key = f"agent:{name}"
        header_vars = self._view_header_vars.get(view_key)
        if header_vars is not None:
            if status is not None:
                header_vars["status"].set(str(st["status"]))
            if heartbeat is not None:
                header_vars["count"].set(f"#{st['count']}")

    def add_user_message(self, *, text: str) -> None:
        self._ensure_king_view()
        widget = self._view_texts.get("agent:蚁王")
        if widget is None:
            return
        content = (text or "").rstrip()
        if not content:
            return
        widget.configure(state="normal")
        widget.insert("end", f"你：{content}\n\n")
        widget.configure(state="disabled")
        widget.see("end")

    def add_user_reply(self, *, text: str, avatar: object | None = None) -> None:
        self._ensure_king_view()
        widget = self._view_texts.get("agent:蚁王")
        if widget is None:
            return
        self._add_to_text(widget, name="蚁王", text=text, avatar=avatar)

    def add_group_message(self, *, name: str, text: str, avatar: object | None = None) -> None:
        self._add_to_text(self._view_texts["group"], name=name, text=text, avatar=avatar)

    def add_agent_message(self, *, name: str, text: str, avatar: object | None = None) -> None:
        if not name:
            return
        self._ensure_agent_view(name=name)
        widget = self._view_texts.get(f"agent:{name}")
        if widget is None:
            return
        self._add_to_text(widget, name=name, text=text, avatar=avatar)

    def _add_to_text(self, widget: tk.Text, *, name: str, text: str, avatar: object | None = None) -> None:
        content = (text or "").rstrip()
        if not content:
            return

        widget.configure(state="normal")
        if avatar is not None:
            widget.image_create("end", image=avatar)
            widget.insert("end", " ")
        widget.insert("end", f"{name}：{content}\n\n")
        widget.configure(state="disabled")
        widget.see("end")

    def _send_from_entry(self) -> None:
        if self.entry is None:
            return
        raw = self.entry.get()
        text = (raw or "").strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if self._on_send:
            self._on_send(text)

    def _ensure_group_view(self) -> None:
        if "group" in self._view_frames:
            return
        frame = ttk.Frame(self._content_stack)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        title = ttk.Label(frame, text="信息展示：全员群聊")
        title.grid(row=0, column=0, sticky="w", padx=6, pady=(6, 4))

        text_widget = tk.Text(frame, wrap="word", state="disabled")
        text_widget.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=6)
        text_widget.configure(yscrollcommand=scroll.set)

        self._view_frames["group"] = frame
        self._view_texts["group"] = text_widget

    def _ensure_agent_view(self, *, name: str) -> None:
        if not name:
            return
        view_key = f"agent:{name}"
        if view_key in self._view_frames:
            return

        frame = ttk.Frame(self._content_stack)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))

        avatar_label = ttk.Label(header)
        avatar_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self._view_avatar_labels[view_key] = avatar_label

        ttk.Label(header, text=name).grid(row=0, column=1, sticky="w")
        status_var = tk.StringVar(value=str((self._agent_state.get(name) or {}).get("status") or "空闲"))
        count_var = tk.StringVar(value=f"#{int((self._agent_state.get(name) or {}).get('count') or 0)}")

        ttk.Label(header, textvariable=status_var).grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(header, textvariable=count_var).grid(row=0, column=3, sticky="w", padx=(10, 0))

        text_widget = tk.Text(frame, wrap="word", state="disabled")
        text_widget.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=6)
        text_widget.configure(yscrollcommand=scroll.set)

        self._view_frames[view_key] = frame
        self._view_texts[view_key] = text_widget
        self._view_header_vars[view_key] = {"status": status_var, "count": count_var}

        if name == "蚁王":
            footer = ttk.Frame(frame)
            footer.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))
            footer.columnconfigure(0, weight=1)

            self.entry = ttk.Entry(footer)
            self.entry.grid(row=0, column=0, sticky="ew")
            self.entry.bind("<Return>", lambda _: self._send_from_entry())

            send_btn = ttk.Button(footer, text="发送", command=self._send_from_entry)
            send_btn.grid(row=0, column=1, padx=(8, 0))

        avatar = self._image_refs.get(self._agent_rows.get(name) or view_key)
        if avatar is not None:
            self._update_agent_view_avatar(name=name, avatar=avatar)

    def _ensure_king_view(self) -> None:
        self.ensure_agent_item(name="蚁王", avatar=None)

    def _update_agent_view_avatar(self, *, name: str, avatar: object | None = None) -> None:
        view_key = f"agent:{name}"
        avatar_label = self._view_avatar_labels.get(view_key)
        if avatar_label is None:
            return
        if avatar is None:
            iid = self._agent_rows.get(name) or view_key
            avatar = self._image_refs.get(iid)
        if avatar is None:
            return
        avatar_label.configure(image=avatar)
        self._image_refs[view_key] = avatar

    def _show_view(self, key: str) -> None:
        frame = self._view_frames.get(key)
        if frame is None:
            return
        frame.tkraise()
        self._current_view = key

    def _on_tree_select(self, _: object) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = str(sel[0])
        if iid == "group":
            self._show_view("group")
            return
        if iid.startswith("agent:"):
            name = iid.split("agent:", 1)[1]
            self._ensure_agent_view(name=name)
            self._show_view(iid)

    def run(self) -> None:
        self.root.mainloop()
