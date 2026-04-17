"""Starlette HTTP handlers for advanced search."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from deepresearch_flow.paper.snapshot.advanced.auth import verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import (
    AdvancedSearchError,
    InvalidQueryError,
    UnauthorizedError,
)
from deepresearch_flow.paper.snapshot.advanced.pipeline import RequestSpec, run_advanced_search
from deepresearch_flow.paper.snapshot.common import _open_ro_conn


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Request-Id") or uuid.uuid4().hex


def _error_response(exc: AdvancedSearchError, trace_id: str) -> JSONResponse:
    details: dict[str, Any] = {}
    if isinstance(exc, UnauthorizedError):
        details["reason"] = exc.reason
    return JSONResponse(
        {
            "success": False,
            "trace_id": trace_id,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": details,
            },
        },
        status_code=exc.http_status,
        headers={"X-Request-Id": trace_id},
    )


async def _api_verify_token(request: Request) -> JSONResponse:
    trace_id = _trace_id(request)
    ctx = getattr(request.app.state, "advanced", None)
    if ctx is None:
        return JSONResponse(
            {"valid": False, "reason": "advanced_disabled"},
            status_code=503,
            headers={"X-Request-Id": trace_id},
        )
    try:
        verify_bearer(request.headers.get("Authorization"), ctx.search_access_token)
    except UnauthorizedError as exc:
        return JSONResponse(
            {"valid": False, "reason": exc.reason},
            status_code=401,
            headers={"X-Request-Id": trace_id},
        )
    return JSONResponse({"valid": True}, headers={"X-Request-Id": trace_id})


def _collect_filter_params(request: Request) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    for key in request.query_params.keys():
        if key.startswith("filters."):
            params[key] = request.query_params.getlist(key)
    return params


async def _api_search_advanced(request: Request) -> JSONResponse:
    trace_id = _trace_id(request)
    ctx = getattr(request.app.state, "advanced", None)
    if ctx is None:
        return JSONResponse(
            {
                "success": False,
                "trace_id": trace_id,
                "error": {
                    "code": "ADVANCED_DISABLED",
                    "message": "advanced search not configured",
                    "details": {},
                },
            },
            status_code=503,
            headers={"X-Request-Id": trace_id},
        )

    try:
        verify_bearer(request.headers.get("Authorization"), ctx.search_access_token)
    except UnauthorizedError as exc:
        return _error_response(exc, trace_id)

    search_cfg = ctx.search_config
    try:
        top_n = int(request.query_params.get("top_n", "10"))
        if top_n < 1 or top_n > search_cfg.advanced_top_n_max:
            raise InvalidQueryError(
                f"top_n must be in [1, {search_cfg.advanced_top_n_max}]"
            )
        mmr_lambda = float(
            request.query_params.get("mmr_lambda", str(search_cfg.advanced_mmr_lambda_default))
        )
        if not (0.0 <= mmr_lambda <= 1.0):
            raise InvalidQueryError("mmr_lambda must be in [0,1]")
        rerank_mode = request.query_params.get("rerank", "auto")
        if rerank_mode not in {"auto", "always", "never"}:
            raise InvalidQueryError("rerank must be auto|always|never")
        request_spec = RequestSpec(
            query_raw=request.query_params.get("q", ""),
            top_n=top_n,
            mmr_lambda=mmr_lambda,
            rerank_mode=rerank_mode,
            filter_params=_collect_filter_params(request),
            trace_id=trace_id,
        )
    except AdvancedSearchError as exc:
        return _error_response(exc, trace_id)
    except ValueError as exc:
        return _error_response(InvalidQueryError(str(exc)), trace_id)

    conn = _open_ro_conn(request.app.state.cfg.snapshot_db)
    try:
        async with httpx.AsyncClient() as client:
            try:
                payload = await run_advanced_search(
                    request_spec=request_spec,
                    ctx=ctx,
                    conn=conn,
                    client=client,
                )
            except AdvancedSearchError as exc:
                return _error_response(exc, trace_id)
    finally:
        conn.close()

    return JSONResponse(payload, headers={"X-Request-Id": trace_id})
