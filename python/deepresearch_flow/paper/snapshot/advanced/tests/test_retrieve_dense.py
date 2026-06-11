from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit, dense_retrieve


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows
        self.received_where = None
        self.received_vector = None
        self.received_top_k = None


def _fake_query_vector(db, vector, *, top_k, where=None):
    db.received_vector = list(vector)
    db.received_top_k = top_k
    db.received_where = where
    return list(db.rows)


def test_returns_chunk_hits(monkeypatch) -> None:
    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.1, 0.2, 0.3]]
            model = "m"
            usage_tokens = 0

        return Result()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense as mod

    monkeypatch.setattr(mod, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(mod, "query_vector", _fake_query_vector)

    db = _FakeLance(
        rows=[
            {
                "id": "p1_simple_content_0",
                "doc_id": "p1",
                "text": "...",
                "field_name": "simple/content",
                "template_tag": "simple",
                "chunk_type": "content",
                "chunk_index": 0,
                "lang": "en",
                "_distance": 0.2,
                "vector": [0.1, 0.2, 0.3],
            }
        ]
    )

    hits = asyncio.run(
        dense_retrieve(
            query_text="q",
            lance_db=db,
            embedding_route_pool=object(),
            client=object(),
            dimensions=3,
            top_k=10,
            lance_where="year = 2023",
        )
    )
    assert db.received_top_k == 10
    assert db.received_where == "year = 2023"
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, ChunkHit)
    assert hit.paper_id == "p1"
    assert hit.chunk_id == "p1_simple_content_0"
    assert hit.dense_score == pytest.approx(0.8)
    assert hit.field_name == "simple/content"
    assert hit.chunk_type == "content"


def test_empty_lance_where_is_not_sent(monkeypatch) -> None:
    async def fake_embed(**kwargs):
        class Result:
            vectors = [[0.0]]
            model = "m"
            usage_tokens = 0

        return Result()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense as mod

    monkeypatch.setattr(mod, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(mod, "query_vector", _fake_query_vector)
    db = _FakeLance(rows=[])
    asyncio.run(
        dense_retrieve(
            query_text="q",
            lance_db=db,
            embedding_route_pool=object(),
            client=object(),
            dimensions=1,
            top_k=5,
            lance_where="",
        )
    )
    assert db.received_where in {None, ""}
