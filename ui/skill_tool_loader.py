from __future__ import annotations

import os
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from services.role_config_store import RoleInfo, load_roles
from utils.skill_tool_catalog import CompositeToolArtifact, SkillArtifact, SkillToolCatalog, load_catalog
from utils.agent_home_locator import get_agent_skill_dir, get_agent_tool_dir


class SkillToolLoaderApp:
    def __init__(self) -> None:
        self.base_dir = os.path.dirname(os.path.dirname(__file__))
        self.catalog: SkillToolCatalog | None = None
        self.skill_map: dict[str, SkillArtifact] = {}
        self.tool_map: dict[str, CompositeToolArtifact] = {}
        self.roles: dict[str, RoleInfo] = {}
        self._current_role_key: str | None = None

        self.root = tk.Tk()
        self.root.title("技能/工具加载器")
        self.root.geometry("1400x780")

        self._build_ui()
        self._reload()

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

        toolbar = ttk.Frame(outer)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        btn_reload = ttk.Button(toolbar, text="刷新", command=self._reload)
        btn_reload.grid(row=0, column=1)

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        self.tab_skills = ttk.Frame(self.notebook, padding=6)
        self.tab_tools = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab_skills, text="技能（单一能力域）")
        self.notebook.add(self.tab_tools, text="工具（复合多技能流程）")

        self._build_skill_tab(self.tab_skills)
        self._build_tool_tab(self.tab_tools)

    def _build_skill_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        vpaned = ttk.PanedWindow(parent, orient="vertical")
        vpaned.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(vpaned, padding=8)
        bottom = ttk.Frame(vpaned, padding=6)
        vpaned.add(top, weight=1)
        vpaned.add(bottom, weight=3)

        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=0)
        top.columnconfigure(2, weight=1)
        top.rowconfigure(0, weight=1)

        agent_box = ttk.LabelFrame(top, text="Agent 列表", padding=8)
        agent_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        agent_box.columnconfigure(0, weight=1)
        agent_box.rowconfigure(0, weight=1)

        self.agent_tree = ttk.Treeview(agent_box, columns=("role_key", "name"), show="headings", selectmode="browse")
        self.agent_tree.heading("role_key", text="role_key")
        self.agent_tree.heading("name", text="显示名")
        self.agent_tree.column("role_key", width=160, anchor="w")
        self.agent_tree.column("name", width=180, anchor="w")
        self.agent_tree.grid(row=0, column=0, sticky="nsew")
        self.agent_tree.bind("<<TreeviewSelect>>", self._on_select_agent)

        ay = ttk.Scrollbar(agent_box, orient="vertical", command=self.agent_tree.yview)
        ay.grid(row=0, column=1, sticky="ns")
        self.agent_tree.configure(yscrollcommand=ay.set)

        mid = ttk.Frame(top)
        mid.grid(row=0, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)

        btn_add = ttk.Button(mid, text="添加 →", command=self._on_add_skill_to_agent)
        btn_add.grid(row=0, column=0, pady=(80, 12))
        btn_remove = ttk.Button(mid, text="← 移除", command=self._on_remove_skill_from_agent)
        btn_remove.grid(row=1, column=0)

        assigned_box = ttk.LabelFrame(top, text="已分配技能", padding=8)
        assigned_box.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        assigned_box.columnconfigure(0, weight=1)
        assigned_box.rowconfigure(0, weight=1)

        self.assigned_tree = ttk.Treeview(assigned_box, columns=("skill_key",), show="headings", selectmode="browse")
        self.assigned_tree.heading("skill_key", text="技能名")
        self.assigned_tree.column("skill_key", width=260, anchor="w")
        self.assigned_tree.grid(row=0, column=0, sticky="nsew")

        sy = ttk.Scrollbar(assigned_box, orient="vertical", command=self.assigned_tree.yview)
        sy.grid(row=0, column=1, sticky="ns")
        self.assigned_tree.configure(yscrollcommand=sy.set)

        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(bottom, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(paned, padding=6)
        right = ttk.Frame(paned, padding=10)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.skill_tree = ttk.Treeview(left, columns=("has", "key", "interfaces"), show="headings", selectmode="browse")
        self.skill_tree.heading("has", text="已有")
        self.skill_tree.heading("key", text="技能名")
        self.skill_tree.heading("interfaces", text="接口数")
        self.skill_tree.column("has", width=60, anchor="center")
        self.skill_tree.column("key", width=220, anchor="w")
        self.skill_tree.column("interfaces", width=90, anchor="center")
        self.skill_tree.grid(row=0, column=0, sticky="nsew")
        self.skill_tree.bind("<<TreeviewSelect>>", self._on_select_skill)

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.skill_tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.skill_tree.configure(yscrollcommand=ysb.set)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.skill_text = tk.Text(right, wrap="word", height=18)
        self.skill_text.grid(row=0, column=0, sticky="nsew")
        s_ysb = ttk.Scrollbar(right, orient="vertical", command=self.skill_text.yview)
        s_ysb.grid(row=0, column=1, sticky="ns")
        self.skill_text.configure(yscrollcommand=s_ysb.set)
        self._set_text(self.skill_text, "加载中…")

    def _build_tool_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=0)
        parent.rowconfigure(1, weight=1)

        top = ttk.Frame(parent, padding=6)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=0)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=0)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="当前 Agent：").grid(row=0, column=0, sticky="w")
        self.tool_role_combo = ttk.Combobox(top, state="readonly", width=28)
        self.tool_role_combo.grid(row=0, column=1, sticky="w", padx=(6, 12))
        self.tool_role_combo.bind("<<ComboboxSelected>>", self._on_select_tool_role)

        btn_box = ttk.Frame(top)
        btn_box.grid(row=0, column=2, sticky="w", padx=(0, 12))
        ttk.Button(btn_box, text="添加 →", command=self._on_add_tool_to_agent).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btn_box, text="← 移除", command=self._on_remove_tool_from_agent).grid(row=0, column=1)

        assigned_box = ttk.LabelFrame(top, text="已分配工具", padding=8)
        assigned_box.grid(row=0, column=3, sticky="ew")
        assigned_box.columnconfigure(0, weight=1)
        self.assigned_tool_tree = ttk.Treeview(assigned_box, columns=("tool_key",), show="headings", selectmode="browse", height=4)
        self.assigned_tool_tree.heading("tool_key", text="工具名")
        self.assigned_tool_tree.column("tool_key", width=260, anchor="w")
        self.assigned_tool_tree.grid(row=0, column=0, sticky="ew")
        ty = ttk.Scrollbar(assigned_box, orient="vertical", command=self.assigned_tool_tree.yview)
        ty.grid(row=0, column=1, sticky="ns")
        self.assigned_tool_tree.configure(yscrollcommand=ty.set)

        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(paned, padding=6)
        right = ttk.Frame(paned, padding=10)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.tool_tree = ttk.Treeview(left, columns=("key", "steps"), show="headings", selectmode="browse")
        self.tool_tree.heading("key", text="工具名")
        self.tool_tree.heading("steps", text="步骤数")
        self.tool_tree.column("key", width=260, anchor="w")
        self.tool_tree.column("steps", width=90, anchor="center")
        self.tool_tree.grid(row=0, column=0, sticky="nsew")
        self.tool_tree.bind("<<TreeviewSelect>>", self._on_select_tool)

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tool_tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.tool_tree.configure(yscrollcommand=ysb.set)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.tool_text = tk.Text(right, wrap="word", height=18)
        self.tool_text.grid(row=0, column=0, sticky="nsew")
        t_ysb = ttk.Scrollbar(right, orient="vertical", command=self.tool_text.yview)
        t_ysb.grid(row=0, column=1, sticky="ns")
        self.tool_text.configure(yscrollcommand=t_ysb.set)
        self._set_text(self.tool_text, "加载中…")

    def _reload(self) -> None:
        self.catalog = load_catalog(repo_root=self.base_dir)
        self.skill_map = {s.key: s for s in self.catalog.skills}
        self.tool_map = {t.key: t for t in self.catalog.tools}
        self.roles = load_roles(self.base_dir)
        self._render_agent_list()
        self._render_skill_list()
        self._render_tool_list()

    def _render_agent_list(self) -> None:
        for item in self.agent_tree.get_children():
            self.agent_tree.delete(item)
        keys = sorted(self.roles.keys())
        for rk in keys:
            role = self.roles[rk]
            self.agent_tree.insert("", "end", iid=rk, values=(rk, role.name))
        first = keys[0] if keys else None
        if first:
            self.agent_tree.selection_set(first)
            self.agent_tree.focus(first)
            self._set_current_role(first)
        else:
            self._current_role_key = None
            self._render_assigned_skill_list()
            self._render_assigned_tool_list()

        if hasattr(self, "tool_role_combo"):
            self.tool_role_combo["values"] = keys
            if first:
                self.tool_role_combo.set(first)

    def _render_skill_list(self) -> None:
        for item in self.skill_tree.get_children():
            self.skill_tree.delete(item)
        assigned = self._get_assigned_skill_set()
        for s in self.catalog.skills if self.catalog else []:
            has = "✓" if s.key in assigned else ""
            self.skill_tree.insert("", "end", iid=s.key, values=(has, s.key, str(len(s.interfaces))))
        first = self.catalog.skills[0].key if self.catalog and self.catalog.skills else None
        if first:
            self.skill_tree.selection_set(first)
            self.skill_tree.focus(first)
            self._show_skill(first)
        else:
            self._set_text(self.skill_text, "未找到任何技能工件：utils/allskill/**/skill.md + skill.py")

    def _render_tool_list(self) -> None:
        for item in self.tool_tree.get_children():
            self.tool_tree.delete(item)
        for t in self.catalog.tools if self.catalog else []:
            self.tool_tree.insert("", "end", iid=t.key, values=(t.key, str(len(t.steps))))
        first = self.catalog.tools[0].key if self.catalog and self.catalog.tools else None
        if first:
            self.tool_tree.selection_set(first)
            self.tool_tree.focus(first)
            self._show_tool(first)
        else:
            self._set_text(
                self.tool_text,
                "未找到任何工具工件：utils/alltool/**/tool.md + tool.json\n\n"
                "你可以按约定创建一个工具目录，例如：\n"
                "- utils/alltool/video_maker/tool.md\n"
                "- utils/alltool/video_maker/tool.json",
            )

    def _on_select_skill(self, _event: object = None) -> None:
        sel = self.skill_tree.selection()
        if not sel:
            return
        key = str(sel[0])
        self._show_skill(key)

    def _on_select_agent(self, _event: object = None) -> None:
        sel = self.agent_tree.selection()
        if not sel:
            return
        role_key = str(sel[0])
        self._set_current_role(role_key)

    def _set_current_role(self, role_key: str) -> None:
        rk = str(role_key or "").strip()
        if not rk or rk not in self.roles:
            return
        self._current_role_key = rk
        self._render_assigned_skill_list()
        self._render_skill_list()
        if hasattr(self, "tool_role_combo"):
            self.tool_role_combo.set(rk)
        self._render_assigned_tool_list()

    def _render_assigned_skill_list(self) -> None:
        for item in self.assigned_tree.get_children():
            self.assigned_tree.delete(item)
        for sk in sorted(self._get_assigned_skill_set()):
            self.assigned_tree.insert("", "end", iid=sk, values=(sk,))

    def _get_assigned_skill_set(self) -> set[str]:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            return set()
        skills_dir = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
        if not os.path.isdir(skills_dir):
            return set()
        return {name for name in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, name))}

    def _on_add_skill_to_agent(self) -> None:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            messagebox.showwarning("提示", "请先选择一个 Agent。")
            return
        sel = self.skill_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在下方技能列表选择一个技能。")
            return
        skill_key = str(sel[0])
        if skill_key in self._get_assigned_skill_set():
            messagebox.showinfo("提示", f"该 Agent 已有技能：{skill_key}")
            return
        s = self.skill_map.get(skill_key)
        if s is None:
            messagebox.showerror("错误", f"未找到技能工件：{skill_key}")
            return
        dest_root = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
        os.makedirs(dest_root, exist_ok=True)
        dest_dir = os.path.join(dest_root, skill_key)
        if os.path.exists(dest_dir):
            messagebox.showinfo("提示", f"该 Agent 已有技能：{skill_key}")
            return
        try:
            shutil.copytree(os.path.dirname(s.script_path), dest_dir)
        except Exception as e:
            messagebox.showerror("错误", f"复制技能失败：{e}")
            return
        self._render_assigned_skill_list()
        self._render_skill_list()

    def _on_remove_skill_from_agent(self) -> None:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            messagebox.showwarning("提示", "请先选择一个 Agent。")
            return
        sel = self.assigned_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在已分配技能列表选择一个技能。")
            return
        skill_key = str(sel[0])
        try:
            dest_root = get_agent_skill_dir(repo_root=self.base_dir, role_key=rk)
            dest_dir = os.path.join(dest_root, skill_key)
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir)
        except Exception as e:
            messagebox.showerror("错误", f"移除技能失败：{e}")
            return
        self._render_assigned_skill_list()
        self._render_skill_list()

    def _on_select_tool_role(self, _event: object = None) -> None:
        rk = str(getattr(self, "tool_role_combo", None).get() if hasattr(self, "tool_role_combo") else "").strip()
        if rk and rk in self.roles:
            self._set_current_role(rk)
            self._render_assigned_tool_list()

    def _render_assigned_tool_list(self) -> None:
        if not hasattr(self, "assigned_tool_tree"):
            return
        for item in self.assigned_tool_tree.get_children():
            self.assigned_tool_tree.delete(item)
        for tk_ in sorted(self._get_assigned_tool_set()):
            self.assigned_tool_tree.insert("", "end", iid=tk_, values=(tk_,))

    def _get_assigned_tool_set(self) -> set[str]:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            return set()
        tools_dir = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
        if not os.path.isdir(tools_dir):
            return set()
        return {name for name in os.listdir(tools_dir) if os.path.isdir(os.path.join(tools_dir, name))}

    def _on_add_tool_to_agent(self) -> None:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            messagebox.showwarning("提示", "请先选择一个 Agent。")
            return
        sel = self.tool_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在右侧工具列表选择一个工具。")
            return
        tool_key = str(sel[0])
        if tool_key in self._get_assigned_tool_set():
            messagebox.showinfo("提示", f"该 Agent 已有工具：{tool_key}")
            return
        t = self.tool_map.get(tool_key)
        if t is None:
            messagebox.showerror("错误", f"未找到工具工件：{tool_key}")
            return
        dest_root = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
        os.makedirs(dest_root, exist_ok=True)
        dest_dir = os.path.join(dest_root, tool_key)
        if os.path.exists(dest_dir):
            messagebox.showinfo("提示", f"该 Agent 已有工具：{tool_key}")
            return
        try:
            shutil.copytree(os.path.dirname(t.spec_path), dest_dir)
        except Exception as e:
            messagebox.showerror("错误", f"复制工具失败：{e}")
            return
        self._render_assigned_tool_list()

    def _on_remove_tool_from_agent(self) -> None:
        rk = str(self._current_role_key or "").strip()
        if not rk:
            messagebox.showwarning("提示", "请先选择一个 Agent。")
            return
        if not hasattr(self, "assigned_tool_tree"):
            return
        sel = self.assigned_tool_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在已分配工具列表选择一个工具。")
            return
        tool_key = str(sel[0])
        try:
            dest_root = get_agent_tool_dir(repo_root=self.base_dir, role_key=rk)
            dest_dir = os.path.join(dest_root, tool_key)
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir)
        except Exception as e:
            messagebox.showerror("错误", f"移除工具失败：{e}")
            return
        self._render_assigned_tool_list()

    def _on_select_tool(self, _event: object = None) -> None:
        sel = self.tool_tree.selection()
        if not sel:
            return
        key = str(sel[0])
        self._show_tool(key)

    def _show_skill(self, key: str) -> None:
        s = self.skill_map.get(key)
        if s is None:
            return
        header_lines = [
            f"技能：{s.key}",
            f"标题：{s.title}",
            f"脚本：{s.script_path}",
            f"文档：{s.doc_path}",
            "",
            "接口列表：",
        ]
        if s.interfaces:
            for it in s.interfaces:
                header_lines.append(f"- {it.signature}")
        else:
            header_lines.append("- （未解析到接口）")

        if s.errors:
            header_lines.extend(["", "解析错误："])
            for e in s.errors:
                header_lines.append(f"- {e}")

        header_lines.extend(["", "-" * 60, "", s.doc_markdown.strip()])
        self._set_text(self.skill_text, "\n".join(header_lines).strip() + "\n")

    def _show_tool(self, key: str) -> None:
        t = self.tool_map.get(key)
        if t is None:
            return
        header_lines = [
            f"工具：{t.key}",
            f"标题：{t.title}",
            f"规范：{t.spec_path}",
            f"文档：{t.doc_path}",
            "",
            "流程步骤：",
        ]
        if t.steps:
            for step in t.steps:
                line = f"{step.idx}. {step.skill_key}.{step.interface_name}"
                if step.note:
                    line += f" —— {step.note}"
                header_lines.append(line)
                if step.params:
                    header_lines.append(f"   params={step.params}")
        else:
            header_lines.append("（未解析到 steps）")

        if t.errors:
            header_lines.extend(["", "解析错误："])
            for e in t.errors:
                header_lines.append(f"- {e}")

        header_lines.extend(["", "-" * 60, "", t.doc_markdown.strip()])
        self._set_text(self.tool_text, "\n".join(header_lines).strip() + "\n")

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


def main() -> None:
    app = SkillToolLoaderApp()
    app.run()


if __name__ == "__main__":
    main()
