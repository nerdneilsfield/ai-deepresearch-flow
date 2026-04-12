from __future__ import annotations

import asyncio
import hmac
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.config import (
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    PaperConfig,
    RerankConfig,
    RerankModelConfig,
    RerankProviderConfig,
    SearchConfig,
)
from deepresearch_flow.paper.vector_store import ChunkRow, INDEX_VERSION, open_store, save_index_meta, write_chunks


def _create_test_embed_db(tmp_path: Path, *, dimensions: int = 1024) -> Path:
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    db = open_store(embed_dir)
    rows = [
        ChunkRow(
            id="doc1__shared_title_0",
            doc_id="doc1",
            source_path="test.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Attention Is All You Need",
            content_hash="abc",
            vector=[0.1] * dimensions,
            title="Attention Is All You Need",
            year=2017,
            authors="Vaswani",
            venue="NeurIPS",
            tags="transformer",
        ),
    ]
    write_chunks(db, rows, dimensions=dimensions)
    save_index_meta(
        embed_dir,
        {
            "model": "bge-m3",
            "dimensions": 1024,
            "normalized": True,
            "provider": "test",
            "index_version": INDEX_VERSION,
        },
    )
    return embed_dir


def _make_app(tmp_path: Path, *, access_token: str | None = None) -> TestClient:
    from deepresearch_flow.paper.web.handlers.api import api_papers_semantic

    embed_dir = _create_test_embed_db(tmp_path, dimensions=1024)
    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.embed_db = open_store(embed_dir)
    app.state.search_access_token = access_token
    app.state.paper_config = None
    return TestClient(app)


def _paper_config(*, embedding_api_key: str = "ollama", rerank_api_key: str = "rerank") -> PaperConfig:
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[],
        main_model=[],
        embedding=EmbeddingConfig(
            default_model="bge-m3",
            default_provider="ollama",
            dimensions=1024,
            normalized=True,
            batch_size=2,
            chunk_max_tokens=512,
            chunk_overlap_tokens=64,
            providers=[
                EmbeddingProviderConfig(
                    name="ollama",
                    type="openai_compatible",
                    base_url="http://localhost:11434/v1",
                    api_key=embedding_api_key,
                    models=[EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)],
                )
            ],
        ),
        rerank=RerankConfig(
            enabled=True,
            default_model="bge-reranker-v2-m3",
            default_provider="siliconflow",
            top_n=10,
            providers=[
                RerankProviderConfig(
                    name="siliconflow",
                    type="openai_compatible",
                    base_url="https://api.siliconflow.cn/v1",
                    api_key=rerank_api_key,
                    models=[
                        RerankModelConfig(
                            model_name="bge-reranker-v2-m3",
                            max_context=2048,
                            max_chunks_per_doc=64,
                            instruction="Rank by relevance",
                        )
                    ],
                )
            ],
        ),
        search=SearchConfig(vector_dir="paper_vectors", vector_top_k=50, keyword_top_k=30, hybrid=True),
    )


def test_semantic_returns_403_without_token(tmp_path: Path) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    response = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert response.status_code == 403


def test_semantic_returns_403_wrong_token(tmp_path: Path) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    response = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403


def test_semantic_uses_constant_time_token_compare(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    seen = []
    real_compare = hmac.compare_digest

    def fake_compare(left, right):  # noqa: ANN001
        seen.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api.hmac.compare_digest", fake_compare)

    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)

    response = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert seen == [("secret-token", "secret-token")]


def test_semantic_returns_200_correct_token(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token="secret-token")

    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)

    response = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data


def test_semantic_open_when_no_token_configured(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token=None)

    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)

    response = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert response.status_code == 200


def test_semantic_returns_400_for_invalid_venue_filter(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token=None)

    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)

    response = client.get("/api/papers/semantic?q=attention&venue=NeurIPS' OR 1=1")
    assert response.status_code == 400


def test_semantic_probe_does_not_call_embed_query(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token="secret-token")

    async def boom_embed_query(text, config, client_obj):  # noqa: ARG001
        raise AssertionError("probe should not embed")

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", boom_embed_query)

    response = client.get(
        "/api/papers/semantic?probe=1",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_semantic_embedding_failure_returns_502(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token=None)

    async def failing_embed_query(text, config, client_obj):  # noqa: ARG001
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", failing_embed_query)

    response = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert response.status_code == 502
    assert response.json()["error"] == "Semantic search query embedding failed"


def test_embed_query_uses_embedding_resolve_active(monkeypatch) -> None:
    from deepresearch_flow.paper.web.handlers.api import _embed_query

    def boom_select_runtime_route(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("select_runtime_route should not be used for semantic embeddings")

    monkeypatch.setattr("deepresearch_flow.paper.routing.select_runtime_route", boom_select_runtime_route)

    seen: dict[str, object] = {}

    async def fake_call_embedding(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ANN001
        seen.update(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "dimensions": dimensions,
                "texts": list(texts),
            }
        )
        return type("EmbeddingResult", (), {"vectors": [[0.1] * 1024]})()

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_call_embedding)

    async def _run() -> list[float]:
        async with httpx.AsyncClient() as client:
            return await _embed_query("attention", _paper_config(embedding_api_key="resolved-embed-key"), client)

    vector = asyncio.run(_run())

    assert len(vector) == 1024
    assert seen == {
        "base_url": "http://localhost:11434/v1",
        "api_key": "resolved-embed-key",
        "model": "bge-m3",
        "dimensions": 1024,
        "texts": ["attention"],
    }


def test_semantic_builds_reranker_from_rerank_config(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token=None)
    client.app.state.paper_config = _paper_config(rerank_api_key="resolved-rerank-key")

    def boom_select_runtime_route(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("select_runtime_route should not be used for semantic rerank")

    monkeypatch.setattr("deepresearch_flow.paper.routing.select_runtime_route", boom_select_runtime_route)

    async def fake_embed_query(*args, **kwargs):  # noqa: ANN002, ANN003
        return [0.1] * 1024

    monkeypatch.setattr("deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query)

    seen: dict[str, object] = {}

    class FakeReranker:
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
            seen.update(
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                    "max_context": max_context,
                    "max_chunks_per_doc": max_chunks_per_doc,
                    "instruction": instruction,
                }
            )

    async def fake_hybrid_search(**kwargs):  # noqa: ANN003
        assert kwargs["reranker"] is not None
        return []

    monkeypatch.setattr("deepresearch_flow.paper.reranker.OpenAICompatibleReranker", FakeReranker)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    response = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert response.status_code == 200
    assert seen == {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "resolved-rerank-key",
        "model": "bge-reranker-v2-m3",
        "max_context": 2048,
        "max_chunks_per_doc": 64,
        "instruction": "Rank by relevance",
    }
