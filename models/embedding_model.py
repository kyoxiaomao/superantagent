from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentscope.embedding import DashScopeMultiModalEmbedding, DashScopeTextEmbedding


@dataclass(frozen=True)
class EmbeddingBundle:
    # 模型打包结构，供模型管理器统一返回
    model: Any
    model_name: str
    dimensions: int


def _apply_dashscope_base_url(base_url: str) -> None:
    url = str(base_url or "").strip().rstrip("/")
    if url and "compatible-mode" not in url:
        import dashscope

        dashscope.base_http_api_url = url


def _normalize_texts(texts: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in texts:
        if isinstance(item, str):
            out.append({"type": "text", "text": item})
            continue
        if isinstance(item, dict) and "text" in item:
            out.append({"type": "text", "text": str(item.get("text") or "")})
            continue
        raise ValueError("向量模型输入格式不支持：必须是字符串列表或包含 text 的 dict 列表")
    return out


class DashScopeVLTextEmbedding(DashScopeTextEmbedding):
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str,
        dimensions: int,
    ) -> None:
        if not api_key:
            raise ValueError("未配置 embedding api_key")
        if not model_name:
            raise ValueError("未配置 embedding model_name")
        _apply_dashscope_base_url(base_url)
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            dimensions=dimensions,
        )
        self._mm = DashScopeMultiModalEmbedding(
            api_key=api_key,
            model_name=model_name,
            dimensions=dimensions,
        )

    async def __call__(self, text: list[Any], **kwargs: Any):
        normalized = _normalize_texts(text)
        return await self._mm(normalized, **kwargs)


def build_embedding_model(
    *,
    api_key: str,
    model_name: str,
    base_url: str,
    dimensions: int,
) -> Any:
    return DashScopeVLTextEmbedding(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        dimensions=dimensions,
    )
