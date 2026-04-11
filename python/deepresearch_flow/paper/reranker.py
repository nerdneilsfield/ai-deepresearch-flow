"""Rerank provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class RerankResult:
    """Rerank output with document indices and scores."""

    indices: list[int]
    scores: list[float]


class RerankProvider(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        client: httpx.AsyncClient,
    ) -> RerankResult: ...


def _parse_results(data: object) -> RerankResult:
    if not isinstance(data, dict):
        raise ValueError("Rerank response must be a JSON object")

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Rerank response missing results")

    indices: list[int] = []
    scores: list[float] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, int) and isinstance(score, (int, float)):
            indices.append(index)
            scores.append(float(score))

    return RerankResult(indices=indices, scores=scores)


class OpenAICompatibleReranker:
    """Reranker for OpenAI-compatible /rerank endpoints."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        client: httpx.AsyncClient,
    ) -> RerankResult:
        if not documents:
            raise ValueError("Rerank documents cannot be empty")

        url = self._base_url.rstrip("/") + "/rerank"
        response = await client.post(
            url,
            json={
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            },
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return _parse_results(response.json())
