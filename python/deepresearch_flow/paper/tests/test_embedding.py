from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from deepresearch_flow.paper.config import BaseConfig, EmbeddingModelConfig, EmbeddingProviderConfig, KeyConfig
from deepresearch_flow.paper.embedding import EmbeddingResult, call_embedding, call_embedding_with_route_pool
from deepresearch_flow.paper.routing import RoutePool


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


def test_call_embedding_ollama_uses_native_api_embed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        body = json.loads(request.content)
        assert body == {
            "model": "embeddinggemma",
            "input": "The quick brown fox jumps over the lazy dog.",
            "dimensions": 768,
        }
        assert "Authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "model": "embeddinggemma",
                "embeddings": [[0.1] * 768],
                "prompt_eval_count": 8,
            },
        )

    async def _run() -> EmbeddingResult:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await call_embedding(
                base_url="http://localhost:11434",
                api_key="ignored-by-ollama",
                model="embeddinggemma",
                texts=["The quick brown fox jumps over the lazy dog."],
                dimensions=768,
                client=client,
                provider_type="ollama",
            )

    result = asyncio.run(_run())
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == 768
    assert result.model == "embeddinggemma"
    assert result.usage_tokens == 8


def test_call_embedding_with_route_pool_passes_provider_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_call_embedding(
        base_url,
        api_key,
        model,
        texts,
        *,
        dimensions=None,
        client=None,
        provider_type="openai_compatible",
    ):  # noqa: ANN001
        seen.update(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "texts": list(texts),
                "dimensions": dimensions,
                "provider_type": provider_type,
            }
        )
        return EmbeddingResult(vectors=[[0.1] * 4], model=model, usage_tokens=1)

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_call_embedding)

    provider = EmbeddingProviderConfig(
        name="ollama",
        type="ollama",
        base=[BaseConfig(url="http://localhost:11434", weight=1, key=[KeyConfig(value="placeholder", weight=1)])],
        models=[EmbeddingModelConfig(model_name="embeddinggemma", dimensions=768, max_context=8192)],
    )
    route_pool = RoutePool.from_embedding_provider(
        type("ResolvedConfig", (), {"resolve_active": lambda self: (provider, provider.models[0])})()
    )

    async def _run() -> EmbeddingResult:
        async with httpx.AsyncClient() as client:
            return await call_embedding_with_route_pool(
                route_pool=route_pool,
                texts=["hello"],
                dimensions=4,
                client=client,
            )

    result = asyncio.run(_run())
    assert result.model == "embeddinggemma"
    assert seen == {
        "base_url": "http://localhost:11434",
        "api_key": "placeholder",
        "model": "embeddinggemma",
        "texts": ["hello"],
        "dimensions": 4,
        "provider_type": "ollama",
    }
