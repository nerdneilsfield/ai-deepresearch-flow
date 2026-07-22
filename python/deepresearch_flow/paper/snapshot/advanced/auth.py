"""Bearer token verification for advanced search endpoints."""

from __future__ import annotations

from deepresearch_flow.paper.snapshot.auth import BearerAuthError, verify_bearer as _verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError
from deepresearch_flow.paper.snapshot.advanced.web_oauth import session_user


def verify_bearer(header_value: str | None, expected: str) -> None:
    try:
        _verify_bearer(header_value, expected)
    except BearerAuthError as exc:
        raise UnauthorizedError(exc.reason) from exc


def authorize_request(request, ctx) -> None:
    """Accept any enabled advanced-search authentication mechanism."""
    bearer_reason = "missing"
    if ctx.auth_mode in {"static", "both"} and ctx.search_access_token:
        try:
            verify_bearer(request.headers.get("Authorization"), ctx.search_access_token)
            return
        except UnauthorizedError as exc:
            bearer_reason = exc.reason
    if ctx.auth_mode in {"github-oauth", "both"} and ctx.web_oauth is not None:
        if session_user(request, ctx.web_oauth) is not None:
            return
    raise UnauthorizedError(bearer_reason)
