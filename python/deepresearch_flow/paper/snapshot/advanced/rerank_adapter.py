"""Rerank adapter: wraps RoutedReranker with timeout and fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


@dataclass(frozen=True)
class RerankOutcome:
    success: bool
    reason: str | None
    chunks: list[SelectedChunk]
    scores: list[float]


async def rerank_with_timeout(
    *,
    reranker: Any | None,
    query: str,
    chunks: list[SelectedChunk],
    top_n: int,
    timeout_ms: int,
    client: Any,
) -> RerankOutcome:
    if reranker is None or not chunks:
        return RerankOutcome(success=True, reason=None, chunks=chunks, scores=[])

    try:
        result = await asyncio.wait_for(
            reranker.rerank(
                query,
                [chunk.chunk_text for chunk in chunks],
                top_n=top_n,
                client=client,
            ),
            timeout=timeout_ms / 1000.0,
        )
    except Exception:
        return RerankOutcome(success=False, reason="reranker_failed", chunks=chunks, scores=[])

    ranked: list[SelectedChunk] = []
    scores: list[float] = []
    for idx, score in zip(result.indices, result.scores):
        if 0 <= idx < len(chunks):
            ranked.append(chunks[idx])
            scores.append(float(score))
        if len(ranked) >= top_n:
            break
    return RerankOutcome(success=True, reason=None, chunks=ranked, scores=scores)
