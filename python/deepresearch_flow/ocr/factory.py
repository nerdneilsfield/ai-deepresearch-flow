"""Factory for creating OCR backend instances from config."""

from __future__ import annotations

from deepresearch_flow.ocr.base import OcrBackend
from deepresearch_flow.ocr.config import BackendConfig


def create_backend(config: BackendConfig) -> OcrBackend:
    """Create an OCR backend instance based on the config type."""
    if config.type == "paddle":
        from deepresearch_flow.ocr.backends.paddle import PaddleOcrBackend

        return PaddleOcrBackend(config)

    raise ValueError(f"Unknown OCR backend type: {config.type}")
