from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.fusion import FusedPaper, fuse_paper_level
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import PaperHit


def _chunk(paper_id: str, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=f"{paper_id}_c0",
        paper_id=paper_id,
        dense_score=score,
        chunk_text="",
        field_name="",
        template_tag="",
        chunk_type="",
        chunk_index=0,
        lang="",
        vector=(),
    )


def test_deterministic_on_fixed_input() -> None:
    dense = [_chunk("p1", 0.9), _chunk("p2", 0.8), _chunk("p3", 0.7)]
    sparse = [PaperHit("p2", 10.0), PaperHit("p3", 5.0), PaperHit("p4", 1.0)]
    output = fuse_paper_level(
        dense_chunks=dense,
        sparse_papers=sparse,
        k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    assert all(isinstance(item, FusedPaper) for item in output)
    assert output[0].paper_id == "p2"


def test_dense_only_channel() -> None:
    output = fuse_paper_level(
        dense_chunks=[_chunk("p1", 0.5)],
        sparse_papers=[],
        k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    assert len(output) == 1
    assert output[0].paper_id == "p1"
    assert output[0].paper_dense_score == 0.5
    assert output[0].paper_sparse_score is None


def test_sparse_only_channel() -> None:
    output = fuse_paper_level(
        dense_chunks=[],
        sparse_papers=[PaperHit("p9", 3.0)],
        k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    assert len(output) == 1
    assert output[0].paper_id == "p9"
    assert output[0].paper_dense_score is None
    assert output[0].paper_sparse_score == 3.0


def test_aggregates_multiple_chunks_per_paper() -> None:
    dense = [_chunk("p1", 0.3), _chunk("p1", 0.8), _chunk("p1", 0.5)]
    output = fuse_paper_level(
        dense_chunks=dense,
        sparse_papers=[],
        k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    assert output[0].paper_dense_score == 0.8


def test_tied_ranks_stable_order() -> None:
    dense = [_chunk("pB", 0.5), _chunk("pA", 0.5)]
    output = fuse_paper_level(
        dense_chunks=dense,
        sparse_papers=[],
        k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    assert [item.paper_id for item in output] == ["pA", "pB"]
