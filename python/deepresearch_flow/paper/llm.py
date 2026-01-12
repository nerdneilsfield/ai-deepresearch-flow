"""Shared LLM call helpers."""

from __future__ import annotations

from typing import Any
import httpx

from deepresearch_flow.paper.config import ProviderConfig
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.providers.azure_openai import chat as azure_openai_chat
from deepresearch_flow.paper.providers.claude import chat as claude_chat
from deepresearch_flow.paper.providers.dashscope import chat as dashscope_chat
from deepresearch_flow.paper.providers.gemini import (
    chat_ai_studio as gemini_ai_studio_chat,
    chat_vertex as gemini_vertex_chat,
)
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
    max_tokens: int | None = None,
) -> str:
    headers = dict(provider.extra_headers)
    if api_key and provider.type == "openai_compatible":
        headers.setdefault("Authorization", f"Bearer {api_key}")
    elif api_key and provider.type == "azure_openai":
        headers.setdefault("api-key", api_key)

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

    if provider.type == "gemini_ai_studio":
        if not api_key:
            raise ProviderError("gemini_ai_studio provider requires api_key")
        return await gemini_ai_studio_chat(api_key=api_key, model=model, messages=messages)

    if provider.type == "gemini_vertex":
        if not provider.project_id or not provider.location:
            raise ProviderError("gemini_vertex provider requires project_id and location")
        return await gemini_vertex_chat(
            project_id=provider.project_id,
            location=provider.location,
            credentials_path=provider.credentials_path,
            model=model,
            messages=messages,
        )

    if provider.type == "azure_openai":
        if not api_key:
            raise ProviderError("azure_openai provider requires api_key")
        if not provider.deployment or not provider.api_version:
            raise ProviderError("azure_openai provider requires deployment and api_version")
        return await azure_openai_chat(
            client,
            provider.base_url,
            provider.deployment,
            provider.api_version,
            messages,
            structured_mode,
            headers,
            timeout,
            schema,
        )

    if provider.type == "claude":
        if not api_key:
            raise ProviderError("claude provider requires api_key")
        if not provider.anthropic_version:
            raise ProviderError("claude provider requires anthropic_version")
        return await claude_chat(
            api_key=api_key,
            model=model,
            messages=messages,
            anthropic_version=provider.anthropic_version,
            max_tokens=max_tokens or 2048,
        )

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
