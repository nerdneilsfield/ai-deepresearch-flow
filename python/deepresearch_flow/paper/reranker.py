"""Rerank provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol

import httpx

from deepresearch_flow.paper.routing import RoutePool

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_ENCODING: Any | None = None
_TIKTOKEN_AVAILABLE: bool | None = None
tiktoken: Any | None = None


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


class _RerankRouteModel(Protocol):
    model_name: str
    max_context: int
    max_chunks_per_doc: int | None
    instruction: str | None


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
    encoding = _get_encoding()
    if encoding is not None:
        return encoding.encode(text, disallowed_special=())
    return _TOKEN_RE.findall(text)


def _decode_tokens(tokens: list[int] | list[str]) -> str:
    encoding = _get_encoding()
    if encoding is not None:
        return encoding.decode(tokens)
    return " ".join(str(token) for token in tokens)


def _get_encoding() -> Any | None:
    global _ENCODING, _TIKTOKEN_AVAILABLE, tiktoken
    if _TIKTOKEN_AVAILABLE is False:
        return None
    if _ENCODING is not None:
        return _ENCODING
    try:
        import tiktoken
    except ImportError:  # pragma: no cover - exercised when dependency is unavailable
        _TIKTOKEN_AVAILABLE = False
        return None
    _TIKTOKEN_AVAILABLE = True
    _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


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


class RoutedReranker:
    """Reranker that selects a concrete weighted route per request."""

    def __init__(self, *, route_pool: RoutePool[Any, _RerankRouteModel]) -> None:
        self._route_pool = route_pool

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

        last_error: Exception | None = None
        max_attempts = max(self._route_pool.candidate_count * 2, 3)
        for _ in range(max_attempts):
            route = await self._route_pool.get()
            model = route.model
            reranker = OpenAICompatibleReranker(
                base_url=route.base.url,
                api_key=route.key.value,
                model=model.model_name,
                max_context=model.max_context,
                max_chunks_per_doc=model.max_chunks_per_doc,
                instruction=model.instruction,
            )
            try:
                return await reranker.rerank(
                    query=query,
                    documents=documents,
                    top_n=top_n,
                    client=client,
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                response = exc.response
                body = response.text if response is not None else str(exc)
                quota_hit = await self._route_pool.mark_quota_exceeded(
                    route,
                    body,
                    response.status_code if response is not None else None,
                )
                if quota_hit:
                    continue
                await self._route_pool.mark_error(route)
            except Exception as exc:  # pragma: no cover - exercised by callers via black-box tests
                last_error = exc
                await self._route_pool.mark_error(route)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Rerank route pool exhausted without producing a result")
