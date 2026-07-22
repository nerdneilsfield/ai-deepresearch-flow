"""Starlette HTTP handlers for advanced search."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from deepresearch_flow.paper.snapshot.advanced.auth import authorize_request, verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import (
    AdvancedSearchError,
    InvalidQueryError,
    UnauthorizedError,
)
from deepresearch_flow.paper.snapshot.advanced.pipeline import run_advanced_search
from deepresearch_flow.paper.snapshot.advanced.request_spec import build_request_spec
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
    if ctx.auth_mode not in {"static", "both"} or not ctx.search_access_token:
        return JSONResponse(
            {"valid": False, "reason": "static_disabled"},
            status_code=404,
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
        authorize_request(request, ctx)
    except UnauthorizedError as exc:
        return _error_response(exc, trace_id)

    search_cfg = ctx.search_config
    try:
        request_spec = build_request_spec(
            query_raw=request.query_params.get("q", ""),
            top_n=request.query_params.get("top_n", "10"),
            mmr_lambda=request.query_params.get(
                "mmr_lambda",
                str(search_cfg.advanced_mmr_lambda_default),
            ),
            rerank_mode=request.query_params.get("rerank", "auto"),
            filter_params=_collect_filter_params(request),
            trace_id=trace_id,
            search_cfg=search_cfg,
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
