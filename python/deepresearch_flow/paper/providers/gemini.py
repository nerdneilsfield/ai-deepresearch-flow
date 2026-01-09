"""Gemini provider implementation (AI Studio and Vertex)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from deepresearch_flow.paper.providers.base import ProviderError


def _build_prompt(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _normalize_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "to_dict"):
        return response.to_dict()
    try:
        return json.loads(str(response))
    except json.JSONDecodeError:
        return {"message": str(response)}


def _extract_text(response: Any) -> str | None:
    text = getattr(response, "text", None)
    if text:
        return text

    data = _normalize_response(response)
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        parts = content.get("parts") or []
        for part in parts:
            part_text = part.get("text")
            if part_text:
                return part_text
    return None


async def chat_ai_studio(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    try:
        from google import genai
    except ImportError as exc:
        raise ProviderError("google-genai package is not installed") from exc

    prompt = _build_prompt(messages)
    client = genai.Client(api_key=api_key)

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=prompt,
        )
    except Exception as exc:  # pragma: no cover - SDK error
        raise ProviderError(str(exc), retryable=True) from exc

    content = _extract_text(response)
    if not content:
        raise ProviderError("Gemini response missing content")
    return content


async def chat_vertex(
    project_id: str,
    location: str,
    credentials_path: str | None,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    try:
        from google import genai
    except ImportError as exc:
        raise ProviderError("google-genai package is not installed") from exc

    credentials = None
    if credentials_path:
        try:
            from google.auth import load_credentials_from_file
        except ImportError as exc:
            raise ProviderError("google-auth package is not installed") from exc
        credentials, _ = load_credentials_from_file(credentials_path)

    prompt = _build_prompt(messages)
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=prompt,
        )
    except Exception as exc:  # pragma: no cover - SDK error
        raise ProviderError(str(exc), retryable=True) from exc

    content = _extract_text(response)
    if not content:
        raise ProviderError("Gemini response missing content")
    return content
