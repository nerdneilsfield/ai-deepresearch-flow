"""Paper-level RRF fusion over dense chunk hits and sparse paper hits."""

from __future__ import annotations

from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import PaperHit


@dataclass(frozen=True)
class FusedPaper:
    paper_id: str
    fused_score: float
    paper_dense_score: float | None
    paper_sparse_score: float | None


def fuse_paper_level(
    *,
    dense_chunks: list[ChunkHit],
    sparse_papers: list[PaperHit],
    k: int,
    w_dense: float,
    w_sparse: float,
) -> list[FusedPaper]:
    paper_dense: dict[str, float] = {}
    for hit in dense_chunks:
        current = paper_dense.get(hit.paper_id)
        if current is None or hit.dense_score > current:
            paper_dense[hit.paper_id] = hit.dense_score

    paper_sparse = {hit.paper_id: hit.sparse_score for hit in sparse_papers}

    dense_ranked = sorted(paper_dense.items(), key=lambda item: (-item[1], item[0]))
    sparse_ranked = sorted(paper_sparse.items(), key=lambda item: (-item[1], item[0]))

    scores: dict[str, float] = {}
    for rank, (paper_id, _score) in enumerate(dense_ranked, start=1):
        scores[paper_id] = scores.get(paper_id, 0.0) + w_dense / (k + rank)
    for rank, (paper_id, _score) in enumerate(sparse_ranked, start=1):
        scores[paper_id] = scores.get(paper_id, 0.0) + w_sparse / (k + rank)

    fused = [
        FusedPaper(
            paper_id=paper_id,
            fused_score=score,
            paper_dense_score=paper_dense.get(paper_id),
            paper_sparse_score=paper_sparse.get(paper_id),
        )
        for paper_id, score in scores.items()
    ]
    fused.sort(key=lambda item: (-item.fused_score, item.paper_id))
    return fused
