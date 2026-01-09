"""Shared LLM call helpers."""

from __future__ import annotations

from typing import Any
import httpx

from deepresearch_flow.paper.config import ProviderConfig
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.providers.dashscope import chat as dashscope_chat
from deepresearch_flow.paper.providers.ollama import chat as ollama_chat
from deepresearch_flow.paper.providers.openai_compatible import chat as openai_chat


def backoff_delay(base: float, attempt: int, max_delay: float) -> float:
    delay = base * (2 ** max(attempt - 1, 0))
    return min(delay, max_delay)


async def call_provider(
    provider: ProviderConfig,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    api_key: str | None,
    timeout: float,
    structured_mode: str,
    client: httpx.AsyncClient,
) -> str:
    headers = dict(provider.extra_headers)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")

    if provider.type == "ollama":
        return await ollama_chat(
            client,
            provider.base_url,
            model,
            messages,
            structured_mode,
            headers,
            timeout,
        )

    if provider.type == "dashscope":
        if not api_key:
            raise ProviderError("dashscope provider requires api_key")
        return await dashscope_chat(api_key=api_key, model=model, messages=messages)

    if provider.type == "openai_compatible":
        return await openai_chat(
            client,
            provider.base_url,
            model,
            messages,
            structured_mode,
            headers,
            timeout,
            schema,
        )

    raise ProviderError(f"Unsupported provider type: {provider.type}")
