from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.mmr import mmr_select


def _selected(chunk_id: str, vector: tuple[float, ...], fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=chunk_id,
        chunk_id=f"{chunk_id}_c",
        chunk_text="",
        field_name="",
        template_tag="",
        chunk_type="",
        chunk_index=0,
        lang="en",
        vector=vector,
        fused_score=fused,
        paper_dense_score=None,
        paper_sparse_score=None,
        dense_score=None,
    )


def test_lambda_one_is_pure_relevance() -> None:
    output = mmr_select(
        [
            _selected("a", (1.0, 0.0), 0.3),
            _selected("b", (0.9, 0.1), 0.5),
            _selected("c", (0.0, 1.0), 0.4),
        ],
        relevance_scores=None,
        lambda_=1.0,
        top_n=3,
    )
    assert [item.paper_id for item in output] == ["b", "c", "a"]


def test_lambda_zero_prefers_diversity() -> None:
    output = mmr_select(
        [
            _selected("a", (1.0, 0.0), 0.9),
            _selected("b", (0.99, 0.01), 0.8),
            _selected("c", (0.0, 1.0), 0.1),
        ],
        relevance_scores=None,
        lambda_=0.0,
        top_n=2,
    )
    assert output[0].paper_id == "a"
    assert output[1].paper_id == "c"


def test_uses_reranker_scores_when_provided() -> None:
    output = mmr_select(
        [
            _selected("a", (1.0, 0.0), 0.9),
            _selected("b", (0.0, 1.0), 0.1),
        ],
        relevance_scores=[0.1, 0.9],
        lambda_=1.0,
        top_n=2,
    )
    assert [item.paper_id for item in output] == ["b", "a"]


def test_stable_tie_break() -> None:
    output = mmr_select(
        [
            _selected("a", (0.1, 0.0), 0.5),
            _selected("b", (0.2, 0.0), 0.5),
        ],
        relevance_scores=None,
        lambda_=1.0,
        top_n=2,
    )
    assert [item.paper_id for item in output] == ["a", "b"]


def test_top_n_truncates() -> None:
    chunks = [_selected(f"p{i}", (float(i),), float(i)) for i in range(5)]
    assert len(mmr_select(chunks, relevance_scores=None, lambda_=0.5, top_n=2)) == 2


def test_empty_input_returns_empty() -> None:
    assert mmr_select([], relevance_scores=None, lambda_=0.5, top_n=10) == []
