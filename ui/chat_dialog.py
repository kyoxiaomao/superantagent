"""
Tkinter 对话框组件（群聊 + 单一回复）。

负责渲染用户输入、群聊消息、以及各 agent 的状态卡片（头像/忙碌状态/心跳）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ui.data_center import DataCenter
from ui.tool_library_panel import ToolLibraryPanel


class ChatDialog:
    def __init__(self, *, title: str = "蚂蚁桌宠对话框", queen_name: str, data_center: DataCenter) -> None:
        queen_name = str(queen_name or "").strip()
        if not queen_name:
            raise ValueError("queen_name is required")
        self.queen_name = queen_name
        self.data_center = data_center

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1220x620")

        self._on_send: Callable[[str], None] | None = None
        self._on_refresh_db: Callable[[], None] | None = None

        self.status_var = tk.StringVar(value="运行中")
        self.system_info_var = tk.StringVar(value="")
        self._agent_rows: dict[str, str] = {}
        self._agent_state: dict[str, dict[str, object]] = {}
        self._view_frames: dict[str, ttk.Frame] = {}
        self._view_texts: dict[str, tk.Text] = {}
        self._view_header_vars: dict[str, dict[str, tk.StringVar]] = {}
        self._view_avatar_labels: dict[str, ttk.Label] = {}
        self._image_refs: dict[str, object] = {}
        self._current_view: str | None = None
        self.entry: ttk.Entry | None = None
        # 用户回复的流式渲染状态：仅用于 UI 增量追加，避免重复插入整段文本
        self._reply_stream_active: dict[str, bool] = {}
        self._reply_stream_full_text: dict[str, str] = {}
        self.tag_var: tk.StringVar | None = None
        self.tag_dropdown: ttk.Combobox | None = None
        self.tag_options: list[str] = ["聊天", "任务", "话题", "请教", "总结", "计划", "提醒", "复盘", "决策", "闲聊", "记录", "待办"]
        self._build()

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self._notebook = ttk.Notebook(self.root)
        self._notebook.grid(row=0, column=0, sticky="nsew")
        self._tab_tool_library = ttk.Frame(self._notebook)
        self._tab_main = ttk.Frame(self._notebook)
        self._tab_db = ttk.Frame(self._notebook)
        
        self._notebook.add(self._tab_main, text="主界面")
        self._notebook.add(self._tab_db, text="数据库")
        self._notebook.add(self._tab_tool_library, text="工具库")
        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._tab_tool_library.columnconfigure(0, weight=1)
        self._tab_tool_library.rowconfigure(0, weight=1)
        tool_panel = ToolLibraryPanel(self._tab_tool_library, data_center=self.data_center)
        tool_panel.grid(row=0, column=0, sticky="nsew")

        self._tab_main.columnconfigure(0, weight=1)
        self._tab_main.rowconfigure(0, weight=0)
        self._tab_main.rowconfigure(1, weight=1)
        self._tab_main.rowconfigure(2, weight=0)

        header = ttk.Frame(self._tab_main)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="状态").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=1, sticky="e")
        ttk.Button(header, text="连接数据库", command=self._on_refresh_db_click).grid(row=0, column=2, sticky="e", padx=(10, 0))

        body = ttk.Frame(self._tab_main)
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

        sys_bar = ttk.Frame(self._tab_main)
        sys_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        sys_bar.columnconfigure(1, weight=1)
        ttk.Label(sys_bar, text="系统：").grid(row=0, column=0, sticky="w")
        ttk.Label(sys_bar, textvariable=self.system_info_var, anchor="w").grid(row=0, column=1, sticky="ew")

        self._tab_db.columnconfigure(0, weight=1)
        self._tab_db.rowconfigure(0, weight=1)
        self._db_panel = None
        self._notebook.select(self._tab_tool_library)

    def update_system_message(self, *, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            return
        content = " ".join(content.splitlines()).strip()
        self.system_info_var.set(content)

    def set_on_send(self, callback: Callable[[str], None]) -> None:
        self._on_send = callback

    def set_on_refresh_db(self, callback: Callable[[], None]) -> None:
        self._on_refresh_db = callback

    def _on_refresh_db_click(self) -> None:
        if self._on_refresh_db:
            self._on_refresh_db()

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
        self._ensure_queen_view()
        widget = self._view_texts.get(f"agent:{self.queen_name}")
        if widget is None:
            return
        content = (text or "").rstrip()
        if not content:
            return
        widget.configure(state="normal")
        widget.insert("end", f"你：{content}\n\n")
        widget.configure(state="disabled")
        widget.see("end")

    def add_user_reply(self, *, name: str, text: str, avatar: object | None = None) -> None:
        self._ensure_queen_view()
        widget = self._view_texts.get(f"agent:{self.queen_name}")
        if widget is None:
            return
        agent_name = str(name or "蚂蚁")
        final_text = (text or "").rstrip()
        if not final_text:
            return

        if self._reply_stream_active.get(agent_name):
            streamed = str(self._reply_stream_full_text.get(agent_name) or "")
            widget.configure(state="normal")
            if final_text.startswith(streamed):
                tail = final_text[len(streamed) :]
                if tail:
                    widget.insert("end", tail)
            elif streamed.rstrip() and streamed.rstrip().endswith(final_text.rstrip()):
                pass
            else:
                widget.insert("end", final_text)
            widget.insert("end", "\n\n")
            widget.configure(state="disabled")
            widget.see("end")
            self._reply_stream_active[agent_name] = False
            self._reply_stream_full_text[agent_name] = ""
            return

        self._add_to_text(widget, name=agent_name, text=final_text, avatar=avatar)

    def add_user_reply_stream(self, *, name: str, delta: str, avatar: object | None = None) -> None:
        """
        用户回复的流式增量追加。

        约定：这里仅追加增量文本，不做“是否重复/是否最后一段”的判断；这些由数据中心统一处理。
        """

        self._ensure_queen_view()
        widget = self._view_texts.get(f"agent:{self.queen_name}")
        if widget is None:
            return

        agent_name = str(name or "蚂蚁")
        inc = (delta or "")
        if not inc:
            return

        first = not bool(self._reply_stream_active.get(agent_name))
        if first:
            self._reply_stream_active[agent_name] = True
            self._reply_stream_full_text[agent_name] = ""

        widget.configure(state="normal")
        if first:
            if avatar is not None:
                widget.image_create("end", image=avatar)
                widget.insert("end", " ")
            widget.insert("end", f"{agent_name}：")
        widget.insert("end", inc)
        widget.configure(state="disabled")
        widget.see("end")
        self._reply_stream_full_text[agent_name] = str(self._reply_stream_full_text.get(agent_name) or "") + inc

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

        if name == self.queen_name:
            footer = ttk.Frame(frame)
            footer.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))
            footer.columnconfigure(1, weight=1)

            if self.tag_var is None:
                self.tag_var = tk.StringVar(value="聊天")
            self.tag_dropdown = ttk.Combobox(
                footer,
                textvariable=self.tag_var,
                values=self.tag_options,
                state="readonly",
                width=6,
            )
            self.tag_dropdown.grid(row=0, column=0, sticky="w")

            self.entry = ttk.Entry(footer)
            self.entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
            self.entry.bind("<Return>", lambda _: self._send_from_entry())

            send_btn = ttk.Button(footer, text="发送", command=self._send_from_entry)
            send_btn.grid(row=0, column=2, padx=(8, 0))

        avatar = self._image_refs.get(self._agent_rows.get(name) or view_key)
        if avatar is not None:
            self._update_agent_view_avatar(name=name, avatar=avatar)

    def _ensure_queen_view(self) -> None:
        self.ensure_agent_item(name=self.queen_name, avatar=None)

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

    def _on_tab_changed(self, _: object) -> None:
        if self._notebook is None or self._tab_db is None:
            return
        if self._notebook.select() != str(self._tab_db):
            return
        if self._db_panel is None:
            from ui.chroma_admin import ChromaAdminPanel

            panel = ChromaAdminPanel(self._tab_db)
            panel.grid(row=0, column=0, sticky="nsew")
            self._db_panel = panel
        start = getattr(self._db_panel, "start", None)
        if callable(start):
            start()
