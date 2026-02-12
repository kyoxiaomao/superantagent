from __future__ import annotations

from typing import Any

from agentscope.embedding import DashScopeMultiModalEmbedding


"""
reme不支持多模态
多模态嵌入适配器（独立于向量库约束）。

说明：
- 向量库仍只接受文本嵌入模型（DashScopeTextEmbedding 或 OpenAITextEmbedding）。
- 多模态嵌入模型用于图文/视频等场景的独立调用，不参与向量库预热。
"""


def _apply_dashscope_base_url(base_url: str) -> None:
    url = str(base_url or "").strip().rstrip("/")
    if url and "compatible-mode" not in url:
        import dashscope

        dashscope.base_http_api_url = url


def _normalize_multimodal_inputs(inputs: list[Any] | str) -> list[dict[str, Any]]:
    if isinstance(inputs, str):
        return [{"type": "text", "text": inputs}]
    if not isinstance(inputs, list):
        raise ValueError("多模态 embedding 输入格式不支持")
    output: list[dict[str, Any]] = []
    for item in inputs:
        if isinstance(item, str):
            output.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            if "type" in item:
                output.append(item)
            elif "text" in item:
                output.append({"type": "text", "text": str(item.get("text") or "")})
            else:
                raise ValueError("多模态 embedding 输入格式不支持")
        else:
            raise ValueError("多模态 embedding 输入格式不支持")
    return output


class DashScopeMultiModalEmbeddingAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str,
        dimensions: int | None,
    ) -> None:
        if not api_key:
            raise ValueError("未配置多模态 embedding api_key")
        _apply_dashscope_base_url(base_url)
        self._model = DashScopeMultiModalEmbedding(
            api_key=api_key,
            model_name=model_name,
            dimensions=dimensions,
        )

    async def __call__(self, inputs: list[Any] | str):
        normalized = _normalize_multimodal_inputs(inputs)
        return await self._model(normalized)


def build_multimodal_embedding_model(
    *,
    api_key: str,
    model_name: str,
    base_url: str,
    dimensions: int | None,
) -> DashScopeMultiModalEmbeddingAdapter:
    return DashScopeMultiModalEmbeddingAdapter(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        dimensions=dimensions,
    )
