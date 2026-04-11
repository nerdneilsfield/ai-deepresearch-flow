"""OpenAI-compatible embedding API client."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    usage_tokens: int


async def call_embedding(
    base_url: str,
    api_key: str,
    model: str,
    texts: list[str],
    *,
    dimensions: int | None = None,
    client: httpx.AsyncClient,
) -> EmbeddingResult:
    if not texts:
        raise ValueError("Embedding input must not be empty")

    url = base_url.rstrip("/") + "/embeddings"
    body: dict[str, object] = {
        "model": model,
        "input": texts,
    }
    if dimensions is not None:
        body["dimensions"] = dimensions

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = await client.post(url, json=body, headers=headers, timeout=120.0)
    response.raise_for_status()

    data = response.json()
    sorted_data = sorted(data["data"], key=lambda item: item["index"])
    vectors = [item["embedding"] for item in sorted_data]
    usage = data.get("usage", {})
    return EmbeddingResult(
        vectors=vectors,
        model=model,
        usage_tokens=usage.get("prompt_tokens", 0),
    )
