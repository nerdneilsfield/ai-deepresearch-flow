"""Tests for PaddleOCR backend with mocked HTTP responses."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from deepresearch_flow.ocr.backends.paddle import PaddleOcrBackend
from deepresearch_flow.ocr.config import BackendConfig

# --- Fixtures ----------------------------------------------------------------

FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n fake png data"


@pytest.fixture()
def backend() -> PaddleOcrBackend:
    cfg = BackendConfig(
        type="paddle",
        api_url="https://example.com/layout-parsing",
        token="test-token",
        options={"useDocOrientationClassify": False},
    )
    return PaddleOcrBackend(cfg)


@pytest.fixture()
def single_page_response() -> dict:
    """Minimal PaddleOCR API response with one page."""
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "# Title\n\nSome text\n\n![fig](http://cdn.example.com/fig1.png)",
                        "images": {
                            "fig1.png": "http://cdn.example.com/fig1.png",
                        },
                    },
                    "outputImages": {
                        "layout": "http://cdn.example.com/layout_0.jpg",
                    },
                }
            ]
        }
    }


@pytest.fixture()
def multi_page_response() -> dict:
    """PaddleOCR API response with two pages."""
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "Page 0 text",
                        "images": {},
                    },
                    "outputImages": {},
                },
                {
                    "markdown": {
                        "text": "Page 1 text with ![img](http://cdn.example.com/t.png)",
                        "images": {"t.png": "http://cdn.example.com/t.png"},
                    },
                    "outputImages": {},
                },
            ]
        }
    }


# --- Helpers ------------------------------------------------------------------


def _mock_transport(ocr_response: dict, image_bytes: bytes = FAKE_IMAGE_BYTES) -> httpx.MockTransport:
    """Build a MockTransport that returns the OCR response for POST and image bytes for GET."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=ocr_response)
        # GET requests are image downloads.
        return httpx.Response(200, content=image_bytes)

    return httpx.MockTransport(handler)


def _mock_transport_with_image_failure(ocr_response: dict) -> httpx.MockTransport:
    """POST succeeds, but all GET (image download) requests return 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=ocr_response)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# --- Tests --------------------------------------------------------------------


class TestPaddleOcrBackend:
    def test_single_page_pdf(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport(single_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.page_index == 0
        # Markdown references should be rewritten to local paths.
        assert "images/page_0000_" in page.markdown
        assert "http://cdn.example.com" not in page.markdown
        # Images dict should have the downloaded bytes.
        assert len(page.images) == 2  # fig + layout output image
        for key, data in page.images.items():
            assert key.startswith("images/page_0000_")
            assert data == FAKE_IMAGE_BYTES
        assert page.missing_images == ()

    def test_multi_page(
        self, backend: PaddleOcrBackend, multi_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport(multi_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        assert len(result.pages) == 2
        assert result.pages[0].page_index == 0
        assert result.pages[1].page_index == 1
        # Page 1 has one markdown image.
        assert len(result.pages[1].images) == 1

    def test_image_file_type(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        """Image files should set fileType=1 in the API request."""
        img_file = tmp_path / "scan.png"
        img_file.write_bytes(b"\x89PNG fake")

        captured_request: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                captured_request.append(request)
                return httpx.Response(
                    200,
                    json={"result": {"layoutParsingResults": [{"markdown": {"text": "ok", "images": {}}, "outputImages": {}}]}},
                )
            return httpx.Response(200, content=b"img")

        transport = httpx.MockTransport(handler)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            backend.ocr(img_file)

        body = json.loads(captured_request[0].content)
        assert body["fileType"] == 1

    def test_api_error_raises(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        pdf_file = tmp_path / "bad.pdf"
        pdf_file.write_bytes(b"%PDF")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            with pytest.raises(httpx.HTTPStatusError):
                backend.ocr(pdf_file)

    def test_image_download_failure_records_missing(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport_with_image_failure(single_page_response)
        with patch.object(backend, "_client", httpx.Client(transport=transport)):
            result = backend.ocr(pdf_file)

        page = result.pages[0]
        # Images dict should be empty (all downloads failed).
        assert len(page.images) == 0
        # Missing images should be recorded.
        assert len(page.missing_images) == 2  # fig + layout output image
        # Markdown still has the local references (not the original URLs).
        assert "images/page_0000_" in page.markdown

    def test_unsupported_extension_raises(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("hello")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            backend.ocr(txt_file)
