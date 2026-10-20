"""OpenAI-compatible embedding API client."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import time
from typing import Any

import httpx

from deepresearch_flow.paper.llm import backoff_delay
from deepresearch_flow.paper.routing import RoutePool
from deepresearch_flow.paper.utils import estimate_tokens


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


class EmbeddingRateLimiter:
    """Limit embedding requests and estimated input tokens over a rolling minute."""

    def __init__(self, *, tpm: int | None = None, rpm: int | None = None) -> None:
        if tpm is not None and tpm <= 0:
            raise ValueError("TPM limit must be positive")
        if rpm is not None and rpm <= 0:
            raise ValueError("RPM limit must be positive")
        self.tpm = tpm
        self.rpm = rpm
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, texts: list[str]) -> None:
        if self.tpm is None and self.rpm is None:
            return
        token_count = sum(estimate_tokens(len(text)) for text in texts)
        if self.tpm is not None and token_count > self.tpm:
            raise ValueError(
                f"Embedding batch needs about {token_count} tokens, more than the "
                f"{self.tpm} tokens/minute limit"
            )

        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - 60.0
                while self._events and self._events[0][0] <= cutoff:
                    self._events.popleft()
                used_tokens = sum(tokens for _, tokens in self._events)
                request_limit_hit = self.rpm is not None and len(self._events) >= self.rpm
                token_limit_hit = self.tpm is not None and used_tokens + token_count > self.tpm
                if not request_limit_hit and not token_limit_hit:
                    self._events.append((now, token_count))
                    return
                wait_until = self._events[0][0] + 60.0
            await asyncio.sleep(max(0.0, wait_until - time.monotonic()))


async def call_embedding_with_route_pool(
    *,
    route_pool: RoutePool[Any, Any],
    texts: list[str],
    dimensions: int | None = None,
    client: httpx.AsyncClient,
    rate_limiter: EmbeddingRateLimiter | None = None,
) -> EmbeddingResult:
    """Select a concrete weighted route and execute one embedding request."""

    last_error: Exception | None = None
    max_attempts = max(route_pool.candidate_count * 2, 3)
    if rate_limiter is not None:
        await rate_limiter.acquire(texts)
    for attempt in range(max_attempts):
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
            if not quota_hit:
                await route_pool.mark_error(route)
        except Exception as exc:
            last_error = exc
            await route_pool.mark_error(route)
        if attempt < max_attempts - 1:
            await asyncio.sleep(backoff_delay(0.25, attempt + 1, 5.0))

    if last_error is not None:
        raise last_error
    raise RuntimeError("Embedding route pool exhausted without producing vectors")
