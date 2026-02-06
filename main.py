"""
终端模式入口。

负责初始化 AgentScope、加载模型配置与 colony，并启动常驻群聊运行时（ColonyRuntime），
从标准输入接收用户文本并输出流式结果。
"""

import agentscope
import os
from dotenv import load_dotenv
import asyncio
import yaml
import requests

from agentscope.pipeline import stream_printing_messages

from services import load_glm_bundle
from services.event_logger import init_event_logger, log_event
from services.startup_cleanup import maybe_cleanup_on_start
from services.workflow import create_colony
from services.runtime import ColonyRuntime

# 加载环境变量
load_dotenv()

def main():
    asyncio.run(_main_async())


def _load_global_config() -> dict:
    path = os.path.join(os.path.dirname(__file__), "configs", "global_config.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def _main_async() -> None:
    maybe_cleanup_on_start(base_dir=os.path.dirname(__file__))
    global_cfg = _load_global_config()
    studio_url = str(global_cfg.get("studio_url") or "") or None
    event_log_path = init_event_logger(base_dir=os.path.dirname(__file__), run_name="terminal")
    log_event(event_type="runtime_start", agent="system", payload={"mode": "terminal", "event_log": event_log_path})
    try:
        agentscope.init(
            project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
            name=os.getenv("AGENTSCOPE_NAME", "DesktopPet"),
            logging_level=str(global_cfg.get("logging_level") or "INFO"),
            studio_url=studio_url,
        )
    except requests.exceptions.RequestException:
        agentscope.init(
            project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
            name=os.getenv("AGENTSCOPE_NAME", "DesktopPet"),
            logging_level=str(global_cfg.get("logging_level") or "INFO"),
        )

    model_bundle = load_glm_bundle(os.path.join(os.path.dirname(__file__), "configs", "model_configs.yaml"))
    colony = create_colony(model_bundle)
    colony.disable_console_output()
    runtime = ColonyRuntime(colony=colony)

    print("蚂蚁多智能体已启动。输入 exit 退出。")
    input_task = asyncio.create_task(_input_loop(runtime))
    async for msg, last in stream_printing_messages(runtime.participants, runtime.run_forever()):
        if last:
            print(msg)

    await input_task


async def _input_loop(runtime: ColonyRuntime) -> None:
    while True:
        text = await asyncio.to_thread(input, "你：")
        text = (text or "").strip()
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            await runtime.request_stop()
            return
        await runtime.submit_user_text(text)

if __name__ == "__main__":
    main()
