from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.errors import (
    AdvancedSearchError,
    InvalidFilterError,
    InvalidQueryError,
    TotalFailureError,
    UnauthorizedError,
    VectorStoreUnavailableError,
)


def test_invalid_query_has_correct_status_and_code() -> None:
    exc = InvalidQueryError("query too long")
    assert exc.code == "INVALID_QUERY"
    assert exc.http_status == 400
    assert isinstance(exc, AdvancedSearchError)


def test_invalid_filter_has_correct_status_and_code() -> None:
    exc = InvalidFilterError("bad venue")
    assert exc.code == "INVALID_FILTER"
    assert exc.http_status == 400


def test_unauthorized_carries_reason() -> None:
    exc = UnauthorizedError("missing")
    assert exc.code == "UNAUTHORIZED"
    assert exc.http_status == 401
    assert exc.reason == "missing"


def test_unauthorized_reason_invalid() -> None:
    exc = UnauthorizedError("invalid")
    assert exc.reason == "invalid"


def test_vector_store_unavailable() -> None:
    exc = VectorStoreUnavailableError("lancedb open failed")
    assert exc.code == "VECTOR_STORE_UNAVAILABLE"
    assert exc.http_status == 503


def test_total_failure() -> None:
    exc = TotalFailureError("both channels dead")
    assert exc.code == "TOTAL_FAILURE"
    assert exc.http_status == 503


def test_base_error_defaults() -> None:
    with pytest.raises(AdvancedSearchError):
        raise AdvancedSearchError("x")
