"""Typed exceptions for the advanced search endpoint."""

from __future__ import annotations


class AdvancedSearchError(Exception):
    """Base class for advanced search errors."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500


class InvalidQueryError(AdvancedSearchError):
    code = "INVALID_QUERY"
    http_status = 400


class InvalidFilterError(AdvancedSearchError):
    code = "INVALID_FILTER"
    http_status = 400


class UnauthorizedError(AdvancedSearchError):
    code = "UNAUTHORIZED"
    http_status = 401

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VectorStoreUnavailableError(AdvancedSearchError):
    code = "VECTOR_STORE_UNAVAILABLE"
    http_status = 503


class TotalFailureError(AdvancedSearchError):
    code = "TOTAL_FAILURE"
    http_status = 503
