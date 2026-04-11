from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from deepresearch_flow.paper.reranker import OpenAICompatibleReranker, RerankResult


def _mock_rerank_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.path.endswith("/rerank")
        assert request.headers["authorization"] == "Bearer key"
        assert request.headers["content-type"] == "application/json"
        assert body == {
            "model": "test-reranker",
            "query": "test query",
            "documents": ["doc a", "doc b", "doc c"],
            "top_n": 2,
            "return_documents": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "test",
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ],
            },
        )

    return httpx.MockTransport(handler)


def test_rerank_returns_results() -> None:
    async def _run() -> RerankResult:
        reranker = OpenAICompatibleReranker(
            base_url="http://localhost/v1",
            api_key="key",
            model="test-reranker",
        )
        transport = _mock_rerank_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            return await reranker.rerank(
                query="test query",
                documents=["doc a", "doc b", "doc c"],
                top_n=2,
                client=client,
            )

    result = asyncio.run(_run())
    assert result.indices == [1, 0]
    assert result.scores == [0.9, 0.8]


def test_rerank_empty_documents_raises() -> None:
    async def _run() -> None:
        reranker = OpenAICompatibleReranker(
            base_url="http://localhost/v1",
            api_key="key",
            model="test-reranker",
        )
        async with httpx.AsyncClient() as client:
            await reranker.rerank(query="q", documents=[], top_n=5, client=client)

    with pytest.raises(ValueError, match="empty"):
        asyncio.run(_run())
