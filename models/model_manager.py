"""
模型构建与配置加载。

只从 `configs/model_configs.yaml` 读取三类配置段，并构造 AgentScope 可用的模型对象：

- llm：对话大模型（ChatModel）
- embedding：向量/嵌入模型（Embedding）
- multimodal_embedding：多模态嵌入模型（仅用于多模态场景）

API Key 支持两种来源（用于开发期排障更直观）：

- 优先使用配置内 `api_key`
- 否则使用配置内 `api_key_env` 指向的环境变量
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml

from agentscope.formatter import OpenAIChatFormatter

from models.embedding_model import EmbeddingBundle, build_embedding_model
from models.glm_chat_model import GLMChatModel
from models.multimodal_embedding_model import build_multimodal_embedding_model


def _load_yaml(path: str) -> dict[str, Any]:
    # 统一读取模型配置，保证结构为 dict
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("model_configs.yaml 内容格式不正确，应为字典结构")
    return data


@dataclass(frozen=True)
class ModelBundle:
    model: Any
    formatter: OpenAIChatFormatter


@dataclass(frozen=True)
class MultimodalEmbeddingBundle:
    model: Any
    model_name: str
    dimensions: int | None


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _get_required(cfg: dict[str, Any], key: str) -> Any:
    val = cfg.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ValueError(f"配置文件缺失必要参数: {key}")
    return val


def _resolve_api_key(*, p: dict[str, Any], default_api_key_env: str) -> str:
    # 优先使用显式 api_key，其次读取 api_key_env 指向的环境变量
    api_key = str(p.get("api_key") or "").strip()
    if api_key:
        return api_key

    api_key_env = str(p.get("api_key_env") or default_api_key_env).strip() or default_api_key_env
    api_key = str(os.getenv(api_key_env) or "").strip()
    if not api_key:
        raise ValueError(f"未配置 api_key 或 {api_key_env}")
    return api_key


# ==================================================================================================
# =======================================  LLM（对话大模型）  =======================================
# ==================================================================================================

def load_model_bundles(config_path: str) -> tuple[dict[str, ModelBundle], str]:
    # 只加载 llm 段，避免混用 embedding 配置
    cfg = _load_yaml(config_path)
    default_provider = "llm"

    bundles: dict[str, ModelBundle] = {}
    if "llm" in cfg:
        bundles["llm"] = _build_llm_bundle(cfg=cfg)

    return bundles, default_provider


def load_model_bundle(config_path: str, *, provider: str | None = None) -> ModelBundle:
    if provider is not None and str(provider).strip().lower() != "llm":
        raise ValueError("LLM 只支持 provider=llm")
    bundles, default_provider = load_model_bundles(config_path)
    use = str(provider or default_provider or "").strip() or default_provider
    if use not in bundles:
        raise ValueError(f"未找到 provider={use} 的模型配置。可用：{sorted(bundles.keys())}")
    return bundles[use]


def load_glm_bundle(config_path: str) -> ModelBundle:
    return load_model_bundle(config_path, provider="llm")


def _build_llm_bundle(*, cfg: dict[str, Any]) -> ModelBundle:
    # llm 段只负责对话模型
    p = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    api_key = _resolve_api_key(p=p, default_api_key_env="GLM_API_KEY")
    
    model_name = str(_get_required(p, "model_name"))
    base_url = str(_get_required(p, "base_url"))
    stream = bool(p.get("stream", True))  # 业务默认值保留
    timeout_s = float(p.get("timeout_s", 120.0))  # 业务默认值保留
    include_thinking = _parse_bool(p.get("include_thinking", False))  # 业务默认值保留

    model = GLMChatModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        stream=stream,
        timeout_s=timeout_s,
        include_thinking=include_thinking,
    )
    formatter = OpenAIChatFormatter()
    return ModelBundle(model=model, formatter=formatter)


# ==================================================================================================
# ===================================  Embedding（向量/嵌入）  ======================================
# ==================================================================================================

def load_embedding_bundles(config_path: str) -> tuple[dict[str, EmbeddingBundle], str]:
    # 只加载 embedding 段，避免混用 llm 配置
    cfg = _load_yaml(config_path)
    default_provider = "embedding"

    bundles: dict[str, EmbeddingBundle] = {}
    if "embedding" in cfg:
        bundles["embedding"] = _build_embedding_bundle(cfg=cfg)

    return bundles, default_provider


def load_embedding_bundle(config_path: str, *, provider: str | None = None) -> EmbeddingBundle:
    if provider is not None and str(provider).strip().lower() != "embedding":
        raise ValueError("embedding 只支持 provider=embedding")
    bundles, default_provider = load_embedding_bundles(config_path)
    use = str(provider or default_provider or "").strip() or default_provider
    if use not in bundles:
        raise ValueError(f"未找到 provider={use} 的嵌入模型配置。可用：{sorted(bundles.keys())}")
    return bundles[use]


def _build_embedding_bundle(*, cfg: dict[str, Any]) -> EmbeddingBundle:
    # embedding 段只负责向量模型
    p = cfg.get("embedding") if isinstance(cfg.get("embedding"), dict) else {}
    api_key = _resolve_api_key(p=p, default_api_key_env="DASHSCOPE_API_KEY")
    
    model_name = str(_get_required(p, "model_name"))
    dimensions = int(_get_required(p, "embedding_dims"))
    base_url = str(_get_required(p, "base_url"))
    
    model = build_embedding_model(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        dimensions=dimensions,
    )
    return EmbeddingBundle(model=model, model_name=model_name, dimensions=dimensions)


"""
多模态嵌入加载区（与向量库解耦）。

说明：
- 向量库只接收文本嵌入模型；
- 多模态嵌入单独加载，供上层需要多模态能力时显式使用。
"""


def load_multimodal_embedding_bundles(config_path: str) -> tuple[dict[str, MultimodalEmbeddingBundle], str]:
    cfg = _load_yaml(config_path)
    default_provider = "multimodal_embedding"
    bundles: dict[str, MultimodalEmbeddingBundle] = {}
    if "multimodal_embedding" in cfg:
        bundles["multimodal_embedding"] = _build_multimodal_embedding_bundle(cfg=cfg)
    return bundles, default_provider


def load_multimodal_embedding_bundle(
    config_path: str,
    *,
    provider: str | None = None,
) -> MultimodalEmbeddingBundle:
    if provider is not None and str(provider).strip().lower() != "multimodal_embedding":
        raise ValueError("multimodal_embedding 只支持 provider=multimodal_embedding")
    bundles, default_provider = load_multimodal_embedding_bundles(config_path)
    use = str(provider or default_provider or "").strip() or default_provider
    if use not in bundles:
        raise ValueError(f"未找到 provider={use} 的多模态嵌入配置。可用：{sorted(bundles.keys())}")
    return bundles[use]


def _build_multimodal_embedding_bundle(*, cfg: dict[str, Any]) -> MultimodalEmbeddingBundle:
    p = cfg.get("multimodal_embedding") if isinstance(cfg.get("multimodal_embedding"), dict) else {}
    api_key = _resolve_api_key(p=p, default_api_key_env="DASHSCOPE_API_KEY")
    
    model_name = str(_get_required(p, "model_name"))
    dimensions = int(_get_required(p, "embedding_dims"))
    base_url = str(_get_required(p, "base_url"))

    model = build_multimodal_embedding_model(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        dimensions=dimensions,
    )
    return MultimodalEmbeddingBundle(model=model, model_name=model_name, dimensions=dimensions)
