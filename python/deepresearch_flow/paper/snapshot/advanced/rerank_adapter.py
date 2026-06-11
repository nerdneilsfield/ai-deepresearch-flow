"""Rerank adapter: wraps RoutedReranker with timeout and fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

import httpx

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankOutcome:
    success: bool
    reason: str | None
    message: str | None
    details: dict[str, Any] | None
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
        return RerankOutcome(
            success=True, reason=None, message=None, details=None, chunks=chunks, scores=[]
        )

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
    except asyncio.TimeoutError:
        logger.warning("Advanced search rerank timed out after %d ms", timeout_ms)
        return RerankOutcome(
            success=False,
            reason="reranker_failed",
            message=f"Rerank request timed out after {timeout_ms} ms.",
            details={"timeout_ms": timeout_ms},
            chunks=chunks,
            scores=[],
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = exc.response.text.strip() if exc.response is not None else ""
        message = f"Rerank HTTP {status}." if status is not None else "Rerank HTTP failure."
        if body:
            message = f"{message} {body}"
        logger.exception("Advanced search rerank failed with HTTP status %s", status)
        return RerankOutcome(
            success=False,
            reason="reranker_failed",
            message=f"Rerank request failed with HTTP {status}."
            if status is not None
            else "Rerank HTTP failure.",
            details={
                "status_code": status,
                "provider_error": body or str(exc),
            },
            chunks=chunks,
            scores=[],
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        logger.exception("Advanced search rerank failed")
        return RerankOutcome(
            success=False,
            reason="reranker_failed",
            message="Rerank request failed.",
            details={"error": detail},
            chunks=chunks,
            scores=[],
        )

    ranked: list[SelectedChunk] = []
    scores: list[float] = []
    for idx, score in zip(result.indices, result.scores):
        if 0 <= idx < len(chunks):
            ranked.append(chunks[idx])
            scores.append(float(score))
        if len(ranked) >= top_n:
            break
    return RerankOutcome(
        success=True, reason=None, message=None, details=None, chunks=ranked, scores=scores
    )
