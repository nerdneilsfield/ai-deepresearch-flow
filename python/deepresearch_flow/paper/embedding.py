"""OpenAI-compatible embedding API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from deepresearch_flow.paper.routing import RoutePool


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


async def call_embedding_with_route_pool(
    *,
    route_pool: RoutePool[Any, Any],
    texts: list[str],
    dimensions: int | None = None,
    client: httpx.AsyncClient,
) -> EmbeddingResult:
    """Select a concrete weighted route and execute one embedding request."""

    last_error: Exception | None = None
    max_attempts = max(route_pool.candidate_count * 2, 3)
    for _ in range(max_attempts):
        route = await route_pool.get()
        try:
            return await call_embedding(
                base_url=route.base.url,
                api_key=route.key.value,
                model=route.model.model_name,
                texts=texts,
                dimensions=dimensions,
                client=client,
            )
        except httpx.HTTPStatusError as exc:
            last_error = exc
            response = exc.response
            body = response.text if response is not None else str(exc)
            quota_hit = await route_pool.mark_quota_exceeded(
                route,
                body,
                response.status_code if response is not None else None,
            )
            if quota_hit:
                continue
            await route_pool.mark_error(route)
        except Exception as exc:
            last_error = exc
            await route_pool.mark_error(route)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Embedding route pool exhausted without producing vectors")
