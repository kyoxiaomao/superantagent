from __future__ import annotations

import os
import time


_ALLOWED_EXTENSIONS = {".png"}
_MAX_SIDE = 2048


def _get_repo_root() -> str:
    from services.role_config_store import get_base_dir

    return get_base_dir()


def _resolve_repo_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("save_path 必须是非空字符串。")
    if os.path.isabs(path):
        raise ValueError("不允许使用绝对路径。")
    repo_root = _get_repo_root()
    abs_path = os.path.abspath(os.path.join(repo_root, path))
    root_nc = os.path.normcase(repo_root)
    abs_nc = os.path.normcase(abs_path)
    if abs_nc != root_nc and not abs_nc.startswith(root_nc + os.path.sep):
        raise ValueError("路径越界：只允许写入仓库目录内的文件。")
    _, ext = os.path.splitext(abs_path)
    if ext.lower() not in _ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型：{ext}")
    return abs_path


def draw_image(*, prompt: str, save_path: str | None = None, width: int = 768, height: int = 512) -> str:
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("prompt 不能为空。")
    w = int(width)
    h = int(height)
    if w <= 0 or h <= 0:
        raise ValueError("width/height 必须为正整数。")
    if w > _MAX_SIDE or h > _MAX_SIDE:
        raise ValueError(f"width/height 过大：最大边长 {_MAX_SIDE}px。")

    if save_path is None or not str(save_path).strip():
        ts = time.strftime("%Y%m%d_%H%M%S")
        save_path = f"docs/generated/draw_image_{ts}.png"

    abs_path = _resolve_repo_path(str(save_path))
    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    margin = 24
    draw.multiline_text((margin, margin), text, fill=(0, 0, 0))
    img.save(abs_path, format="PNG")

    size = os.path.getsize(abs_path)
    return f"{abs_path} ({size} bytes)"


def register(toolkit: object) -> None:
    register_tool_function = getattr(toolkit, "register_tool_function", None)
    if register_tool_function is None or not callable(register_tool_function):
        raise ValueError("toolkit 缺少可调用的 register_tool_function。")
    register_tool_function(draw_image)
