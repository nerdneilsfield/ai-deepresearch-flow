"""Shared advanced-search request-spec construction."""

from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.errors import InvalidQueryError
from deepresearch_flow.paper.snapshot.advanced.pipeline import RequestSpec


def build_request_spec(
    *,
    query_raw: str,
    top_n: int | str,
    mmr_lambda: float | str,
    rerank_mode: str,
    filter_params: dict[str, list[str]],
    trace_id: str,
    search_cfg,
) -> RequestSpec:
    resolved_top_n = int(top_n)
    if resolved_top_n < 1 or resolved_top_n > search_cfg.advanced_top_n_max:
        raise InvalidQueryError(f"top_n must be in [1, {search_cfg.advanced_top_n_max}]")

    resolved_mmr_lambda = float(mmr_lambda)
    if not (0.0 <= resolved_mmr_lambda <= 1.0):
        raise InvalidQueryError("mmr_lambda must be in [0,1]")

    resolved_rerank = (rerank_mode or "auto").strip().lower()
    if resolved_rerank not in {"auto", "always", "never"}:
        raise InvalidQueryError("rerank must be auto|always|never")

    return RequestSpec(
        query_raw=query_raw,
        top_n=resolved_top_n,
        mmr_lambda=resolved_mmr_lambda,
        rerank_mode=resolved_rerank,
        filter_params=filter_params,
        trace_id=trace_id,
    )
