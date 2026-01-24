"""OpenAI-compatible provider implementation."""

from __future__ import annotations

from typing import Any
import httpx

from deepresearch_flow.paper.providers.base import ProviderError


def _extract_error_message(response: httpx.Response) -> str:
    parts: list[str] = []
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            for key in ("code", "type", "message"):
                value = error.get(key)
                if isinstance(value, str):
                    value = value.strip()
                    if value and value not in parts:
                        parts.append(value)
        elif isinstance(error, str):
            value = error.strip()
            if value and value not in parts:
                parts.append(value)
        for key in ("code", "type", "message"):
            value = data.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value and value not in parts:
                    parts.append(value)
    if parts:
        return " | ".join(parts)
    text = (response.text or "").strip()
    if text:
        return text
    reason = response.reason_phrase or "HTTP error"
    return f"{response.status_code} {reason}".strip()


async def chat(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    structured_mode: str,
    headers: dict[str, str] | None,
    timeout: float,
    schema: dict[str, Any] | None = None,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }

    if structured_mode == "json_schema" and schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "paper_extract",
                "schema": schema,
            },
        }
    elif structured_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.RequestError as exc:
        raise ProviderError(str(exc), retryable=True) from exc

    if response.status_code == 429:
        raise ProviderError(_extract_error_message(response), status_code=429, retryable=True)
    if response.status_code >= 500:
        raise ProviderError(
            _extract_error_message(response),
            status_code=response.status_code,
            retryable=True,
        )
    if response.status_code >= 400:
        structured_error = structured_mode in ("json_schema", "json_object")
        raise ProviderError(
            _extract_error_message(response),
            status_code=response.status_code,
            structured_error=structured_error,
        )

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("OpenAI-compatible response missing choices", status_code=response.status_code)
    message = choices[0].get("message", {})
    content = message.get("content")
    if not content:
        raise ProviderError("OpenAI-compatible response missing content", status_code=response.status_code)
    return content
