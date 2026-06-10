"""Shared bearer-token auth helpers for snapshot HTTP surfaces."""

from __future__ import annotations

import hmac
from typing import Literal

from starlette.responses import JSONResponse

_BEARER_SCHEME = "bearer"


class BearerAuthError(Exception):
    """Bearer auth failure with a machine-readable reason."""

    def __init__(self, reason: Literal["missing", "invalid"]) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_bearer(header_value: str | None, expected: str) -> None:
    """Validate ``Authorization: Bearer <token>`` using constant-time compare."""
    if not header_value:
        raise BearerAuthError("missing")
    scheme, sep, candidate = header_value.partition(" ")
    if sep != " " or scheme.lower() != _BEARER_SCHEME:
        raise BearerAuthError("missing")
    if not hmac.compare_digest(candidate, expected):
        raise BearerAuthError("invalid")


def bearer_auth_app(app, access_token: str | None):
    """Wrap an ASGI app with optional bearer-token protection.

    Passing ``None`` or ``""`` leaves the app public.
    """
    if access_token in (None, ""):
        return app

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        try:
            verify_bearer(headers.get("authorization"), access_token)
        except BearerAuthError:
            response = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return

        await app(scope, receive, send)

    # Preserve common ASGI app metadata for route inspection and transport helpers.
    wrapped.routes = getattr(app, "routes", None)
    wrapped.router = getattr(app, "router", None)
    return wrapped
