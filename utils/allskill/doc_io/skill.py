from __future__ import annotations

import os


_ALLOWED_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".ini"}
_MAX_READ_LINES = 1000
_MAX_WRITE_CHARS = 200_000


def _get_repo_root() -> str:
    current_dir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(current_dir, os.pardir, os.pardir, os.pardir))
    return repo_root


def _resolve_repo_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 必须是非空字符串。")
    if os.path.isabs(path):
        raise ValueError("不允许使用绝对路径。")

    repo_root = _get_repo_root()
    abs_path = os.path.abspath(os.path.join(repo_root, path))

    root_nc = os.path.normcase(repo_root)
    abs_nc = os.path.normcase(abs_path)
    if abs_nc != root_nc and not abs_nc.startswith(root_nc + os.path.sep):
        raise ValueError("路径越界：只允许访问仓库目录内的文件。")

    _, ext = os.path.splitext(abs_path)
    if ext.lower() not in _ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型：{ext}")

    return abs_path


def read_text_doc(path: str, start_line: int = 1, max_lines: int = 200, encoding: str = "utf-8") -> str:
    if start_line < 1:
        raise ValueError("start_line 必须 >= 1。")
    if max_lines < 1:
        raise ValueError("max_lines 必须 >= 1。")
    max_lines = min(max_lines, _MAX_READ_LINES)

    abs_path = _resolve_repo_path(path)
    lines: list[str] = []
    end_line = start_line + max_lines - 1

    with open(abs_path, "r", encoding=encoding) as f:
        for i, line in enumerate(f, start=1):
            if i < start_line:
                continue
            if i > end_line:
                break
            lines.append(f"{i:>6}→{line.rstrip()}")

    return "\n".join(lines)


def write_text_doc(path: str, content: str, overwrite: bool = True, encoding: str = "utf-8") -> str:
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串。")
    if len(content) > _MAX_WRITE_CHARS:
        raise ValueError(f"content 过大：最大允许 {_MAX_WRITE_CHARS} 字符。")

    abs_path = _resolve_repo_path(path)
    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    if not overwrite and os.path.exists(abs_path):
        raise ValueError("目标文件已存在且 overwrite=False。")

    with open(abs_path, "w", encoding=encoding) as f:
        f.write(content)

    size = os.path.getsize(abs_path)
    return f"{abs_path} ({size} bytes)"


def append_text_doc(path: str, content: str, encoding: str = "utf-8") -> str:
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串。")
    if len(content) > _MAX_WRITE_CHARS:
        raise ValueError(f"content 过大：最大允许 {_MAX_WRITE_CHARS} 字符。")

    abs_path = _resolve_repo_path(path)
    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(abs_path, "a", encoding=encoding) as f:
        f.write(content)

    size = os.path.getsize(abs_path)
    return f"{abs_path} ({size} bytes)"


def register(toolkit: object) -> None:
    register_tool_function = getattr(toolkit, "register_tool_function", None)
    if register_tool_function is None or not callable(register_tool_function):
        raise ValueError("toolkit 缺少可调用的 register_tool_function。")

    register_tool_function(read_text_doc)
    register_tool_function(write_text_doc)
    register_tool_function(append_text_doc)

