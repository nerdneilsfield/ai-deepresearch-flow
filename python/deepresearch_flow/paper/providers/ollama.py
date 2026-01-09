"""Ollama provider implementation."""

from __future__ import annotations

from typing import Any
import httpx

from deepresearch_flow.paper.providers.base import ProviderError


async def chat(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    structured_mode: str,
    headers: dict[str, str] | None,
    timeout: float,
) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if structured_mode in ("json_schema", "json_object"):
        payload["format"] = "json"

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.RequestError as exc:
        raise ProviderError(str(exc), retryable=True) from exc

    if response.status_code == 429:
        raise ProviderError(response.text, status_code=429, retryable=True)
    if response.status_code >= 500:
        raise ProviderError(response.text, status_code=response.status_code, retryable=True)
    if response.status_code >= 400:
        raise ProviderError(response.text, status_code=response.status_code)

    data = response.json()
    message = data.get("message", {})
    content = message.get("content")
    if not content:
        raise ProviderError("Ollama response missing content", status_code=response.status_code)
    return content
