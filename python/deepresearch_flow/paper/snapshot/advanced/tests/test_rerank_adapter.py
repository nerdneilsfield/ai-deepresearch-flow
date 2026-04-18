from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.rerank_adapter import RerankOutcome, rerank_with_timeout


def _selected(chunk_id: str, fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=chunk_id,
        chunk_id=f"{chunk_id}_c",
        chunk_text=f"t-{chunk_id}",
        field_name="",
        template_tag="",
        chunk_type="",
        chunk_index=0,
        lang="en",
        vector=(0.0,),
        fused_score=fused,
        paper_dense_score=None,
        paper_sparse_score=None,
        dense_score=None,
    )


class _FakeReranker:
    def __init__(self, indices, scores, *, sleep=0.0, raises=None):
        self.indices = indices
        self.scores = scores
        self.sleep = sleep
        self.raises = raises

    async def rerank(self, query, documents, *, top_n, client):
        if self.raises:
            raise self.raises
        if self.sleep:
            await asyncio.sleep(self.sleep)

        class Result:
            pass

        result = Result()
        result.indices = list(self.indices)
        result.scores = list(self.scores)
        return result


def test_happy_path_attaches_reranker_scores() -> None:
    chunks = [_selected("p1", 0.1), _selected("p2", 0.3), _selected("p3", 0.2)]
    output = asyncio.run(
        rerank_with_timeout(
            reranker=_FakeReranker(indices=[1, 2, 0], scores=[0.9, 0.5, 0.1]),
            query="q",
            chunks=chunks,
            top_n=2,
            timeout_ms=5000,
            client=object(),
        )
    )
    assert isinstance(output, RerankOutcome)
    assert output.success is True
    assert output.reason is None
    assert len(output.chunks) == 2
    assert output.chunks[0].chunk_id == "p2_c"
    assert output.scores[0] == pytest.approx(0.9)


def test_timeout_returns_degraded_and_logs(caplog) -> None:
    chunks = [_selected("p1", 0.1)]
    with caplog.at_level("WARNING"):
        output = asyncio.run(
            rerank_with_timeout(
                reranker=_FakeReranker(indices=[0], scores=[0.5], sleep=0.2),
                query="q",
                chunks=chunks,
                top_n=1,
                timeout_ms=50,
                client=object(),
            )
        )
    assert output.success is False
    assert output.reason == "reranker_failed"
    assert output.chunks == chunks
    assert output.scores == []
    assert "rerank timed out" in caplog.text


def test_exception_returns_degraded_and_logs(caplog) -> None:
    chunks = [_selected("p1", 0.1)]
    with caplog.at_level("ERROR"):
        output = asyncio.run(
            rerank_with_timeout(
                reranker=_FakeReranker(indices=[], scores=[], raises=RuntimeError("boom")),
                query="q",
                chunks=chunks,
                top_n=1,
                timeout_ms=5000,
                client=object(),
            )
        )
    assert output.success is False
    assert output.reason == "reranker_failed"
    assert "rerank failed" in caplog.text
    assert "boom" in caplog.text


def test_none_reranker_returns_success_without_changes() -> None:
    chunks = [_selected("p1", 0.3), _selected("p2", 0.1)]
    output = asyncio.run(
        rerank_with_timeout(
            reranker=None,
            query="q",
            chunks=chunks,
            top_n=10,
            timeout_ms=5000,
            client=object(),
        )
    )
    assert output.success is True
    assert output.reason is None
    assert output.chunks == chunks
    assert output.scores == []
