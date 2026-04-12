"""Rerank provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import httpx

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised when dependency is unavailable
    tiktoken = None

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


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


def _encode_tokens(text: str) -> list[int] | list[str]:
    if tiktoken is not None:
        encoding = tiktoken.get_encoding("cl100k_base")
        return encoding.encode(text, disallowed_special=())
    return _TOKEN_RE.findall(text)


def _decode_tokens(tokens: list[int] | list[str]) -> str:
    if tiktoken is not None:
        encoding = tiktoken.get_encoding("cl100k_base")
        return encoding.decode(tokens)
    return " ".join(str(token) for token in tokens)


def _truncate_to_max_context(text: str, max_context: int) -> str:
    tokens = _encode_tokens(text)
    if len(tokens) <= max_context:
        return text
    return _decode_tokens(tokens[:max_context]).strip()


class OpenAICompatibleReranker:
    """Reranker for OpenAI-compatible /rerank endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_context: int,
        max_chunks_per_doc: int | None,
        instruction: str | None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._max_context = max_context
        self._max_chunks_per_doc = max_chunks_per_doc
        self._instruction = instruction

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

        prepared_documents = documents
        if self._max_chunks_per_doc is not None:
            prepared_documents = prepared_documents[: self._max_chunks_per_doc]
        prepared_documents = [
            _truncate_to_max_context(document, self._max_context) for document in prepared_documents
        ]

        url = self._base_url.rstrip("/") + "/rerank"
        payload: dict[str, object] = {
            "model": self._model,
            "query": query,
            "documents": prepared_documents,
            "top_n": min(top_n, len(prepared_documents)),
            "return_documents": False,
        }
        if self._max_chunks_per_doc is not None:
            payload["max_chunks_per_doc"] = self._max_chunks_per_doc
        if self._instruction is not None:
            payload["instruction"] = self._instruction

        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return _parse_results(response.json())
