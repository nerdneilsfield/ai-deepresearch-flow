from __future__ import annotations

import sqlite3

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.response import assemble_response


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY, title TEXT, year TEXT, venue TEXT,
          source_hash TEXT, doi TEXT
        );
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER, PRIMARY KEY(paper_id, author_id));
        INSERT INTO paper VALUES ('p1','Vision Transformer','2021','ICLR','abc123','10.x');
        INSERT INTO author VALUES (1,'Dosovitskiy A.'),(2,'Kolesnikov A.');
        INSERT INTO paper_author VALUES ('p1',1),('p1',2);
        """
    )
    return connection


def _selected(
    paper_id: str,
    chunk_id: str,
    fused: float,
    *,
    dense: float | None = None,
    sparse: float | None = None,
    vector: tuple[float, ...] = (0.0,),
) -> SelectedChunk:
    return SelectedChunk(
        paper_id=paper_id,
        chunk_id=chunk_id,
        chunk_text="body",
        field_name="simple/content",
        template_tag="simple",
        chunk_type="content",
        chunk_index=0,
        lang="en",
        vector=vector,
        fused_score=fused,
        paper_dense_score=dense,
        paper_sparse_score=sparse,
        dense_score=dense,
    )


def test_success_payload_shape(conn) -> None:
    output = assemble_response(
        chunks=[_selected("p1", "p1_c0", 0.016, dense=0.84, sparse=12.37)],
        rerank_scores=[0.912],
        conn=conn,
        rerank_applied=True,
        mmr_applied=True,
        mmr_lambda=0.6,
        fusion_label="rrf",
        embedding_model="bge-m3",
        embedding_dimensions=1024,
        reranker_model="bge-reranker-v2-m3",
        query_raw="vision transformer",
        query_normalized="vision transformer",
        applied_filters={"year": {"min": 2020, "max": 2022}},
        counts={
            "dense_papers": 5,
            "sparse_papers": 3,
            "fused_papers": 6,
            "selected_chunks": 6,
            "deduped": 5,
            "reranked": 3,
            "returned": 1,
        },
        latency_ms={"total": 100, "embed": 10},
        trace_id="tid-1",
        degraded=False,
        degradation_reason=None,
    )
    assert output["success"] is True
    assert output["trace_id"] == "tid-1"
    assert output["degraded"] is False
    assert output["degradation"] is None
    assert output["query"]["raw"] == "vision transformer"
    assert output["query"]["applied_filters"]["year"]["min"] == 2020
    assert len(output["results"]) == 1
    result = output["results"][0]
    assert result["paper_id"] == "p1"
    assert result["chunk_id"] == "p1_c0"
    assert result["paper"]["title"] == "Vision Transformer"
    assert result["paper"]["authors"] == ["Dosovitskiy A.", "Kolesnikov A."]
    assert result["paper"]["year"] == "2021"
    assert result["paper"]["source_hash"] == "abc123"
    assert result["scores"]["fused"] == pytest.approx(0.016)
    assert result["scores"]["reranker"] == pytest.approx(0.912)
    assert result["scores"]["final"] == pytest.approx(0.912)
    assert result["chunk"]["field_name"] == "simple/content"
    metadata = output["metadata"]
    assert metadata["fusion"] == "rrf"
    assert metadata["reranker"]["applied"] is True
    assert metadata["reranker"]["model"] == "bge-reranker-v2-m3"
    assert metadata["mmr"]["applied"] is True
    assert metadata["embedding"]["dimensions"] == 1024


def test_degraded_fields_set_when_degraded(conn) -> None:
    output = assemble_response(
        chunks=[_selected("p1", "p1_c0", 0.01)],
        rerank_scores=[],
        conn=conn,
        rerank_applied=False,
        mmr_applied=True,
        mmr_lambda=0.6,
        fusion_label="rrf",
        embedding_model="bge-m3",
        embedding_dimensions=1024,
        reranker_model=None,
        query_raw="q",
        query_normalized="q",
        applied_filters={},
        counts={},
        latency_ms={},
        trace_id="t",
        degraded=True,
        degradation_reason="reranker_failed",
    )
    assert output["degraded"] is True
    assert output["degradation"] == {"reason": "reranker_failed"}
    result = output["results"][0]
    assert "reranker" not in result["scores"]
    assert result["scores"]["final"] == pytest.approx(0.01)
