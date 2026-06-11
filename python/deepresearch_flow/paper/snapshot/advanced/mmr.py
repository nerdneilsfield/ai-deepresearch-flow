"""Maximal Marginal Relevance selection."""

from __future__ import annotations

import math

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def mmr_select(
    chunks: list[SelectedChunk],
    *,
    relevance_scores: list[float] | None,
    lambda_: float,
    top_n: int,
) -> list[SelectedChunk]:
    if not chunks or top_n <= 0:
        return []

    relevance = (
        relevance_scores
        if relevance_scores is not None and len(relevance_scores) == len(chunks)
        else [chunk.fused_score for chunk in chunks]
    )

    if lambda_ >= 1.0:
        order = sorted(range(len(chunks)), key=lambda idx: (-relevance[idx], idx))
        return [chunks[idx] for idx in order[:top_n]]

    remaining = list(range(len(chunks)))
    selected: list[int] = []
    while remaining and len(selected) < top_n:
        best_idx = None
        best_score = None
        for idx in remaining:
            if not selected:
                score = lambda_ * relevance[idx]
            else:
                max_similarity = max(
                    _cosine(chunks[idx].vector, chunks[chosen].vector) for chosen in selected
                )
                score = lambda_ * relevance[idx] - (1.0 - lambda_) * max_similarity
            if best_score is None or score > best_score:
                best_score = score
                best_idx = idx
        assert best_idx is not None
        selected.append(best_idx)
        remaining.remove(best_idx)
    return [chunks[idx] for idx in selected]
