from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.dedup import dedup


def _selected(chunk_id: str, paper_id: str, text: str, vector: tuple[float, ...], fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=paper_id,
        chunk_id=chunk_id,
        chunk_text=text,
        field_name="",
        template_tag="simple",
        chunk_type="content",
        chunk_index=0,
        lang="en",
        vector=vector,
        fused_score=fused,
        paper_dense_score=None,
        paper_sparse_score=None,
        dense_score=None,
    )


def test_content_hash_collapses_keeps_higher_fused() -> None:
    output = dedup(
        [
            _selected("a", "p1", "same text", (1.0, 0.0), 0.1),
            _selected("b", "p2", "same text", (0.0, 1.0), 0.5),
        ],
        cosine_threshold=0.95,
    )
    assert len(output) == 1
    assert output[0].chunk_id == "b"


def test_cosine_collapses_near_duplicates() -> None:
    output = dedup(
        [
            _selected("a", "p1", "text1", (1.0, 0.0, 0.0), 0.2),
            _selected("b", "p2", "text2", (0.99, 0.1, 0.0), 0.6),
        ],
        cosine_threshold=0.95,
    )
    assert len(output) == 1
    assert output[0].chunk_id == "b"


def test_unrelated_chunks_preserved() -> None:
    output = dedup(
        [
            _selected("a", "p1", "t1", (1.0, 0.0, 0.0), 0.3),
            _selected("b", "p2", "t2", (0.0, 1.0, 0.0), 0.4),
        ],
        cosine_threshold=0.95,
    )
    assert len(output) == 2


def test_empty_input_returns_empty() -> None:
    assert dedup([], cosine_threshold=0.95) == []


def test_zero_vectors_not_treated_as_similar() -> None:
    output = dedup(
        [
            _selected("a", "p1", "t1", (0.0, 0.0), 0.3),
            _selected("b", "p2", "t2", (0.0, 0.0), 0.4),
        ],
        cosine_threshold=0.95,
    )
    assert len(output) == 2
