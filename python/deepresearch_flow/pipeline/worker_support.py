"""Small worker policy helpers kept outside orchestration module."""

from __future__ import annotations

import re


def safe_error(exc: BaseException) -> str:
    """Return allowlisted diagnostics; provider details never cross boundary."""
    error_type = getattr(exc, "error_type", "")
    safe_messages = {
        "model_invalidated": "job model selection is no longer valid",
        "validation_failed": "validation retry limit exceeded",
        "lease_lost": "worker lease is no longer current",
        "cancelled": "job cancellation observed at step boundary",
        "input_missing": "input PDF artifact is missing",
        "input_tampered": "input PDF does not match uploaded source",
        "adapter_unavailable": "adapter unavailable",
    }
    if isinstance(error_type, str) and error_type in safe_messages:
        return safe_messages[error_type]
    return "step execution failed"


def retryable(exc: BaseException) -> bool:
    value = getattr(exc, "retryable", None)
    return bool(value) if isinstance(value, bool) else not isinstance(
        exc, (ValueError, TypeError)
    )


def error_type(exc: BaseException) -> str:
    value = getattr(exc, "error_type", None)
    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", value):
        return value
    class_name = type(exc).__name__.lower()
    return class_name if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", class_name) else "step_failed"


__all__ = ["error_type", "retryable", "safe_error"]
