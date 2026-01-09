"""Provider base helpers."""

from __future__ import annotations


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retryable: bool = False,
        structured_error: bool = False,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.structured_error = structured_error
        self.error_type = error_type or "provider_error"
