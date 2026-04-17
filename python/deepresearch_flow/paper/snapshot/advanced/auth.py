"""Bearer token verification for advanced search endpoints."""

from __future__ import annotations

import hmac

from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError

_BEARER_PREFIX = "Bearer "


def verify_bearer(header_value: str | None, expected: str) -> None:
    if not header_value or not header_value.startswith(_BEARER_PREFIX):
        raise UnauthorizedError("missing")
    candidate = header_value[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(candidate, expected):
        raise UnauthorizedError("invalid")
