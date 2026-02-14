"""
终端模式入口。

负责初始化 AgentScope、加载模型配置与 colony，并启动精简运行时（ColonyRuntime），
从标准输入接收用户文本并输出蚁后回复。
"""

import os
from dotenv import load_dotenv
import asyncio
import yaml
from typing import Any

_项目根目录 = os.path.dirname(__file__)
load_dotenv(os.path.join(_项目根目录, ".env"))


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _setup_quiet_third_party_logs() -> None:
    if not _env_truthy("ANT_QUIET_THIRD_PARTY_LOGS", True):
        return
    try:
        import logging

        logging.getLogger().setLevel(logging.WARNING)
        for name in ["chromadb", "flowllm", "posthog"]:
            logging.getLogger(name).setLevel(logging.WARNING)
        _redirect_third_party_logs_to_file()
    except Exception:
        pass
    try:
        from loguru import logger

        level = str(os.getenv("ANT_LOGURU_LEVEL", "INFO") or "INFO").strip().upper()
        logger.remove()
        logger.add(_third_party_log_path(), level=level, encoding="utf-8")
    except Exception:
        pass


def _third_party_log_path() -> str:
    base_dir = os.path.dirname(__file__)
    logs_dir = os.path.join(base_dir, "memory", "vector_store", "_logs")
    os.makedirs(logs_dir, exist_ok=True)
    from datetime import datetime

    day = datetime.now().strftime("%Y%m%d")
    return os.path.join(logs_dir, f"third_party_{day}.log")


def _redirect_third_party_logs_to_file() -> None:
    import logging

    path = _third_party_log_path()
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")
    handler.setFormatter(formatter)
    for name in [
        "as",
        "reme_ai",
        "flowllm",
        "chromadb",
        "_reme_personal_long_term_memory",
    ]:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
        logger.addHandler(handler)


def _rebind_agentscope_logger_to_file() -> None:
    import logging

    path = _third_party_log_path()
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")
    handler.setFormatter(formatter)
    logger = logging.getLogger("as")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)


def main():
    asyncio.run(_main_async())


def _load_global_config() -> dict:
    path = os.path.join(os.path.dirname(__file__), "configs", "global_config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("global_config.yaml 内容格式不正确，应为字典结构")
    return data


async def _main_async() -> None:
    _setup_quiet_third_party_logs()
    import agentscope

    from services import load_model_bundles
    from services.event_logger import init_event_logger, log_event
    from services.startup_cleanup import maybe_cleanup_on_start
    from services.workflow import create_colony
    from services.runtime import ColonyRuntime

    maybe_cleanup_on_start(base_dir=_项目根目录)
    global_cfg = _load_global_config()
    event_log_path = init_event_logger(base_dir=_项目根目录, run_name="terminal")
    log_event(event_type="runtime_start", agent="system", payload={"mode": "terminal", "event_log": event_log_path})
    agentscope.init(
        project=os.getenv("AGENTSCOPE_PROJECT", "AntAgent"),
        name=os.getenv("AGENTSCOPE_NAME", "DesktopPet"),
        logging_level=str(global_cfg.get("logging_level") or "INFO"),
    )
    _rebind_agentscope_logger_to_file()

    model_bundles, default_provider = load_model_bundles(os.path.join(_项目根目录, "configs", "model_configs.yaml"))
    colony = create_colony(model_bundles=model_bundles, default_provider=default_provider, base_dir=_项目根目录)
    colony.disable_console_output()
    runtime = ColonyRuntime(colony=colony, enable_ui_events=False)
    await runtime.start()
    from services.runtime_context import set_current_runtime

    set_current_runtime(runtime)

    print("蚂蚁多智能体已启动。输入 exit 退出。")
    try:
        await _input_loop(runtime)
    finally:
        set_current_runtime(None)


async def _input_loop(runtime: Any) -> None:
    while True:
        text = await asyncio.to_thread(input, "你：")
        text = (text or "").strip()
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            await runtime.stop()
            return
        reply = await runtime.submit_user_text(text)
        if reply:
            print(f"蚁后_瑟拉：{reply}")

if __name__ == "__main__":
    main()
