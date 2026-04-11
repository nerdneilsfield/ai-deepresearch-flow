from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from deepresearch_flow.paper.embedding import EmbeddingResult, call_embedding


def _mock_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "bge-m3"
        assert isinstance(body["input"], list)
        count = len(body["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1] * 1024, "index": idx} for idx in range(count)
                ],
                "usage": {"prompt_tokens": count * 10},
            },
        )

    return httpx.MockTransport(handler)


def test_call_embedding_returns_vectors() -> None:
    async def _run() -> EmbeddingResult:
        transport = _mock_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            return await call_embedding(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                model="bge-m3",
                texts=["hello", "world"],
                dimensions=1024,
                client=client,
            )

    result = asyncio.run(_run())
    assert len(result.vectors) == 2
    assert len(result.vectors[0]) == 1024
    assert result.model == "bge-m3"
    assert result.usage_tokens == 20


def test_call_embedding_empty_input_raises() -> None:
    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            await call_embedding(
                base_url="http://localhost/v1",
                api_key="k",
                model="m",
                texts=[],
                client=client,
            )

    with pytest.raises(ValueError, match="empty"):
        asyncio.run(_run())
