from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.advanced import AdvancedSearchContext, create_advanced_routes


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows
        self._sel = rows

    def open_table(self, name):
        return self

    def search(self, *args, **kwargs):
        return self

    def where(self, clause):
        self._sel = list(self.rows)
        return self

    def limit(self, n):
        self._sel = self._sel[:n]
        return self

    def to_list(self):
        return list(self._sel)


class _EmbedModel:
    model_name = "bge-m3"
    canonical_name = "bge-m3"
    dimensions = 2


class _EmbedProvider:
    name = "ollama"


class _EmbeddingCfg:
    default_model = "bge-m3"
    default_provider = "ollama"
    dimensions = 2
    normalized = True

    def resolve_active(self):
        return _EmbedProvider(), _EmbedModel()


class _RerankCfg:
    enabled = False


class _PaperCfg:
    embedding = _EmbeddingCfg()
    rerank = _RerankCfg()


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


def _build_app(tmp_path: Path, monkeypatch) -> tuple[Starlette, Path]:
    db_path = tmp_path / "snap.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY, title TEXT, year TEXT, venue TEXT,
          source_hash TEXT, doi TEXT, output_language TEXT
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
    conn.commit()
    conn.close()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.5, 0.5]]
            model = "bge-m3"
            usage_tokens = 0

        return Result()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [
            {
                "id": "p1_c0",
                "doc_id": "p1",
                "_distance": 0.1,
                "text": "body",
                "field_name": "simple/content",
                "template_tag": "simple",
                "chunk_type": "content",
                "chunk_index": 0,
                "lang": "en",
                "vector": [0.5, 0.5],
            }
        ]

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)

    ctx = AdvancedSearchContext(
        embed_db_path=tmp_path / "lance",
        lance_db=_FakeLance([]),
        paper_config=_PaperCfg(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token="secret",
        search_config=_SearchCfg(),
    )
    app = Starlette(routes=create_advanced_routes(ctx))
    app.state.advanced = ctx
    app.state.cfg = SimpleNamespace(snapshot_db=db_path)
    return app, db_path


def test_verify_token_missing(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post("/api/v1/search/advanced/verify-token")
    assert response.status_code == 401
    assert response.json() == {"valid": False, "reason": "missing"}


def test_verify_token_invalid(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/v1/search/advanced/verify-token",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401
    assert response.json() == {"valid": False, "reason": "invalid"}


def test_verify_token_ok(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/v1/search/advanced/verify-token",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": True}


def test_search_happy_path(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["degraded"] is False
    assert body["results"][0]["paper_id"] == "p1"


def test_search_missing_token(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get("/api/v1/search/advanced?q=vision")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.json()["error"]["details"]["reason"] == "missing"


def test_search_invalid_token(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer bad"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "invalid"


def test_search_empty_query(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_search_bad_filter_venue(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision&filters.venue=drop;table",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILTER"


def test_search_invalid_rerank_value(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision&rerank=bogus",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_search_rerank_never_skips_reranker(tmp_path, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter

    app, _db = _build_app(tmp_path, monkeypatch)

    async def boom_rerank(**kwargs):
        raise AssertionError("reranker should be skipped")

    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", boom_rerank)

    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision&rerank=never",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["reranker"]["applied"] is False


def test_search_rerank_always_applies_reranker(tmp_path, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter

    app, _db = _build_app(tmp_path, monkeypatch)

    class _EnabledRerankCfg:
        enabled = True

        def resolve_active(self):
            class Provider:
                name = "rerank"

            class Model:
                model_name = "bge-reranker-v2-m3"

            return Provider(), Model()

    class _PaperCfgWithRerank:
        embedding = _EmbeddingCfg()
        rerank = _EnabledRerankCfg()

    app.state.advanced = AdvancedSearchContext(
        embed_db_path=app.state.advanced.embed_db_path,
        lance_db=app.state.advanced.lance_db,
        paper_config=_PaperCfgWithRerank(),
        embedding_route_pool=app.state.advanced.embedding_route_pool,
        rerank_route_pool=object(),
        search_access_token=app.state.advanced.search_access_token,
        search_config=app.state.advanced.search_config,
    )

    async def fake_rerank(**kwargs):
        class Outcome:
            success = True
            reason = None
            chunks = kwargs["chunks"]
            scores = [0.91 for _ in kwargs["chunks"]]

        return Outcome()

    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision&rerank=always",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["reranker"]["applied"] is True


def test_search_mmr_lambda_boundaries(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)

    response_zero = client.get(
        "/api/v1/search/advanced?q=vision&mmr_lambda=0.0",
        headers={"Authorization": "Bearer secret"},
    )
    assert response_zero.status_code == 200
    assert response_zero.json()["metadata"]["mmr"]["lambda"] == 0.0

    response_one = client.get(
        "/api/v1/search/advanced?q=vision&mmr_lambda=1.0",
        headers={"Authorization": "Bearer secret"},
    )
    assert response_one.status_code == 200
    assert response_one.json()["metadata"]["mmr"]["lambda"] == 1.0
    assert response_one.json()["metadata"]["mmr"]["applied"] is False


def test_search_invalid_mmr_lambda_value(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision&mmr_lambda=1.5",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_trace_id_echoed(tmp_path, monkeypatch) -> None:
    app, _db = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret", "X-Request-Id": "my-trace"},
    )
    assert response.headers.get("X-Request-Id") == "my-trace"
    assert response.json()["trace_id"] == "my-trace"


def test_search_total_failure_503(tmp_path, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense, retrieve_sparse

    app, _db = _build_app(tmp_path, monkeypatch)

    async def raise_embed(**kwargs):
        raise RuntimeError("embedding down")

    def raise_sparse(**kwargs):
        raise RuntimeError("fts busted")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)
    monkeypatch.setattr(retrieve_sparse, "sparse_retrieve", raise_sparse)

    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TOTAL_FAILURE"


def test_search_vector_store_unavailable_503(tmp_path, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    app, _db = _build_app(tmp_path, monkeypatch)

    async def raise_embed(**kwargs):
        raise RuntimeError("embed down")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)

    class _BadLance:
        def open_table(self, name):
            raise RuntimeError("lance file corrupted")

    app.state.advanced = AdvancedSearchContext(
        embed_db_path=app.state.advanced.embed_db_path,
        lance_db=_BadLance(),
        paper_config=app.state.advanced.paper_config,
        embedding_route_pool=app.state.advanced.embedding_route_pool,
        rerank_route_pool=app.state.advanced.rerank_route_pool,
        search_access_token=app.state.advanced.search_access_token,
        search_config=app.state.advanced.search_config,
    )

    client = TestClient(app)
    response = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VECTOR_STORE_UNAVAILABLE"
