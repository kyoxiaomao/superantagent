from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.data_center import DataCenter, ToolLibrarySnapshot


class ToolLibraryPanel(ttk.Frame):
    def __init__(self, parent: ttk.Frame, *, data_center: DataCenter) -> None:
        super().__init__(parent)
        self.data_center = data_center

        self._type_display_to_key: dict[str, str] = {"全部": "all", "技能": "skill", "工具": "tool"}
        self._type_key_to_display: dict[str, str] = {"all": "全部", "skill": "技能", "tool": "工具"}

        self.mode_var = tk.StringVar(value="library")
        self.role_var = tk.StringVar(value="")
        self.type_var = tk.StringVar(value="全部")
        self.query_var = tk.StringVar(value="")

        self._role_name_to_key: dict[str, str] = {}
        self._role_key_to_name: dict[str, str] = {}
        self._card_frames: list[ttk.Frame] = []
        self._columns = 1

        self._build()
        self.refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(7, weight=1)

        ttk.Label(toolbar, text="蚁族工具库").grid(row=0, column=0, sticky="w", padx=(0, 12))

        ttk.Radiobutton(toolbar, text="工具库", value="library", variable=self.mode_var, command=self.refresh).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(toolbar, text="角色", value="role", variable=self.mode_var, command=self.refresh).grid(row=0, column=2, sticky="w", padx=(6, 12))

        self.role_combo = ttk.Combobox(toolbar, textvariable=self.role_var, state="readonly", width=18)
        self.role_combo.grid(row=0, column=3, sticky="w", padx=(0, 10))
        self.role_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        self.type_combo = ttk.Combobox(toolbar, textvariable=self.type_var, state="readonly", width=8, values=["全部", "技能", "工具"])
        self.type_combo.grid(row=0, column=4, sticky="w", padx=(0, 10))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        ttk.Entry(toolbar, textvariable=self.query_var).grid(row=0, column=5, sticky="ew")
        self.query_var.trace_add("write", lambda *_a: self.refresh())

        body = ttk.Frame(self, padding=(10, 0, 10, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)

        self.inner = ttk.Frame(self.canvas)
        self.inner.columnconfigure(0, weight=1)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _e: object) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e: object) -> None:
        w = int(getattr(e, "width", 0) or 0)
        if w > 0:
            self.canvas.itemconfigure(self.inner_id, width=w)
        cols = max(1, w // 360) if w else 1
        if cols != self._columns:
            self._columns = cols
            self.refresh()

    def refresh(self) -> None:
        snapshot = self._get_snapshot()
        self._refresh_role_options(snapshot)
        self._render_cards(snapshot)

    def _refresh_role_options(self, snapshot: ToolLibrarySnapshot) -> None:
        key_to_name: dict[str, str] = {}
        name_to_key: dict[str, str] = {}
        for r in snapshot.roles:
            role_name = str(r.name)
            role_key = str(r.role_key)
            if role_name in name_to_key and name_to_key[role_name] != role_key:
                raise ValueError(f"role name duplicated: {role_name}")
            name_to_key[role_name] = role_key
            key_to_name[role_key] = role_name
        self._role_key_to_name = key_to_name
        self._role_name_to_key = name_to_key

        values = sorted(name_to_key.keys())
        self.role_combo["values"] = values

        current = str(self.role_var.get() or "").strip()
        if current in name_to_key:
            return
        default_role_key = "queen_sera" if "queen_sera" in key_to_name else (snapshot.roles[0].role_key if snapshot.roles else "")
        default_name = key_to_name.get(default_role_key) or (values[0] if values else "")
        if default_name:
            self.role_var.set(default_name)

    def _get_snapshot(self) -> ToolLibrarySnapshot:
        mode_raw = str(self.mode_var.get() or "").strip().lower()
        mode = "role" if mode_raw == "role" else "library"

        role_name = str(self.role_var.get() or "").strip()
        role_key = self._role_name_to_key.get(role_name) or "queen_sera"

        type_display = str(self.type_var.get() or "").strip()
        type_raw = self._type_display_to_key.get(type_display) or "all"

        query = str(self.query_var.get() or "")
        return self.data_center.get_tool_library_snapshot(mode=mode, role_key=role_key, type_filter=type_raw, query=query)

    def _clear_cards(self) -> None:
        for f in self._card_frames:
            f.destroy()
        self._card_frames = []

    def _render_cards(self, snapshot: ToolLibrarySnapshot) -> None:
        self._clear_cards()
        cols = max(1, int(self._columns))
        for i, card in enumerate(snapshot.cards):
            row = i // cols
            col = i % cols
            frame = ttk.Frame(self.inner, padding=10, relief="ridge")
            frame.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            self._card_frames.append(frame)

            frame.columnconfigure(0, weight=1)

            title = ttk.Label(frame, text=str(card.title or card.key), font=("Segoe UI", 11, "bold"))
            title.grid(row=0, column=0, sticky="w")

            kind_text = "技能" if card.kind == "skill" else "工具"
            ttk.Label(frame, text=kind_text).grid(row=0, column=1, sticky="e", padx=(10, 0))

            summary = str(card.summary or "").strip()
            if summary:
                ttk.Label(frame, text=summary, wraplength=320, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 6))

            action = self._build_action_spec(snapshot=snapshot, card_kind=card.kind, key=card.key, is_installed=card.is_installed)
            btn = ttk.Button(frame, text=action["label"], command=action["command"])
            btn.grid(row=2, column=1, sticky="e", pady=(6, 0))
            if not action["enabled"]:
                btn.state(["disabled"])

        for c in range(cols):
            self.inner.columnconfigure(c, weight=1)

    def _build_action_spec(
        self,
        *,
        snapshot: ToolLibrarySnapshot,
        card_kind: str,
        key: str,
        is_installed: bool,
    ) -> dict[str, object]:
        role_key = str(snapshot.role_key)
        mode = str(snapshot.mode)
        kind = str(card_kind)
        item_key = str(key)

        if mode == "role":
            if kind == "skill":
                return {"label": "卸载", "enabled": True, "command": lambda: self._do_uninstall_skill(role_key=role_key, skill_key=item_key)}
            return {"label": "卸载", "enabled": True, "command": lambda: self._do_uninstall_tool(role_key=role_key, tool_key=item_key)}

        if is_installed:
            return {"label": "已装配", "enabled": False, "command": lambda: None}
        if kind == "skill":
            return {"label": "装配", "enabled": True, "command": lambda: self._do_install_skill(role_key=role_key, skill_key=item_key)}
        return {"label": "装配", "enabled": True, "command": lambda: self._do_install_tool(role_key=role_key, tool_key=item_key)}

    def _do_install_skill(self, *, role_key: str, skill_key: str) -> None:
        self.data_center.install_skill(role_key=role_key, skill_key=skill_key)
        self.refresh()

    def _do_uninstall_skill(self, *, role_key: str, skill_key: str) -> None:
        self.data_center.uninstall_skill(role_key=role_key, skill_key=skill_key)
        self.refresh()

    def _do_install_tool(self, *, role_key: str, tool_key: str) -> None:
        self.data_center.install_tool(role_key=role_key, tool_key=tool_key)
        self.refresh()

    def _do_uninstall_tool(self, *, role_key: str, tool_key: str) -> None:
        self.data_center.uninstall_tool(role_key=role_key, tool_key=tool_key)
        self.refresh()
