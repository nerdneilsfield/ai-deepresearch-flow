"""Bearer token verification for advanced search endpoints."""

from __future__ import annotations

from deepresearch_flow.paper.snapshot.auth import BearerAuthError, verify_bearer as _verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError


def verify_bearer(header_value: str | None, expected: str) -> None:
    try:
        _verify_bearer(header_value, expected)
    except BearerAuthError as exc:
        raise UnauthorizedError(exc.reason) from exc
