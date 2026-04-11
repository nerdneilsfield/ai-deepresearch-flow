from __future__ import annotations

from pathlib import Path
import hmac

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.vector_store import ChunkRow, INDEX_VERSION, open_store, save_index_meta, write_chunks


def _create_test_embed_db(tmp_path: Path) -> Path:
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
            vector=[0.1] * 1024,
            title="Attention Is All You Need",
            year=2017,
            authors="Vaswani",
            venue="NeurIPS",
            tags="transformer",
        ),
    ]
    write_chunks(db, rows)
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

    embed_dir = _create_test_embed_db(tmp_path)
    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.embed_db = open_store(embed_dir)
    app.state.search_access_token = access_token
    app.state.paper_config = None
    return TestClient(app)


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
