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
    provider_type: str = "openai_compatible",
) -> EmbeddingResult:
    if not texts:
        raise ValueError("Embedding input must not be empty")

    normalized_provider_type = provider_type.strip().lower()
    if normalized_provider_type == "ollama":
        url = base_url.rstrip("/") + "/api/embed"
        body: dict[str, object] = {
            "model": model,
            "input": texts[0] if len(texts) == 1 else texts,
        }
        if dimensions is not None:
            body["dimensions"] = dimensions
        headers = {"Content-Type": "application/json"}
    elif normalized_provider_type == "openai_compatible":
        url = base_url.rstrip("/") + "/embeddings"
        body = {
            "model": model,
            "input": texts,
        }
        if dimensions is not None:
            body["dimensions"] = dimensions
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        raise ValueError(f"Unsupported embedding provider type: {provider_type}")

    response = await client.post(url, json=body, headers=headers, timeout=120.0)
    response.raise_for_status()

    data = response.json()
    if normalized_provider_type == "ollama":
        raw_vectors = data.get("embeddings")
        if not isinstance(raw_vectors, list):
            raise ValueError("Ollama embedding response missing 'embeddings' list")
        vectors = raw_vectors
        usage_tokens = int(data.get("prompt_eval_count") or 0)
    else:
        sorted_data = sorted(data["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in sorted_data]
        usage = data.get("usage", {})
        usage_tokens = int(usage.get("prompt_tokens", 0) or 0)
    return EmbeddingResult(
        vectors=vectors,
        model=model,
        usage_tokens=usage_tokens,
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
                provider_type=route.provider.type,
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
