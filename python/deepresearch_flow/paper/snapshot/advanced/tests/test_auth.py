from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.auth import verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError


def test_missing_header_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        verify_bearer(None, "secret")
    assert exc.value.reason == "missing"


def test_empty_header_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        verify_bearer("", "secret")
    assert exc.value.reason == "missing"


def test_malformed_prefix_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        verify_bearer("Basic xyz", "secret")
    assert exc.value.reason == "missing"


def test_wrong_token_raises_invalid() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        verify_bearer("Bearer wrong", "secret")
    assert exc.value.reason == "invalid"


def test_correct_token_returns_none() -> None:
    assert verify_bearer("Bearer secret", "secret") is None


def test_constant_time_compare_not_substring() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        verify_bearer("Bearer sec", "secret")
    assert exc.value.reason == "invalid"
