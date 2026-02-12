"""
colony 组装与单轮执行编排。

    - `create_colony()` 负责实例化蚁王/兵蚁/工蚁并形成参与者集合。
    - `run_turn()` 提供“单轮对话”执行器：解析蚁王调度 JSON，完成 dispatch/tool_create 闭环。

"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

try:
    import yaml
except Exception:
    yaml = None

from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from agents.king import create_king_agent
from agents.queen import create_queen_agent
from agents.soldier import create_soldier_agent
from agents.worker import (
    create_browser_worker_agent,
    create_doc_worker_agent,
    create_emotion_worker_agent,
)
from message import extract_first_json_obj, make_msg, msg_to_text
from services import ModelBundle
from services.skill_loader import load_skills


@dataclass
class AntColony:
    king: Any
    queen: Any
    soldier: Any
    emotion_worker: Any
    browser_worker: Any
    doc_worker: Any

    @property
    def participants(self) -> list[Any]:
        return [self.king, self.queen, self.soldier, self.emotion_worker, self.browser_worker, self.doc_worker]

    @property
    def agent_map(self) -> dict[str, Any]:
        return {
            "emotion_worker": self.emotion_worker,
            "browser_worker": self.browser_worker,
            "doc_worker": self.doc_worker,
            "soldier_ant": self.soldier,
            "queen": self.queen,
            "king": self.king,
            "none": None,
        }

    def disable_console_output(self) -> None:
        for a in self.participants:
            if hasattr(a, "set_console_output_enabled"):
                a.set_console_output_enabled(False)


def _load_role_model_providers(*, base_dir: str) -> dict[str, str]:
    if yaml is None:
        raise ImportError("未安装 PyYAML，无法读取角色模型提供方配置")
    path = os.path.join(base_dir, "configs", "agent_configs.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("agent_configs.yaml 内容格式不正确，应为字典结构")
    out: dict[str, str] = {}
    for role_key, role_cfg in data.items():
        if not isinstance(role_key, str) or not role_key.strip():
            continue
        rcfg = role_cfg if isinstance(role_cfg, dict) else {}
        provider = str(rcfg.get("model_provider") or "").strip().lower()
        if provider:
            out[role_key.strip()] = provider
    return out


def _pick_bundle(
    *,
    role_key: str,
    model_bundles: dict[str, ModelBundle],
    role_provider: str | None,
    default_provider: str,
) -> ModelBundle:
    rp = str(role_provider or "").strip().lower()
    dp = str(default_provider or "").strip().lower() or "llm"
    if rp and rp in model_bundles:
        return model_bundles[rp]
    if dp in model_bundles:
        return model_bundles[dp]
    if model_bundles:
        return next(iter(model_bundles.values()))
    raise ValueError(f"未加载任何模型配置，无法创建 {role_key}。")


def create_colony(*, model_bundles: dict[str, ModelBundle], default_provider: str = "llm", base_dir: str | None = None) -> AntColony:
    base_dir = base_dir or os.path.dirname(os.path.dirname(__file__))
    role_providers = _load_role_model_providers(base_dir=base_dir)

    return AntColony(
        king=create_king_agent(_pick_bundle(role_key="king", model_bundles=model_bundles, role_provider=role_providers.get("king"), default_provider=default_provider)),
        queen=create_queen_agent(_pick_bundle(role_key="queen", model_bundles=model_bundles, role_provider=role_providers.get("queen"), default_provider=default_provider)),
        soldier=create_soldier_agent(_pick_bundle(role_key="soldier", model_bundles=model_bundles, role_provider=role_providers.get("soldier"), default_provider=default_provider)),
        emotion_worker=create_emotion_worker_agent(_pick_bundle(role_key="emotion_worker", model_bundles=model_bundles, role_provider=role_providers.get("emotion_worker"), default_provider=default_provider)),
        browser_worker=create_browser_worker_agent(_pick_bundle(role_key="browser_worker", model_bundles=model_bundles, role_provider=role_providers.get("browser_worker"), default_provider=default_provider)),
        doc_worker=create_doc_worker_agent(_pick_bundle(role_key="doc_worker", model_bundles=model_bundles, role_provider=role_providers.get("doc_worker"), default_provider=default_provider)),
    )


async def run_turn(colony: AntColony, user_text: str) -> str:
    user_msg = Msg(name="user", role="user", content=user_text)

    async with MsgHub(participants=colony.participants) as hub:
        await hub.broadcast(user_msg)

        current_msg: Msg | None = user_msg
        for _ in range(12):
            king_msg: Msg = await colony.king(current_msg)
            text = msg_to_text(king_msg)
            cmd = extract_first_json_obj(text)
            if not isinstance(cmd, dict):
                return text.strip()

            task_type = str(cmd.get("task_type") or "").strip()
            target_ant = str(cmd.get("target_ant") or "none").strip()
            task_params = cmd.get("task_params") or {}

            if task_type == "ask":
                return text.strip()

            if task_type == "final":
                if isinstance(task_params, dict):
                    reply = task_params.get("reply") or task_params.get("answer")
                    if isinstance(reply, str) and reply.strip():
                        return reply.strip()
                return text.strip()

            if task_type == "tool_create":
                soldier_result = await _handle_tool_create(colony, hub, task_params)
                current_msg = soldier_result
                continue

            if task_type == "dispatch":
                agent = colony.agent_map.get(target_ant)
                if agent is None:
                    return text.strip()

                worker_result = await _handle_dispatch(colony, hub, target_ant, task_params)
                current_msg = worker_result
                continue

            return text.strip()

    return "未能在限定步数内完成本轮任务。"


async def _handle_dispatch(
    colony: AntColony,
    hub: MsgHub,
    target_ant: str,
    task_params: Any,
) -> Msg:
    if not isinstance(task_params, dict):
        task_params = {"task": str(task_params)}

    if target_ant == "browser_worker":
        await _ensure_tool_ready(
            colony=colony,
            hub=hub,
            worker_agent=colony.browser_worker,
            worker_type="browser_worker",
            tool_name=str(task_params.get("tool_name") or "open_browser_search_image"),
        )

    if target_ant == "doc_worker":
        await _ensure_tool_ready(
            colony=colony,
            hub=hub,
            worker_agent=colony.doc_worker,
            worker_type="doc_worker",
            tool_name=str(task_params.get("tool_name") or "write_and_save_doc"),
        )

        if "save_path" not in task_params:
            root = os.path.dirname(os.path.dirname(__file__))
            task_params["save_path"] = os.path.join(root, "docs", "generated")

    payload = json.dumps(task_params, ensure_ascii=False)
    msg = make_msg(
        role="user",
        name="user",
        content=payload,
        metadata={"task_params": task_params, "target_ant": target_ant},
    )
    return await colony.agent_map[target_ant](msg)


async def _handle_tool_create(colony: AntColony, hub: MsgHub, task_params: Any) -> Msg:
    if not isinstance(task_params, dict):
        task_params = {"task": str(task_params)}

    payload = json.dumps(task_params, ensure_ascii=False)
    msg = make_msg(
        role="user",
        name="user",
        content=payload,
        metadata={"task_type": "tool_create", **task_params},
    )
    return await colony.soldier(msg)


async def _ensure_tool_ready(
    *,
    colony: AntColony,
    hub: MsgHub,
    worker_agent: Any,
    worker_type: str,
    tool_name: str,
) -> None:
    if hasattr(worker_agent, "toolkit") and tool_name in getattr(worker_agent.toolkit, "tools", {}):
        return

    tool_missing_msg = make_msg(
        role="assistant",
        name=getattr(worker_agent, "name", worker_type),
        content=f"缺少工具：{tool_name}",
        metadata={"tool_missing": True, "tool_name": tool_name, "worker_type": worker_type},
    )
    await hub.broadcast(tool_missing_msg)

    king_msg: Msg = await colony.king(tool_missing_msg)
    cmd = extract_first_json_obj(msg_to_text(king_msg)) or {}
    params = cmd.get("task_params") if isinstance(cmd, dict) else None

    if not isinstance(params, dict):
        params = {"worker_type": worker_type, "tool_name": tool_name, "skill_desc": "自动补齐缺失工具。"}

    await _handle_tool_create(colony, hub, params)

    if hasattr(worker_agent, "toolkit"):
        load_skills(worker_agent.toolkit, role_key=worker_type)
