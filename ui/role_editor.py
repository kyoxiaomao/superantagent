from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from services.role_config_store import RoleInfo, load_roles, save_roles, validate_roles


_ROLE_ORDER_DEFAULT: list[str] = ["king", "queen", "soldier", "emotion_worker", "browser_worker", "doc_worker"]

_HB_FIELDS: list[tuple[str, str]] = [
    ("enabled", "启用（enabled）"),
    ("interval_s", "间隔（interval_s，秒）"),
    ("idle_no_increment_s", "空闲不递增（idle_no_increment_s，秒）"),
    ("topic_cooldown_s", "话题冷却（topic_cooldown_s，秒）"),
    ("topic_active_s", "话题活跃（topic_active_s，秒）"),
    ("topic_decision_min_gap_s", "决策最小间隔（topic_decision_min_gap_s，秒）"),
    ("topic_turn_interval_s", "轮转间隔（topic_turn_interval_s，秒）"),
    ("history_window_n", "历史窗口（history_window_n）"),
]


class RoleEditorApp:
    def __init__(self) -> None:
        self.base_dir = os.path.dirname(os.path.dirname(__file__))
        self.roles: dict[str, RoleInfo] = {}
        self._dirty = False
        self._suppress_dirty = False
        self._current_role_key: str | None = None
        self._suppress_tree_select = False

        self.root = tk.Tk()
        self.root.title("Agent角色编辑器")
        self.root.geometry("1400x720")

        self._build_ui()
        self._load()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=0)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=0)

        toolbar = ttk.Frame(outer)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        btn_reload = ttk.Button(toolbar, text="重新加载", command=self._on_reload)
        btn_reload.grid(row=0, column=1, padx=(0, 6))
        btn_validate = ttk.Button(toolbar, text="验证", command=self._on_validate)
        btn_validate.grid(row=0, column=2, padx=(0, 6))
        btn_save = ttk.Button(toolbar, text="保存", command=self._on_save)
        btn_save.grid(row=0, column=3)

        paned = ttk.PanedWindow(outer, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        left = ttk.Frame(paned, padding=6)
        right = ttk.Frame(paned, padding=10)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.role_tree = ttk.Treeview(left, columns=("role_key", "name"), show="headings", selectmode="browse")
        self.role_tree.heading("role_key", text="role_key")
        self.role_tree.heading("name", text="显示名")
        self.role_tree.column("role_key", width=160, anchor="w")
        self.role_tree.column("name", width=160, anchor="w")
        self.role_tree.grid(row=0, column=0, sticky="nsew")
        self.role_tree.bind("<<TreeviewSelect>>", self._on_select_role)

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.role_tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.role_tree.configure(yscrollcommand=ysb.set)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(right)
        notebook.grid(row=0, column=0, sticky="nsew")

        tab_prompt = ttk.Frame(notebook, padding=10)
        tab_basic = ttk.Frame(notebook, padding=10)
        notebook.add(tab_prompt, text="系统提示词（system prompt）")
        notebook.add(tab_basic, text="基础信息（config）")

        tab_prompt.columnconfigure(0, weight=1)
        tab_prompt.rowconfigure(0, weight=1)

        tab_basic.columnconfigure(0, weight=1)

        basic = ttk.LabelFrame(tab_basic, text="基础信息（basic）", padding=10)
        basic.grid(row=0, column=0, sticky="ew")
        for i in range(4):
            basic.columnconfigure(i, weight=1)

        ttk.Label(basic, text="角色标识（role_key）").grid(row=0, column=0, sticky="w")
        self.var_role_key = tk.StringVar(value="")
        ttk.Entry(basic, textvariable=self.var_role_key, state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 16))

        ttk.Label(basic, text="显示名（name）").grid(row=0, column=2, sticky="w")
        self.var_name = tk.StringVar(value="")
        entry_name = ttk.Entry(basic, textvariable=self.var_name)
        entry_name.grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(basic, text="最大迭代（max_iters）").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.var_max_iters = tk.StringVar(value="")
        entry_iters = ttk.Entry(basic, textvariable=self.var_max_iters)
        entry_iters.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=(10, 0))

        self.var_name.trace_add("write", lambda *_: self._set_dirty(True))
        self.var_max_iters.trace_add("write", lambda *_: self._set_dirty(True))

        hb = ttk.LabelFrame(tab_basic, text="心跳（heartbeat）", padding=10)
        hb.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for i in range(6):
            hb.columnconfigure(i, weight=1)

        self.var_hb_enabled = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(hb, text="启用（enabled）", variable=self.var_hb_enabled, command=lambda: self._set_dirty(True))
        cb.grid(row=0, column=0, sticky="w")

        self.hb_vars: dict[str, tk.StringVar] = {}
        row = 1
        col = 0
        for key, label in _HB_FIELDS[1:]:
            var = tk.StringVar(value="")
            self.hb_vars[key] = var
            ttk.Label(hb, text=label).grid(row=row, column=col, sticky="w", pady=(6, 0))
            ent = ttk.Entry(hb, textvariable=var)
            ent.grid(row=row, column=col + 1, sticky="ew", padx=(8, 16), pady=(6, 0))
            var.trace_add("write", lambda *_: self._set_dirty(True))
            col += 2
            if col >= 6:
                col = 0
                row += 1

        prompt = ttk.LabelFrame(tab_prompt, text="系统提示词（system prompt）", padding=10)
        prompt.grid(row=0, column=0, sticky="nsew")
        prompt.columnconfigure(0, weight=1)
        prompt.rowconfigure(0, weight=1)

        self.txt_prompt = tk.Text(prompt, wrap="word", height=18, undo=True)
        self.txt_prompt.grid(row=0, column=0, sticky="nsew")
        prompt_ysb = ttk.Scrollbar(prompt, orient="vertical", command=self.txt_prompt.yview)
        prompt_ysb.grid(row=0, column=1, sticky="ns")
        self.txt_prompt.configure(yscrollcommand=prompt_ysb.set)
        self.txt_prompt.bind("<<Modified>>", self._on_prompt_modified)

        self.status = tk.StringVar(value="就绪")
        status_bar = ttk.Label(outer, textvariable=self.status, anchor="w")
        status_bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))

    def _set_dirty(self, dirty: bool) -> None:
        if self._suppress_dirty:
            return
        if dirty and not self._dirty:
            self._dirty = True
            self._refresh_title()
        if not dirty and self._dirty:
            self._dirty = False
            self._refresh_title()

    def _refresh_title(self) -> None:
        suffix = "（未保存）" if self._dirty else ""
        self.root.title(f"Agent角色编辑器{suffix}")

    def _load(self) -> None:
        self.roles = load_roles(self.base_dir)
        self._render_role_list()
        first_key = self._pick_first_role_key()
        if first_key:
            self._select_role(first_key)
        self._set_dirty(False)
        self.status.set("已加载配置")

    def _render_role_list(self) -> None:
        for item in self.role_tree.get_children():
            self.role_tree.delete(item)
        for role_key in self._sorted_role_keys():
            role = self.roles[role_key]
            self.role_tree.insert("", "end", iid=role_key, values=(role_key, role.name))

    def _sorted_role_keys(self) -> list[str]:
        keys = list(self.roles.keys())
        ordered: list[str] = []
        for k in _ROLE_ORDER_DEFAULT:
            if k in self.roles:
                ordered.append(k)
        for k in keys:
            if k not in ordered:
                ordered.append(k)
        return ordered

    def _pick_first_role_key(self) -> str | None:
        keys = self._sorted_role_keys()
        return keys[0] if keys else None

    def _on_select_role(self, _event: object = None) -> None:
        if self._suppress_tree_select:
            return
        sel = self.role_tree.selection()
        if not sel:
            return
        role_key = str(sel[0])
        self._select_role(role_key)

    def _select_role(self, role_key: str) -> None:
        if role_key not in self.roles:
            return
        if role_key == self._current_role_key:
            return
        if self._current_role_key:
            self._apply_form_to_role(self._current_role_key)
            self._update_role_row(self._current_role_key)
        self._current_role_key = role_key
        self._load_role_to_form(role_key)
        self._suppress_tree_select = True
        try:
            if self.role_tree.exists(role_key):
                self.role_tree.selection_set(role_key)
                self.role_tree.focus(role_key)
        finally:
            self._suppress_tree_select = False
        self.status.set(f"正在编辑：{role_key}")

    def _update_role_row(self, role_key: str) -> None:
        role = self.roles.get(role_key)
        if not role:
            return
        if self.role_tree.exists(role_key):
            self.role_tree.item(role_key, values=(role_key, role.name))

    def _load_role_to_form(self, role_key: str) -> None:
        role = self.roles[role_key]
        self._suppress_dirty = True
        try:
            self.var_role_key.set(role.role_key)
            self.var_name.set(role.name)
            self.var_max_iters.set(str(role.max_iters))

            hb = role.heartbeat or {}
            self.var_hb_enabled.set(bool(hb.get("enabled", True)))
            for key in self.hb_vars:
                v = hb.get(key, "")
                self.hb_vars[key].set("" if v is None else str(v))

            self.txt_prompt.delete("1.0", "end")
            self.txt_prompt.insert("1.0", role.sys_prompt or "")
            self.txt_prompt.edit_modified(False)
        finally:
            self._suppress_dirty = False

    def _apply_form_to_role(self, role_key: str) -> None:
        role = self.roles[role_key]
        role.name = str(self.var_name.get() or "").strip()
        role.max_iters = self._parse_int(self.var_max_iters.get(), default=role.max_iters)

        hb: dict[str, object] = {"enabled": bool(self.var_hb_enabled.get())}
        for key, var in self.hb_vars.items():
            raw = str(var.get() or "").strip()
            if raw == "":
                continue
            hb[key] = raw
        role.heartbeat = hb

        role.sys_prompt = str(self.txt_prompt.get("1.0", "end-1c") or "")

    def _on_prompt_modified(self, _event: object = None) -> None:
        if self._suppress_dirty:
            self.txt_prompt.edit_modified(False)
            return
        if self.txt_prompt.edit_modified():
            self._set_dirty(True)
            self.txt_prompt.edit_modified(False)

    def _on_reload(self) -> None:
        if self._dirty:
            ok = messagebox.askyesno("提示", "存在未保存修改，确定重新加载并丢弃修改吗？")
            if not ok:
                return
        try:
            self._load()
        except Exception as e:
            messagebox.showerror("错误", f"重新加载失败：{e}")

    def _on_validate(self) -> None:
        if self._current_role_key:
            self._apply_form_to_role(self._current_role_key)
            self._update_role_row(self._current_role_key)
        errors = validate_roles(self.roles)
        if errors:
            messagebox.showerror("校验失败", "\n".join(errors))
            self.status.set("校验失败")
            return
        messagebox.showinfo("校验通过", "配置校验通过。")
        self.status.set("校验通过")

    def _on_save(self) -> None:
        if self._current_role_key:
            self._apply_form_to_role(self._current_role_key)
            self._update_role_row(self._current_role_key)
        try:
            save_roles(self.roles, self.base_dir)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            self.status.set("保存失败")
            return
        self._set_dirty(False)
        self.status.set("保存成功（重启运行时后生效）")
        messagebox.showinfo("保存成功", "已写回配置文件。当前运行中的群聊/终端不会热更新，需重启后生效。")

    def _on_close(self) -> None:
        if self._dirty:
            ok = messagebox.askyesno("提示", "存在未保存修改，确定退出吗？")
            if not ok:
                return
        self.root.destroy()

    @staticmethod
    def _parse_int(raw: str, *, default: int) -> int:
        s = str(raw or "").strip()
        if not s:
            return default
        try:
            return int(s)
        except Exception:
            return default


def main() -> None:
    app = RoleEditorApp()
    app.run()


if __name__ == "__main__":
    main()
