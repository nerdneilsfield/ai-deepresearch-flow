from __future__ import annotations

import asyncio
import sqlite3

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.errors import TotalFailureError, VectorStoreUnavailableError
from deepresearch_flow.paper.snapshot.advanced.pipeline import RequestSpec, run_advanced_search


class _SearchCfg:
    advanced_rrf_k = 60
    advanced_dense_top_k = 50
    advanced_sparse_top_k = 30
    advanced_post_fusion_top_k = 50
    advanced_dedup_cosine_threshold = 0.95
    advanced_rerank_top_n = 20
    advanced_mmr_lambda_default = 0.6
    advanced_rerank_timeout_ms = 1500
    advanced_top_n_max = 50
    advanced_max_query_length = 500


class _EmbeddingCfg:
    default_model = "bge-m3"
    dimensions = 2

    def resolve_active(self):
        class Provider:
            name = "ollama"

        class Model:
            model_name = "bge-m3"
            canonical_name = "bge-m3"
            dimensions = 2

        return Provider(), Model()


class _RerankCfg:
    enabled = True

    def resolve_active(self):
        class Provider:
            name = "rerank"

        class Model:
            model_name = "bge-reranker-v2-m3"

        return Provider(), Model()


class _PaperCfg:
    embedding = _EmbeddingCfg()
    rerank = _RerankCfg()


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows

    def open_table(self, name):
        return self

    def search(self, *args, **kwargs):
        return self

    def where(self, clause):
        return self

    def limit(self, n):
        return self

    def to_list(self):
        return list(self.rows)


class _BadLance:
    def open_table(self, name):
        raise RuntimeError("nope")


