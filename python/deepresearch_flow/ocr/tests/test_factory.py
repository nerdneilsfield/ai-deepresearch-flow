"""Tests for OCR backend factory."""

from __future__ import annotations

import pytest

from deepresearch_flow.ocr.config import BackendConfig
from deepresearch_flow.ocr.factory import create_backend


class TestCreateBackend:
    def test_unknown_type_raises(self) -> None:
        cfg = BackendConfig(type="unknown", api_url="http://x", token="t")
        with pytest.raises(ValueError, match="Unknown OCR backend type: unknown"):
            create_backend(cfg)

    def test_paddle_returns_backend(self) -> None:
        cfg = BackendConfig(
            type="paddle",
            api_url="https://example.com/api",
            token="test-token",
            options={"useDocOrientationClassify": False},
        )
        backend = create_backend(cfg)
        # Verify it has the ocr method (Protocol compliance).
        assert callable(getattr(backend, "ocr", None))
