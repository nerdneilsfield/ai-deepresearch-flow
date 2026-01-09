"""DashScope provider implementation."""

from __future__ import annotations

import json
from typing import Any

from deepresearch_flow.paper.providers.base import ProviderError


async def chat(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    try:
        from dashscope.aigc.generation import AioGeneration, Message
    except ImportError as exc:
        raise ProviderError("dashscope package is not installed") from exc

    ds_messages = [Message(role=item["role"], content=item["content"]) for item in messages]

    try:
        response = await AioGeneration.call(
            api_key=api_key,
            model=model,
            messages=ds_messages,
            result_format="message",
        )
    except Exception as exc:  # pragma: no cover - SDK error
        raise ProviderError(str(exc), retryable=True) from exc

    data = _normalize_response(response)
    status_code = data.get("status_code")
    if status_code and status_code != 200:
        retryable = status_code in (429, 500, 502, 503, 504)
        raise ProviderError(data.get("message") or "DashScope error", status_code=status_code, retryable=retryable)

    content = (
        data.get("output", {})
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content")
    )
    if not content:
        raise ProviderError("DashScope response missing content")
    return content


def _normalize_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "to_dict"):
        return response.to_dict()
    try:
        return json.loads(str(response))
    except json.JSONDecodeError:
        return {"message": str(response)}
