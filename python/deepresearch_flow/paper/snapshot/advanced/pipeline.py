"""Pipeline orchestrator for advanced snapshot search."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from deepresearch_flow.paper.reranker import RoutedReranker
from deepresearch_flow.paper.snapshot.advanced import (
    chunk_select,
    dedup as dedup_mod,
    filters as filters_mod,
    fusion,
    mmr as mmr_mod,
    normalize as normalize_mod,
    response as response_mod,
    retrieve_dense,
    retrieve_sparse,
    rerank_adapter,
)
from deepresearch_flow.paper.snapshot.advanced.errors import (
    InvalidQueryError,
    TotalFailureError,
    VectorStoreUnavailableError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestSpec:
    query_raw: str
    top_n: int
    mmr_lambda: float
    rerank_mode: str
    filter_params: dict[str, list[str]]
    trace_id: str


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _exception_message_and_details(
    exc: Exception,
    *,
    default_message: str,
) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        body = exc.response.text if exc.response is not None else str(exc)
        message = (
            f"{default_message} Upstream HTTP status: {status}."
            if status is not None
            else default_message
        )
        return message, {
            "status_code": status,
            "provider_error": body,
        }

    detail = str(exc).strip() or exc.__class__.__name__
    return default_message, {"error": detail}


async def run_advanced_search(
    *,
    request_spec: RequestSpec,
    ctx: Any,
    conn: Any,
    client: Any,
) -> dict[str, Any]:
    total_started = _now_ms()
    search_cfg = ctx.search_config
    latency_ms: dict[str, int] = {
        "embed": 0,
        "dense": 0,
        "sparse": 0,
        "fusion": 0,
        "chunk_select": 0,
        "dedup": 0,
        "rerank": 0,
        "mmr": 0,
        "total": 0,
    }

    if not request_spec.query_raw.strip():
        raise InvalidQueryError("q is empty")
    if len(request_spec.query_raw) > search_cfg.advanced_max_query_length:
        raise InvalidQueryError("q exceeds max length")

    normalize_started = _now_ms()
    normalized = normalize_mod.normalize(request_spec.query_raw)
    if not normalized.normalized:
        raise InvalidQueryError("q empty after normalization")
    _ = _now_ms() - normalize_started

    filter_started = _now_ms()
    parsed_filters = filters_mod.parse_filters(request_spec.filter_params)
    _ = _now_ms() - filter_started

    dense_task = retrieve_dense.dense_retrieve_with_metrics(
        query_text=normalized.normalized,
        lance_db=ctx.lance_db,
        embedding_route_pool=ctx.embedding_route_pool,
        client=client,
        dimensions=ctx.paper_config.embedding.dimensions,
        top_k=search_cfg.advanced_dense_top_k,
        lance_where=parsed_filters.lance_where,
    )

    async def _run_sparse():
        sparse_started = _now_ms()
        try:
            return (
                retrieve_sparse.sparse_retrieve(
                    conn=conn,
                    fts_expr=normalized.fts_expr,
                    filters=parsed_filters,
                    top_k=search_cfg.advanced_sparse_top_k,
                    lang=normalized.lang,
                ),
                _now_ms() - sparse_started,
            )
        except Exception as exc:  # pragma: no cover - exercised via pipeline tests
            return exc, _now_ms() - sparse_started

    dense_result, sparse_result = await asyncio.gather(
        dense_task,
        _run_sparse(),
        return_exceptions=True,
    )

    dense_failed = isinstance(dense_result, Exception)
    dense_hits = [] if dense_failed else list(dense_result.hits)
    if not dense_failed:
        latency_ms["embed"] = dense_result.embed_ms
        latency_ms["dense"] = dense_result.dense_ms

    sparse_elapsed = 0
    sparse_payload = sparse_result
    if not isinstance(sparse_result, Exception):
        sparse_payload, sparse_elapsed = sparse_result
    latency_ms["sparse"] = sparse_elapsed
    sparse_failed = isinstance(sparse_payload, Exception)
    sparse_hits = [] if sparse_failed else list(sparse_payload)

    degraded = False
    degradation_reason: str | None = None
    degradation_message: str | None = None
    degradation_details: dict[str, Any] | None = None
    if dense_failed and sparse_failed:
        raise TotalFailureError("both retrieval branches failed")
    if dense_failed and not sparse_hits:
        raise TotalFailureError("embedding failed and sparse returned empty")
    if dense_failed:
        degraded = True
        degradation_reason = "embedding_failed"
        degradation_message, degradation_details = _exception_message_and_details(
            dense_result,
            default_message="Dense retrieval failed; results fall back to sparse retrieval.",
        )
    elif sparse_failed:
        degraded = True
        degradation_reason = "fts_unavailable"
        degradation_message, degradation_details = _exception_message_and_details(
            sparse_payload,
            default_message="Sparse retrieval failed; results fall back to dense retrieval.",
        )

    fusion_started = _now_ms()
    fused = fusion.fuse_paper_level(
        dense_chunks=dense_hits,
        sparse_papers=sparse_hits,
        k=search_cfg.advanced_rrf_k,
        w_dense=1.0,
        w_sparse=1.0,
    )
    latency_ms["fusion"] = _now_ms() - fusion_started
    if not fused:
        raise TotalFailureError("no fused papers")

    chunk_select_started = _now_ms()
    selected = chunk_select.select_chunks(
        fused_papers=fused,
        dense_chunks=dense_hits,
        lance_db=ctx.lance_db,
        max_papers=search_cfg.advanced_post_fusion_top_k,
    )
    latency_ms["chunk_select"] = _now_ms() - chunk_select_started
    if not selected and degraded:
        raise VectorStoreUnavailableError("no fallback chunks available")

    dedup_started = _now_ms()
    deduped = dedup_mod.dedup(
        selected,
        cosine_threshold=search_cfg.advanced_dedup_cosine_threshold,
    )
    latency_ms["dedup"] = _now_ms() - dedup_started
    deduped_count = len(deduped)

    rerank_applied = False
    rerank_scores: list[float] = []
    reranker_model: str | None = None
    rerank_candidates = deduped[: search_cfg.advanced_rerank_top_n * 2]
    if (
        request_spec.rerank_mode != "never"
        and ctx.rerank_route_pool is not None
        and ctx.paper_config.rerank is not None
        and ctx.paper_config.rerank.enabled
        and rerank_candidates
    ):
        rerank_started = _now_ms()
        rerank_provider, rerank_model_config = ctx.paper_config.rerank.resolve_active()
        reranker_model = rerank_model_config.model_name
        reranker = RoutedReranker(route_pool=ctx.rerank_route_pool)
        rerank_outcome = await rerank_adapter.rerank_with_timeout(
            reranker=reranker,
            query=normalized.normalized,
            chunks=rerank_candidates,
            top_n=search_cfg.advanced_rerank_top_n,
            timeout_ms=search_cfg.advanced_rerank_timeout_ms,
            client=client,
        )
        latency_ms["rerank"] = _now_ms() - rerank_started
        if rerank_outcome.success:
            rerank_applied = True
            deduped = rerank_outcome.chunks
            rerank_scores = rerank_outcome.scores
        else:
            degraded = True
            degradation_reason = rerank_outcome.reason or "reranker_failed"
            degradation_message = (
                getattr(rerank_outcome, "message", None)
                or "Reranking failed; results fall back to fused ranking."
            )
            degradation_details = getattr(rerank_outcome, "details", None)
            logger.warning(
                "Advanced search degraded: reason=%s trace_id=%s message=%s",
                degradation_reason,
                request_spec.trace_id,
                degradation_message,
            )

    mmr_started = _now_ms()
    final_chunks = mmr_mod.mmr_select(
        deduped,
        relevance_scores=rerank_scores if rerank_applied else None,
        lambda_=request_spec.mmr_lambda,
        top_n=request_spec.top_n,
    )
    latency_ms["mmr"] = _now_ms() - mmr_started

    final_rerank_scores: list[float] = []
    if rerank_applied and rerank_scores:
        rerank_by_chunk_id = {chunk.chunk_id: score for chunk, score in zip(deduped, rerank_scores)}
        final_rerank_scores = [
            rerank_by_chunk_id.get(chunk.chunk_id, chunk.fused_score) for chunk in final_chunks
        ]

    counts = {
        "dense_papers": len({hit.paper_id for hit in dense_hits}),
        "sparse_papers": len(sparse_hits),
        "fused_papers": len(fused),
        "selected_chunks": len(selected),
        "deduped": deduped_count,
        "reranked": len(deduped) if rerank_applied else 0,
        "returned": len(final_chunks),
    }
    latency_ms["total"] = _now_ms() - total_started

    embed_provider, embed_model = ctx.paper_config.embedding.resolve_active()
    return response_mod.assemble_response(
        chunks=final_chunks,
        rerank_scores=final_rerank_scores,
        conn=conn,
        rerank_applied=rerank_applied,
        mmr_applied=request_spec.mmr_lambda < 1.0,
        mmr_lambda=request_spec.mmr_lambda,
        fusion_label="rrf",
        embedding_model=embed_model.model_name,
        embedding_dimensions=ctx.paper_config.embedding.dimensions,
        reranker_model=reranker_model,
        query_raw=request_spec.query_raw,
        query_normalized=normalized.normalized,
        applied_filters=parsed_filters.applied,
        counts=counts,
        latency_ms=latency_ms,
        trace_id=request_spec.trace_id,
        degraded=degraded,
        degradation_reason=degradation_reason,
        degradation_message=degradation_message,
        degradation_details=degradation_details,
    )
