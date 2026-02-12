from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

import yaml

from chromaserver.client import RemoteVectorStoreClient, load_server_url, save_server_url
from chromaserver.protocol import VectorStoreSpec


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def _logs_dir() -> str:
    p = os.path.join(_repo_root(), "chromaserver", "_logs")
    os.makedirs(p, exist_ok=True)
    return p


def _server_log_path() -> str:
    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(_logs_dir(), f"chromaserver_{day}.log")

def _is_local_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u:
        return False
    try:
        parsed = urlparse(u)
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1"}


class ChromaAdminApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("向量数据库管理（Chroma）")
        self.root.geometry("1180x720")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.panel = ChromaAdminPanel(self.root)
        self.panel.grid(row=0, column=0, sticky="nsew")
        self.panel.start()

    def run(self) -> None:
        self.root.mainloop()

class ChromaAdminPanel(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.var_url = tk.StringVar(master=self, value=load_server_url())
        self.var_status = tk.StringVar(master=self, value="未连接")
        self.var_preload = tk.BooleanVar(master=self, value=False)
        self.var_service_state = tk.StringVar(master=self, value="未启动")

        self._proc: subprocess.Popen[str] | None = None
        self._proc_log_fp: Any | None = None
        self._health_polling = False
        self._health_poll_tries = 0
        self._health_poll_max_tries = 0
        self._last_health_error: str = ""
        self._last_clear_stop_first = False
        self._last_health_pid: int | None = None
        self._health_poll_mode: str = ""
        self._started = False
        self._last_collection_counts: dict[str, int] = {}

        self.tree: ttk.Treeview | None = None
        self.text: tk.Text | None = None
        self.service_state_label: tk.Label | None = None

        self._build_ui()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._after(100, self._auto_detect_service)

    def _after(self, ms: int, fn: Callable[[], None]) -> None:
        self.winfo_toplevel().after(int(ms), fn)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=0)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=0)

        cfg = ttk.LabelFrame(outer, text="服务地址", padding=10)
        cfg.grid(row=0, column=0, sticky="ew")
        cfg.columnconfigure(1, weight=1)

        ttk.Label(cfg, text="URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(cfg, textvariable=self.var_url).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cfg, text="保存", command=self._on_save_url).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(cfg, text="健康检查", command=self._on_health).grid(row=0, column=3)

        ops = ttk.LabelFrame(outer, text="操作", padding=10)
        ops.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        ops.columnconfigure(0, weight=1)
        ops.rowconfigure(1, weight=1)

        bar = ttk.Frame(ops)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        ttk.Button(bar, text="启动服务", command=self._on_start_server).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(bar, text="停止服务", command=self._on_stop_server).grid(row=0, column=2, padx=(0, 6))
        ttk.Checkbutton(bar, text="初始化时写入角色卡片", variable=self.var_preload).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(bar, text="初始化数据库", command=self._on_init_db).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(bar, text="清理数据库", command=self._on_clear_db).grid(row=0, column=5, padx=(0, 6))
        ttk.Button(bar, text="清理日志", command=self._on_clear_logs).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(bar, text="刷新信息", command=self._on_refresh_info).grid(row=0, column=7)

        paned = ttk.PanedWindow(ops, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        left = ttk.Frame(paned, padding=6)
        right = ttk.Frame(paned, padding=6)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=0)
        self.tree = ttk.Treeview(left, columns=("workspace_id", "collection", "count"), show="headings", selectmode="browse")
        self.tree.heading("workspace_id", text="workspace_id")
        self.tree.heading("collection", text="collection")
        self.tree.heading("count", text="count")
        self.tree.column("workspace_id", width=420, anchor="w")
        self.tree.column("collection", width=260, anchor="w")
        self.tree.column("count", width=90, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.text = tk.Text(right, wrap="word", height=18)
        self.text.grid(row=0, column=0, sticky="nsew")
        t_ysb = ttk.Scrollbar(right, orient="vertical", command=self.text.yview)
        t_ysb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=t_ysb.set)
        self._set_text(f"日志目录：{_logs_dir()}\n服务日志：{_server_log_path()}\n")

        foot = ttk.Frame(outer)
        foot.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        foot.columnconfigure(0, weight=1)
        self.service_state_label = tk.Label(foot, textvariable=self.var_service_state, bg="#eeeeee", fg="#111111", padx=10, pady=3)
        self.service_state_label.grid(row=0, column=0, sticky="w")
        ttk.Label(foot, textvariable=self.var_status).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(foot, text="打开日志目录", command=self._on_open_logs).grid(row=0, column=2)

    def _set_text(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    def _clear_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _append_text(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", str(text))
        self.text.see("end")
        self.text.configure(state="disabled")

    def _append_line(self, text: str) -> None:
        self._append_text(f"{text}\n")

    def _set_service_state(self, *, state: str) -> None:
        s = str(state or "").strip() or "未启动"
        self.var_service_state.set(s)
        bg = "#eeeeee"
        fg = "#111111"
        if s == "启动中":
            bg = "#fff3cd"
            fg = "#664d03"
        elif s == "运行中":
            bg = "#d1e7dd"
            fg = "#0f5132"
        elif s == "失败":
            bg = "#f8d7da"
            fg = "#842029"
        self.service_state_label.configure(bg=bg, fg=fg)

    def _auto_detect_service(self) -> None:
        url = str(self.var_url.get() or "").strip()
        if not url:
            self.var_status.set("未配置 URL")
            self._set_service_state(state="未启动")
            return
        if self._health_polling:
            return
        self.var_status.set("检测服务中…")
        self._set_service_state(state="启动中")
        self._append_line("")
        self._append_line(f"========== 自动探测 {datetime.now().strftime('%H:%M:%S')} ==========")
        self._append_line(f"url={url}")
        self._start_health_poll(max_tries=10, interval_ms=300, mode="detect")

    def _client(self) -> RemoteVectorStoreClient:
        return RemoteVectorStoreClient(base_url=str(self.var_url.get() or "").strip())

    def _run_async(self, coro: Any, *, on_ok: Callable[[Any], None], on_err: Callable[[Exception], None]) -> None:
        def _work() -> None:
            try:
                res = asyncio.run(coro)
            except Exception as e:
                self._after(0, lambda e=e: on_err(e))
                return
            self._after(0, lambda: on_ok(res))

        threading.Thread(target=_work, daemon=True).start()

    def _on_save_url(self) -> None:
        try:
            path = save_server_url(str(self.var_url.get() or "").strip())
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self.var_status.set(f"已保存：{path}")

    def _on_open_logs(self) -> None:
        path = _logs_dir()
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _on_clear_logs(self) -> None:
        if not messagebox.askyesno("确认清理日志", "将删除 chromaserver/_logs 目录下的日志文件，是否继续？"):
            return
        self.var_status.set("清理日志中…")
        self._append_line("")
        self._append_line(f"========== 清理日志 {datetime.now().strftime('%H:%M:%S')} ==========")
        try:
            self._clear_local_logs(base_dir=_repo_root())
        except Exception as e:
            self.var_status.set(f"清理日志失败：{e}")
            self._append_line(f"清理日志失败：{type(e).__name__}: {e}")
            return
        self.var_status.set("清理日志完成。")

    def _on_start_server(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self.var_status.set("服务进程已在运行。")
            return
        log_path = _server_log_path()
        if self._proc_log_fp is not None:
            try:
                self._proc_log_fp.close()
            except Exception:
                pass
            self._proc_log_fp = None
        f = open(log_path, "a", encoding="utf-8")
        self._proc_log_fp = f
        cmd = [sys.executable, "-m", "chromaserver.server"]
        self._proc = subprocess.Popen(cmd, cwd=_repo_root(), stdout=f, stderr=f, text=True)
        self._append_line(f"启动服务进程：{' '.join(cmd)}")
        self._append_line(f"服务日志：{log_path}")
        self.var_status.set("服务启动中…")
        self._set_service_state(state="启动中")
        self._start_health_poll(max_tries=40, interval_ms=300, mode="start")

    def _start_health_poll(self, *, max_tries: int, interval_ms: int, mode: str) -> None:
        if self._health_polling:
            return
        self._health_polling = True
        self._health_poll_tries = 0
        self._health_poll_max_tries = int(max_tries)
        self._last_health_error = ""
        self._health_poll_mode = str(mode or "").strip() or "start"
        self._poll_health_step(interval_ms=int(interval_ms))

    def _poll_health_step(self, *, interval_ms: int) -> None:
        if not self._health_polling:
            return
        self._health_poll_tries += 1
        try:
            c = self._client()
        except Exception as e:
            self._last_health_error = str(e)
            if self._health_poll_tries >= self._health_poll_max_tries:
                self._health_polling = False
                self.var_status.set(f"健康：FAIL {self._last_health_error}")
                self._set_service_state(state="失败")
                self._append_line(f"健康检查失败：{self._last_health_error}")
                return
            self._after(interval_ms, lambda: self._poll_health_step(interval_ms=interval_ms))
            return

        def _ok(res: dict[str, Any]) -> None:
            self._health_polling = False
            self.var_status.set("健康：OK")
            self._set_service_state(state="运行中")
            self._append_line("健康检查通过，服务已就绪。")
            try:
                self._append_line(json_pretty(res))
            except Exception:
                pass
            try:
                pid = res.get("pid", None)
                self._last_health_pid = int(pid) if pid is not None else None
            except Exception:
                self._last_health_pid = None
            if self._health_poll_mode == "detect":
                self.var_status.set("已连接")
            self._refresh_tree_only(update_status=True)

        def _err(e: Exception) -> None:
            self._last_health_error = str(e)
            if self._health_poll_tries >= self._health_poll_max_tries:
                self._health_polling = False
                if self._health_poll_mode == "detect":
                    self.var_status.set("未发现服务")
                    self._set_service_state(state="未启动")
                    self._append_line("未发现可用服务（health 不可达）。")
                else:
                    self.var_status.set(f"健康：FAIL {self._last_health_error}")
                    self._set_service_state(state="失败")
                    self._append_line(f"健康检查失败：{self._last_health_error}")
                return
            self._after(interval_ms, lambda: self._poll_health_step(interval_ms=interval_ms))

        self._run_async(c.health(), on_ok=_ok, on_err=_err)

    def _refresh_tree_only(self, *, update_status: bool = False) -> None:
        try:
            c = self._client()
        except Exception:
            return

        def _ok(res: dict[str, Any]) -> None:
            for i in self.tree.get_children():
                self.tree.delete(i)
            rows = res.get("collections") or []
            n = 0
            new_counts: dict[str, int] = {}
            if isinstance(rows, list):
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    wid = str(r.get("workspace_id") or "")
                    cname = str(r.get("collection") or "")
                    cnt_raw = r.get("count")
                    cnt = str(cnt_raw if cnt_raw is not None else "")
                    self.tree.insert("", "end", values=(wid, cname, cnt))
                    n += 1
                    if cname:
                        try:
                            new_counts[cname] = int(cnt_raw) if cnt_raw is not None else 0
                        except Exception:
                            pass
            self._last_collection_counts = new_counts
            if update_status:
                if n > 0:
                    self.var_status.set(f"已连接（collections={n}）")
                else:
                    self.var_status.set("已连接（collections=0）")

        def _err(_: Exception) -> None:
            return

        self._run_async(c.info(), on_ok=_ok, on_err=_err)

    def _on_stop_server(self) -> None:
        self._append_line("")
        self._append_line(f"========== 停止服务 {datetime.now().strftime('%H:%M:%S')} ==========")
        try:
            c = self._client()
        except Exception as e:
            messagebox.showerror("停止失败", str(e))
            self._append_line(f"停止失败：{e}")
            return

        try:
            info = asyncio.run(c.health())
            pid = info.get("pid", None)
            self._last_health_pid = int(pid) if pid is not None else None
            if self._last_health_pid is not None:
                self._append_line(f"当前 health pid={self._last_health_pid}")
        except Exception as e:
            self._append_line(f"health 获取失败：{e}")

        def _ok(_: Any) -> None:
            self.var_status.set("已请求服务停止。")
            self._set_service_state(state="未启动")
            self._append_line("已发送 /shutdown 请求。")
            self._append_line("等待服务退出（health 不可达）…")
            self._start_wait_health_down(max_wait_s=8.0, interval_ms=300)
            if self._proc_log_fp is not None:
                try:
                    self._proc_log_fp.close()
                except Exception:
                    pass
                self._proc_log_fp = None

        def _err(e: Exception) -> None:
            self.var_status.set(f"停止失败：{e}")
            self._append_line(f"停止失败：{type(e).__name__}: {e}")

        self._run_async(c.shutdown(), on_ok=_ok, on_err=_err)

    def _start_wait_health_down(self, *, max_wait_s: float, interval_ms: int) -> None:
        started = time.perf_counter()

        def _tick() -> None:
            elapsed = time.perf_counter() - started
            if elapsed >= float(max_wait_s):
                self._append_line("等待超时：服务仍可达，尝试强制终止本地进程…")
                killed = self._force_kill_local_proc()
                if not killed:
                    self._force_kill_by_pid()
                return
            try:
                c = self._client()
                self._run_async(
                    c.health(),
                    on_ok=lambda _: self._after(interval_ms, _tick),
                    on_err=lambda _: self._append_line("服务已退出（health 不可达）。"),
                )
            except Exception:
                self._append_line("服务已退出（health 不可达）。")

        self._after(interval_ms, _tick)

    def _force_kill_local_proc(self) -> bool:
        p = self._proc
        if p is None or p.poll() is not None:
            self._append_line("未检测到可终止的本地进程（可能不是从本页面启动的服务）。")
            return False
        try:
            p.terminate()
            self._append_line("已发送 terminate。")
        except Exception as e:
            self._append_line(f"terminate 失败：{e}")
        try:
            p.wait(timeout=3)
            self._append_line("进程已退出。")
            return True
        except Exception:
            try:
                p.kill()
                self._append_line("已发送 kill。")
                return True
            except Exception as e:
                self._append_line(f"kill 失败：{e}")
                return False

    def _force_kill_by_pid(self) -> None:
        pid = self._last_health_pid
        if pid is None or pid <= 0:
            self._append_line("无法强制终止：health 未提供 pid。")
            return
        if not _is_local_url(str(self.var_url.get() or "").strip()):
            self._append_line("无法强制终止：当前 URL 不是本机地址。")
            return
        self._append_line(f"尝试 taskkill 强制终止 pid={pid} …")
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            self._append_line(f"taskkill exit_code={r.returncode}")
            if out:
                self._append_line(out)
            if err:
                self._append_line(err)
        except Exception as e:
            self._append_line(f"taskkill 执行失败：{e}")

    def _on_health(self) -> None:
        try:
            c = self._client()
        except Exception as e:
            messagebox.showerror("健康检查失败", str(e))
            return

        def _ok(res: dict[str, Any]) -> None:
            self.var_status.set("健康：OK")
            self._set_text(json_pretty(res))

        def _err(e: Exception) -> None:
            self.var_status.set(f"健康：FAIL {e}")
            self._set_text(str(e))

        self._run_async(c.health(), on_ok=_ok, on_err=_err)

    def _on_init_db(self) -> None:
        try:
            _ = self._client()
        except Exception as e:
            messagebox.showerror("初始化失败", str(e))
            return
        preload = bool(self.var_preload.get())
        self.var_status.set("初始化中…")
        self._append_line("")
        self._append_line(f"========== 初始化 {datetime.now().strftime('%H:%M:%S')} ==========")
        self._append_line("初始化开始")
        self._append_line(f"preload_system_prompts={preload}")

        def _work() -> None:
            try:
                self._init_db_stepwise(preload_system_prompts=preload)
            except Exception as e:
                self._after(0, lambda e=e: self._on_init_db_error(e))
                return
            self._after(0, self._on_init_db_ok)

        threading.Thread(target=_work, daemon=True).start()

    def _on_init_db_ok(self) -> None:
        self.var_status.set("初始化完成。")
        self._append_line("初始化完成")
        self._on_refresh_info(append=True)

    def _on_init_db_error(self, e: Exception) -> None:
        self.var_status.set(f"初始化失败：{e}")
        self._append_line(f"初始化失败：{type(e).__name__}: {e}")

    def _init_db_stepwise(self, *, preload_system_prompts: bool) -> None:
        base_dir = _repo_root()
        uid = str(os.getenv("ANT_USER_ID") or "local_user").strip() or "local_user"
        agents = self._load_agent_infos(base_dir=base_dir)
        self._ui_append(f"读取角色配置完成：roles={len(agents)} user_id={uid}")

        c = self._client()
        for role_key, agent_name in agents:
            default_memory_type = self._default_memory_type_by_role(role_key)
            workspace_id = f"{uid}:{agent_name}"
            spec = VectorStoreSpec(
                agent_name=agent_name,
                base_workspace_id=workspace_id,
                default_memory_type=default_memory_type,
                vector_store_dir="",
                jsonl_storage_dir="",
                reme_config_path=None,
            )
            self._ui_append(f"[{role_key}] ensure_ready start: {workspace_id} default_memory_type={default_memory_type}")
            asyncio.run(c.call("ensure_ready", spec))
            self._ui_append(f"[{role_key}] ensure_ready ok: {workspace_id}")

            if preload_system_prompts:
                blocks = self._split_system_prompts(base_dir=base_dir, role_key=role_key)
                self._ui_append(f"[{role_key}] record system_prompts start: blocks={len(blocks)}")
                asyncio.run(c.call("record_to_memory", spec, "system_prompts_init", blocks, "personal", None))
                self._ui_append(f"[{role_key}] record system_prompts ok")

    def _ui_append(self, text: str) -> None:
        self._after(0, lambda t=str(text): self._append_line(t))

    @staticmethod
    def _default_memory_type_by_role(role_key: str) -> str:
        k = str(role_key or "").strip().lower()
        if k == "queen":
            return "personal"
        if k == "king":
            return "task"
        return "tool"

    @staticmethod
    def _load_agent_infos(*, base_dir: str) -> list[tuple[str, str]]:
        path = os.path.join(os.path.abspath(base_dir), "configs", "agent_configs.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("agent_configs.yaml 内容格式不正确，应为对象。")
        out: list[tuple[str, str]] = []
        for role_key, cfg in data.items():
            if not isinstance(role_key, str):
                continue
            if not isinstance(cfg, dict):
                continue
            name = str(cfg.get("name") or "").strip()
            if not name:
                continue
            out.append((role_key.strip(), name))
        if not out:
            raise ValueError("agent_configs.yaml 未找到有效角色。")
        return out

    @staticmethod
    def _split_system_prompts(*, base_dir: str, role_key: str) -> list[str]:
        path = os.path.join(os.path.abspath(base_dir), "configs", "prompts", "system_prompts.yaml")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("system_prompts.yaml 内容格式不正确，应为对象。")
        text = data.get(role_key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"system_prompts.yaml 缺少角色：{role_key}")
        blocks = [x.strip() for x in text.split("\n\n") if x.strip()]
        return [f"【系统提示】【{role_key}】\n{b}" for b in blocks]

    def _on_clear_db(self) -> None:
        if not messagebox.askyesno("确认清理", "清理将重置向量库与 JSONL 数据目录，是否继续？"):
            return
        base_url = str(self.var_url.get() or "").strip()
        local = _is_local_url(base_url)
        if not local:
            self.var_status.set("清理失败：非本机 URL")
            self._append_line("")
            self._append_line(f"========== 清理 {datetime.now().strftime('%H:%M:%S')} ==========")
            self._append_line(f"url={base_url}")
            self._append_line("清理失败：非本机 URL 仅支持停服+本地删除。")
            messagebox.showerror("清理失败", "非本机 URL 仅支持停服+本地删除，请在本机打开数据库界面执行清理。")
            return
        self.var_status.set("清理中…")
        self._append_line("")
        self._append_line(f"========== 清理 {datetime.now().strftime('%H:%M:%S')} ==========")
        self._append_line(f"url={base_url}")
        self._append_line(f"local_url={local}")
        self._append_line("检查服务健康状态…")

        try:
            c = self._client()
        except Exception as e:
            messagebox.showerror("清理失败", str(e))
            self._append_line(f"清理失败：{e}")
            return

        def _on_health_ok(health: dict[str, Any]) -> None:
            stop_first = True
            self._last_clear_stop_first = True
            self._append_line("stop_first=True")

            def _work() -> None:
                try:
                    self._clear_db_stepwise(stop_first=stop_first, local=local, health=health)
                except Exception as e:
                    self._after(0, lambda e=e: self._on_clear_db_error(e))
                    return
                self._after(0, self._on_clear_db_ok)

            threading.Thread(target=_work, daemon=True).start()

        def _on_health_err(e: Exception) -> None:
            self._append_line(f"health 不可达：{e}")
            self._append_line("检测到服务已停止（health 不可达），执行本地清理…")
            vector_store_dir, jsonl_storage_dir = self._local_storage_dirs(base_dir=_repo_root())
            self._append_line(f"vector_store_dir={vector_store_dir}")
            self._append_line(f"jsonl_storage_dir={jsonl_storage_dir}")
            self._last_clear_stop_first = True

            def _work() -> None:
                try:
                    self._safe_rmtree(vector_store_dir)
                    self._safe_rmtree(jsonl_storage_dir)
                    os.makedirs(vector_store_dir, exist_ok=True)
                    os.makedirs(jsonl_storage_dir, exist_ok=True)
                    self._set_service_state(state="未启动")
                except Exception as e2:
                    self._after(0, lambda e=e2: self._on_clear_db_error(e))
                    return
                self._after(0, self._on_clear_db_ok)

            threading.Thread(target=_work, daemon=True).start()

        self._run_async(c.health(), on_ok=_on_health_ok, on_err=_on_health_err)

    def _on_clear_db_ok(self) -> None:
        self.var_status.set("清理完成。")
        self._append_line("清理完成")
        self._append_line("已停止服务并完成本地清理，请重新启动服务后再刷新信息。")

    def _on_clear_db_error(self, e: Exception) -> None:
        self.var_status.set(f"清理失败：{e}")
        self._append_line(f"清理失败：{type(e).__name__}: {e}")
        msg = str(e)
        if "WinError 32" in msg or "正在使用此文件" in msg or "chroma.sqlite3" in msg:
            self._append_line("提示：检测到数据库文件被占用（WinError 32）。建议先停止服务再清理。")
            self._append_line("可选路径：点“停止服务”→ 等待服务退出 → 再点“清理数据库”（选择先停服）。")

    def _clear_db_stepwise(self, *, stop_first: bool, local: bool, health: dict[str, Any] | None = None) -> None:
        c = self._client()
        if health is None:
            self._ui_append("检查服务健康状态…")
            health = asyncio.run(c.health())
        vector_store_dir = str(health.get("vector_store_dir") or "").strip()
        jsonl_storage_dir = str(health.get("jsonl_storage_dir") or "").strip()
        self._ui_append(f"vector_store_dir={vector_store_dir}")
        self._ui_append(f"jsonl_storage_dir={jsonl_storage_dir}")

        if not stop_first:
            raise RuntimeError("严格模式下清理数据库必须先停止服务。")
        self._ui_append("请求停止服务…")
        asyncio.run(c.shutdown())
        self._ui_append("停止请求已发送，等待服务退出…")
        self._wait_health_down(max_wait_s=15.0, interval_s=0.3)
        self._ui_append("服务已退出（health 不可达）")
        self._ui_append("开始本地删除目录…")
        self._safe_rmtree(vector_store_dir)
        self._safe_rmtree(jsonl_storage_dir)
        os.makedirs(vector_store_dir, exist_ok=True)
        os.makedirs(jsonl_storage_dir, exist_ok=True)
        self._ui_append("本地目录已重建")
        self._set_service_state(state="未启动")

    def _clear_local_logs(self, *, base_dir: str) -> None:
        base = os.path.abspath(str(base_dir or "").strip() or _repo_root())
        logs_dir = os.path.join(base, "chromaserver", "_logs")
        if not os.path.exists(logs_dir):
            return
        removed = 0
        failed = 0
        try:
            names = list(os.listdir(logs_dir))
        except Exception:
            return
        for name in names:
            p = os.path.join(logs_dir, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed += 1
            except Exception:
                failed += 1
        self._ui_append(f"logs 清理完成：removed={removed} failed={failed} dir={logs_dir}")

    @staticmethod
    def _local_storage_dirs(*, base_dir: str) -> tuple[str, str]:
        base = os.path.abspath(str(base_dir or "").strip() or _repo_root())
        default_vector_dir = os.path.join(base, "chromaserver", "data", "chroma_vector_store")
        default_jsonl_dir = os.path.join(base, "chromaserver", "data", "jsonl_storage")
        vector_store_dir = str(os.getenv("ANT_VECTOR_STORE_DIR") or default_vector_dir).strip() or default_vector_dir
        jsonl_storage_dir = str(os.getenv("ANT_JSONL_STORAGE_DIR") or default_jsonl_dir).strip() or default_jsonl_dir
        return vector_store_dir, jsonl_storage_dir

    def _wait_health_down(self, *, max_wait_s: float, interval_s: float) -> None:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < float(max_wait_s):
            try:
                c = self._client()
                _ = asyncio.run(c.health())
            except Exception:
                return
            time.sleep(float(interval_s))
        raise TimeoutError("等待服务退出超时。")

    @staticmethod
    def _safe_rmtree(path: str) -> None:
        p = os.path.abspath(str(path or "").strip())
        if not p:
            return
        if not os.path.exists(p):
            return
        shutil.rmtree(p)

    def _on_refresh_info(self, append: bool = False) -> None:
        try:
            c = self._client()
        except Exception as e:
            self.var_status.set(f"刷新失败：{e}")
            return

        def _ok(res: dict[str, Any]) -> None:
            self.var_status.set("已刷新。")
            if append:
                self._append_line("刷新信息：")
                self._append_line(json_pretty(res))
            else:
                self._set_text(json_pretty(res))
            for i in self.tree.get_children():
                self.tree.delete(i)
            rows = res.get("collections") or []
            prev_counts = dict(self._last_collection_counts)
            new_counts: dict[str, int] = {}
            changes: list[str] = []
            if isinstance(rows, list):
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    wid = str(r.get("workspace_id") or "")
                    cname = str(r.get("collection") or "")
                    cnt_raw = r.get("count")
                    cnt = str(cnt_raw if cnt_raw is not None else "")
                    self.tree.insert("", "end", values=(wid, cname, cnt))
                    if cname:
                        try:
                            new_counts[cname] = int(cnt_raw) if cnt_raw is not None else 0
                        except Exception:
                            pass
                    if cname and cnt_raw is not None:
                        try:
                            after = int(cnt_raw)
                        except Exception:
                            continue
                        before = prev_counts.get(cname)
                        if before is None:
                            if after != 0:
                                changes.append(f"{cname} ({wid}) 0 -> {after} (delta=+{after})")
                        elif before != after:
                            delta = after - int(before)
                            sign = "+" if delta >= 0 else ""
                            changes.append(f"{cname} ({wid}) {before} -> {after} (delta={sign}{delta})")
            self._last_collection_counts = new_counts
            if changes:
                self._append_line("")
                self._append_line("========== count 变化 ==========")
                for line in changes[:30]:
                    self._append_line(line)
                if len(changes) > 30:
                    self._append_line(f"... 省略 {len(changes) - 30} 条")

        def _err(e: Exception) -> None:
            self.var_status.set(f"刷新失败：{e}")
            if append:
                self._append_line(f"刷新失败：{e}")
            else:
                self._set_text(str(e))

        self._run_async(c.info(), on_ok=_ok, on_err=_err)


def json_pretty(obj: Any) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def main() -> None:
    app = ChromaAdminApp()
    app.run()


if __name__ == "__main__":
    main()
