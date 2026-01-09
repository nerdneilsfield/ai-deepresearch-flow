"""Claude provider implementation (Anthropic SDK)."""

from __future__ import annotations

from typing import Any

from deepresearch_flow.paper.providers.base import ProviderError


def _extract_text_blocks(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if not content:
        return None
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
            continue
        if isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "".join(parts) if parts else None


async def chat(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    anthropic_version: str,
    max_tokens: int = 2048,
) -> str:
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise ProviderError("anthropic package is not installed") from exc

    system_parts: list[str] = []
    claude_messages: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            system_parts.append(content)
        else:
            claude_messages.append({"role": role or "user", "content": content})

    system_prompt = "\n\n".join(system_parts).strip() or None

    client = AsyncAnthropic(
        api_key=api_key,
        default_headers={"anthropic-version": anthropic_version},
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=claude_messages,
        )
    except Exception as exc:  # pragma: no cover - SDK error
        raise ProviderError(str(exc), retryable=True) from exc

    content = _extract_text_blocks(response.content)
    if not content:
        raise ProviderError("Claude response missing content")
    return content
