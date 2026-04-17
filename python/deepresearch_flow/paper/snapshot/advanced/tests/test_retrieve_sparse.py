from __future__ import annotations

import sqlite3
from collections.abc import Sequence

import pytest

from deepresearch_flow.paper.snapshot.advanced.filters import parse_filters
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import PaperHit, sparse_retrieve


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY,
          title TEXT, year TEXT, venue TEXT, output_language TEXT
        );
        CREATE VIRTUAL TABLE paper_fts USING fts5(
          paper_id UNINDEXED, title, summary, source, translated, metadata,
          tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE paper_fts_trigram USING fts5(
          paper_id UNINDEXED, title, venue, tokenize='trigram'
        );
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER, PRIMARY KEY(paper_id, author_id));

        INSERT INTO paper VALUES
          ('p1','Vision Transformer','2021','ICLR','en'),
          ('p2','ResNet Deep Residual','2016','CVPR','en'),
          ('p3','视觉模型综述','2024','journal','zh');

        INSERT INTO paper_fts (paper_id, title, summary, source, translated, metadata)
        VALUES
          ('p1','Vision Transformer','patch transformer','','','arxiv'),
          ('p2','ResNet Deep Residual','residual learning','','','cvpr'),
          ('p3','视觉模型综述','综述 视觉 transformer','','','review');

        INSERT INTO paper_fts_trigram (paper_id, title, venue) VALUES
          ('p3','视觉模型综述','journal');

        INSERT INTO author VALUES (1,'alice'),(2,'bob');
        INSERT INTO paper_author VALUES ('p1',1),('p2',2);
        """
    )
    return connection


def test_returns_paper_hits_sorted(conn) -> None:
    hits = sparse_retrieve(conn=conn, fts_expr="transformer", filters=parse_filters({}), top_k=10, lang="en")
    assert all(isinstance(hit, PaperHit) for hit in hits)
    assert "p1" in [hit.paper_id for hit in hits]


def test_applies_year_filter(conn) -> None:
    hits = sparse_retrieve(
        conn=conn,
        fts_expr="transformer",
        filters=parse_filters({"filters.year": ["2020..2022"]}),
        top_k=10,
        lang="en",
    )
    ids = [hit.paper_id for hit in hits]
    assert "p1" in ids
    assert "p2" not in ids


def test_applies_author_filter(conn) -> None:
    hits = sparse_retrieve(
        conn=conn,
        fts_expr="residual",
        filters=parse_filters({"filters.authors": ["bob"]}),
        top_k=10,
        lang="en",
    )
    assert [hit.paper_id for hit in hits] == ["p2"]


def test_empty_fts_expr_returns_empty(conn) -> None:
    assert sparse_retrieve(conn=conn, fts_expr="", filters=parse_filters({}), top_k=10, lang="en") == []


def test_zh_lang_merges_trigram_hits(conn) -> None:
    hits = sparse_retrieve(conn=conn, fts_expr='"视觉"', filters=parse_filters({}), top_k=10, lang="zh")
    assert "p3" in [hit.paper_id for hit in hits]


def test_zh_lang_trigram_respects_filters(conn) -> None:
    hits = sparse_retrieve(
        conn=conn,
        fts_expr='"视觉"',
        filters=parse_filters({"filters.year": ["2021"]}),
        top_k=10,
        lang="zh",
    )
    assert hits == []


def test_zh_lang_prefers_better_trigram_rank() -> None:
    class FakeResult(Sequence[dict[str, object]]):
        def __init__(self, rows: list[dict[str, object]]):
            self._rows = rows

        def __getitem__(self, index):
            return self._rows[index]

        def __len__(self) -> int:
            return len(self._rows)

        def fetchall(self):
            return list(self._rows)

        def __iter__(self):
            return iter(self._rows)

    class FakeConn:
        def execute(self, sql, params):  # noqa: ANN001
            if "paper_fts_trigram" in sql:
                return FakeResult([
                    {"paper_id": "p1", "rank": -2.0},
                ])
            return FakeResult([
                {"paper_id": "p1", "rank": -1.0},
                {"paper_id": "p2", "rank": -0.5},
            ])

    hits = sparse_retrieve(
        conn=FakeConn(),  # type: ignore[arg-type]
        fts_expr='"视觉"',
        filters=parse_filters({}),
        top_k=10,
        lang="zh",
    )
    by_id = {hit.paper_id: hit.sparse_score for hit in hits}
    assert by_id["p1"] > by_id["p2"]