class _Ctx:
    def __init__(self, paper_rows, *, lance_ok=True):
        self.embedding_route_pool = object()
        self.rerank_route_pool = object()
        self.search_config = _SearchCfg()
        self.paper_config = _PaperCfg()
        self.lance_db = _FakeLance(paper_rows) if lance_ok else _BadLance()
        self.embed_db_path = "/tmp/embed_db"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY, title TEXT, year TEXT,
          venue TEXT, source_hash TEXT, doi TEXT, output_language TEXT
        );
        CREATE VIRTUAL TABLE paper_fts USING fts5(
          paper_id UNINDEXED, title, summary, source, translated, metadata,
          tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE paper_fts_trigram USING fts5(
          paper_id UNINDEXED, title, venue, tokenize='trigram'
        );
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER);
        INSERT INTO paper VALUES ('p1','Vision','2023','ICLR','h','10.x','en');
        INSERT INTO paper_fts (paper_id,title,summary,source,translated,metadata)
          VALUES ('p1','Vision','vision transformer','','','meta');
        INSERT INTO author VALUES (1,'Alice');
        INSERT INTO paper_author VALUES ('p1',1);
        """
    )
    return connection


def _dense_row(paper_id: str, distance: float = 0.1):
    return {
        "id": f"{paper_id}_c0",
        "doc_id": paper_id,
        "_distance": distance,
        "text": "body",
        "field_name": "simple/content",
        "template_tag": "simple",
        "chunk_type": "content",
        "chunk_index": 0,
        "lang": "en",
        "vector": [0.5, 0.5],
    }


def test_happy_path(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter, retrieve_dense

    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.5, 0.5]]
            model = "bge-m3"
            usage_tokens = 0

        return Result()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [_dense_row("p1")]

    async def fake_rerank(**kwargs):
        class Outcome:
            success = True
            reason = None
            chunks = kwargs["chunks"]
            scores = [0.9]

        return Outcome()

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    output = asyncio.run(
        run_advanced_search(
            request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-1"),
            ctx=_Ctx([_dense_row("p1")]),
            conn=conn,
            client=object(),
        )
    )
    assert output["success"] is True
    assert output["degraded"] is False
    assert output["results"][0]["paper_id"] == "p1"
    assert set(output["metadata"]["latency_ms"]) == {
        "embed",
        "dense",
        "sparse",
        "fusion",
        "chunk_select",
        "dedup",
        "rerank",
        "mmr",
        "total",
    }


def test_dense_failure_degrades_to_sparse_only(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter, retrieve_dense

    async def raise_embed(**kwargs):
        raise RuntimeError("embedding down")

    async def fake_rerank(**kwargs):
        class Outcome:
            success = True
            reason = None
            chunks = kwargs["chunks"]
            scores = [0.9 for _ in kwargs["chunks"]]

        return Outcome()

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    output = asyncio.run(
        run_advanced_search(
            request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-2"),
            ctx=_Ctx([_dense_row("p1")]),
            conn=conn,
            client=object(),
        )
    )
    assert output["degraded"] is True
    assert output["degradation"]["reason"] == "embedding_failed"


def test_sparse_failure_degrades_to_dense_only(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import (
        rerank_adapter,
        retrieve_dense,
        retrieve_sparse,
    )

    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.5, 0.5]]
            model = "bge-m3"
            usage_tokens = 0

        return Result()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [_dense_row("p1")]

    def raise_sparse(**kwargs):
        raise RuntimeError("fts down")

    async def fake_rerank(**kwargs):
        class Outcome:
            success = True
            reason = None
            chunks = kwargs["chunks"]
            scores = [0.9 for _ in kwargs["chunks"]]

        return Outcome()

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)
    monkeypatch.setattr(retrieve_sparse, "sparse_retrieve", raise_sparse)
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    output = asyncio.run(
        run_advanced_search(
            request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-fts"),
            ctx=_Ctx([]),
            conn=conn,
            client=object(),
        )
    )
    assert output["degraded"] is True
    assert output["degradation"]["reason"] == "fts_unavailable"
    assert output["results"][0]["paper_id"] == "p1"


def test_rerank_failure_degrades(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter, retrieve_dense

    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.5, 0.5]]
            model = "bge-m3"
            usage_tokens = 0

        return Result()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [_dense_row("p1")]

    async def fake_rerank(**kwargs):
        class Outcome:
            success = False
            reason = "reranker_failed"
            chunks = kwargs["chunks"]
            scores = []

        return Outcome()

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    output = asyncio.run(
        run_advanced_search(
            request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-3"),
            ctx=_Ctx([]),
            conn=conn,
            client=object(),
        )
    )
    assert output["degraded"] is True
    assert output["degradation"]["reason"] == "reranker_failed"


def test_deduped_count_preserved_after_rerank(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import (
        chunk_select as chunk_select_mod,
        rerank_adapter,
        retrieve_dense,
    )

    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.5, 0.5]]
            model = "bge-m3"
            usage_tokens = 0

        return Result()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [_dense_row("p1")]

    def fake_select_chunks(**kwargs):
        return [
            SelectedChunk(
                paper_id="p1",
                chunk_id="p1_c0",
                chunk_text="body-0",
                field_name="simple/content",
                template_tag="simple",
                chunk_type="content",
                chunk_index=0,
                lang="en",
                vector=(1.0, 0.0),
                fused_score=0.5,
                paper_dense_score=0.9,
                paper_sparse_score=None,
                dense_score=0.9,
            ),
            SelectedChunk(
                paper_id="p1",
                chunk_id="p1_c1",
                chunk_text="body-1",
                field_name="simple/content",
                template_tag="simple",
                chunk_type="content",
                chunk_index=1,
                lang="en",
                vector=(0.0, 1.0),
                fused_score=0.4,
                paper_dense_score=0.8,
                paper_sparse_score=None,
                dense_score=0.8,
            ),
        ]

    async def fake_rerank(**kwargs):
        class Outcome:
            success = True
            reason = None
            chunks = kwargs["chunks"][:1]
            scores = [0.95]

        return Outcome()

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)
    monkeypatch.setattr(chunk_select_mod, "select_chunks", fake_select_chunks)
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    output = asyncio.run(
        run_advanced_search(
            request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-counts"),
            ctx=_Ctx([]),
            conn=conn,
            client=object(),
        )
    )
    assert output["metadata"]["counts"]["deduped"] == 2
    assert output["metadata"]["counts"]["reranked"] == 1


def test_total_failure_raises(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def raise_embed(**kwargs):
        raise RuntimeError("embed down")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)

    with pytest.raises(TotalFailureError):
        asyncio.run(
            run_advanced_search(
                request_spec=RequestSpec("nonexistentqueryxyz", 10, 0.6, "auto", {}, "t-4"),
                ctx=_Ctx([]),
                conn=conn,
                client=object(),
            )
        )


def test_lance_unavailable_in_chunk_select(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def raise_embed(**kwargs):
        raise RuntimeError("embed down")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)

    with pytest.raises(VectorStoreUnavailableError):
        asyncio.run(
            run_advanced_search(
                request_spec=RequestSpec("vision", 10, 0.6, "auto", {}, "t-5"),
                ctx=_Ctx([], lance_ok=False),
                conn=conn,
                client=object(),
            )
        )
