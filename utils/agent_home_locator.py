from __future__ import annotations

import os
import re


_ROLE_KEY_RE = re.compile(r'role_key\s*=\s*"([^"]+)"')


def find_agent_home(*, repo_root: str, role_key: str) -> str:
    root = os.path.abspath(str(repo_root or ""))
    rk = str(role_key or "").strip()
    if not root:
        raise ValueError("repo_root 不能为空。")
    if not rk:
        raise ValueError("role_key 不能为空。")

    agents_dir = os.path.join(root, "agents")
    if not os.path.isdir(agents_dir):
        raise FileNotFoundError(f"未找到 agents 目录：{agents_dir}")

    for dirpath, _dirnames, filenames in os.walk(agents_dir):
        if "agent.py" not in filenames:
            continue
        p = os.path.join(dirpath, "agent.py")
        try:
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        m = _ROLE_KEY_RE.search(src)
        if not m:
            continue
        if str(m.group(1)).strip() == rk:
            return dirpath

    raise FileNotFoundError(f"未找到 role_key={rk} 对应的 agent_home（扫描 agents/**/agent.py 失败）。")


def get_agent_skill_dir(*, repo_root: str, role_key: str) -> str:
    home = find_agent_home(repo_root=repo_root, role_key=role_key)
    return os.path.join(home, "skills")


def get_agent_tool_dir(*, repo_root: str, role_key: str) -> str:
    home = find_agent_home(repo_root=repo_root, role_key=role_key)
    return os.path.join(home, "tools")

