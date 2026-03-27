"""Tests for OCR base types."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.ocr.base import OcrBackend, OcrPage, OcrResult


class TestOcrPage:
    def test_frozen(self) -> None:
        page = OcrPage(page_index=0, markdown="hello", images={})
        with pytest.raises(AttributeError):
            page.markdown = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        page = OcrPage(page_index=0, markdown="hello", images={})
        assert page.missing_images == ()

    def test_with_images_and_missing(self) -> None:
        page = OcrPage(
            page_index=1,
            markdown="![fig](images/page_0001_00_figure.png)",
            images={"images/page_0001_00_figure.png": b"\x89PNG"},
            missing_images=("images/page_0001_01_table.png",),
        )
        assert len(page.images) == 1
        assert len(page.missing_images) == 1


class TestOcrResult:
    def test_empty_pages(self) -> None:
        result = OcrResult(pages=[])
        assert result.pages == []

    def test_multiple_pages(self) -> None:
        pages = [
            OcrPage(page_index=0, markdown="page0", images={}),
            OcrPage(page_index=1, markdown="page1", images={}),
        ]
        result = OcrResult(pages=pages)
        assert len(result.pages) == 2


class TestOcrBackendProtocol:
    def test_protocol_compliance(self) -> None:
        """A class with an ocr(Path) -> OcrResult method satisfies the protocol."""

        class FakeBackend:
            def ocr(self, file_path: Path) -> OcrResult:
                return OcrResult(pages=[])

        backend: OcrBackend = FakeBackend()
        result = backend.ocr(Path("test.pdf"))
        assert result.pages == []
