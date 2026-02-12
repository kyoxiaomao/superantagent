"""
头像加载与缓存。

负责将 `ui/resources/animations` 下的 PNG 加载为 Tk 可用的 PhotoImage，并做简单缓存避免闪烁。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

from PIL import Image, ImageTk


@dataclass(frozen=True)
class AvatarSpec:
    key: str
    file_name: str


class AvatarStore:
    def __init__(self, base_dir: str, *, size: int = 40) -> None:
        self.base_dir = base_dir
        self.size = int(size)
        self._cache: Dict[str, ImageTk.PhotoImage] = {}

    def get(self, key: str) -> ImageTk.PhotoImage:
        if key in self._cache:
            return self._cache[key]
        path = os.path.join(self.base_dir, key)
        img = Image.open(path).convert("RGBA")
        img = img.resize((self.size, self.size))
        photo = ImageTk.PhotoImage(img)
        self._cache[key] = photo
        return photo


def default_avatar_map() -> dict[str, str]:
    return {
        "king": "king.png",
        "queen": "queen.png",
        "soldier": "soldier.png",
        "worker": "worker.png",
        "user": "queen.png",
    }


def resolve_avatar_key(agent_name: str) -> str:
    n = (agent_name or "").strip()
    if n in {"蚁王", "king"}:
        return "king"
    if n in {"蚁后", "queen"}:
        return "queen"
    if n in {"兵蚁", "soldier"}:
        return "soldier"
    if n in {"user", "用户", "你"}:
        return "user"
    return "worker"
