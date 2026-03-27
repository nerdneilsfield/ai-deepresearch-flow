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


def _run_with_transport(backend: PaddleOcrBackend, transport: httpx.MockTransport, file_path: Path):
    """Run backend.ocr() with a mocked httpx transport."""
    mock_client = httpx.Client(transport=transport)
    with patch("deepresearch_flow.ocr.backends.paddle.httpx.Client", return_value=mock_client):
        return backend.ocr(file_path)


# --- Tests --------------------------------------------------------------------


class TestPaddleOcrBackend:
    def test_single_page_pdf(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport(single_page_response)
        result = _run_with_transport(backend, transport, pdf_file)

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
        result = _run_with_transport(backend, transport, pdf_file)

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
        _run_with_transport(backend, transport, img_file)

        body = json.loads(captured_request[0].content)
        assert body["fileType"] == 1

    def test_api_error_raises(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        pdf_file = tmp_path / "bad.pdf"
        pdf_file.write_bytes(b"%PDF")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        with pytest.raises(httpx.HTTPStatusError):
            _run_with_transport(backend, transport, pdf_file)

    def test_image_download_failure_records_missing(
        self, backend: PaddleOcrBackend, single_page_response: dict, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        transport = _mock_transport_with_image_failure(single_page_response)
        result = _run_with_transport(backend, transport, pdf_file)

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

    def test_unmapped_url_in_markdown_gets_normalized(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        """Regression: URLs in markdown not listed in images mapping must still be normalized."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "Text\n\n![extra](http://cdn.example.com/unmapped.png)\n\nMore text",
                            "images": {},  # Empty — URL not in mapping.
                        },
                        "outputImages": {},
                    }
                ]
            }
        }

        transport = _mock_transport(response)
        result = _run_with_transport(backend, transport, pdf_file)

        page = result.pages[0]
        # The remote URL must be replaced with a local path.
        assert "http://cdn.example.com" not in page.markdown
        assert "images/page_0000_" in page.markdown
        # Image was downloaded.
        assert len(page.images) == 1
        assert page.missing_images == ()

    def test_unmapped_url_download_failure(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        """Unmapped URL that fails to download should be in missing_images."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "![x](http://cdn.example.com/gone.png)",
                            "images": {},
                        },
                        "outputImages": {},
                    }
                ]
            }
        }

        transport = _mock_transport_with_image_failure(response)
        result = _run_with_transport(backend, transport, pdf_file)

        page = result.pages[0]
        assert len(page.images) == 0
        assert len(page.missing_images) == 1
        # Markdown should still have the local ref, not the original URL.
        assert "http://cdn.example.com" not in page.markdown
        assert "images/page_0000_" in page.markdown

    def test_html_img_tags_normalized(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        """HTML <img src="..."> tags should also be normalized to local paths."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": (
                                'Text\n\n'
                                '<div style="text-align: center;">'
                                '<img src="http://cdn.example.com/chart.jpg" alt="Image" width="35%" />'
                                '</div>\n\n'
                                'More text'
                            ),
                            "images": {},
                        },
                        "outputImages": {},
                    }
                ]
            }
        }

        transport = _mock_transport(response)
        result = _run_with_transport(backend, transport, pdf_file)

        page = result.pages[0]
        # HTML img tag should be converted to markdown syntax.
        assert "http://cdn.example.com" not in page.markdown
        assert "<img" not in page.markdown
        assert "![Image](images/page_0000_" in page.markdown
        # Wrapping div should be stripped.
        assert "<div" not in page.markdown
        # Image was downloaded.
        assert len(page.images) == 1
        assert page.missing_images == ()

    def test_html_img_local_path_normalized(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        """Local relative paths in HTML <img> tags (e.g. imgs/...) should be rewritten."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": (
                                '<div><img src="imgs/img_in_chart_box_397_233_828_536.jpg" '
                                'alt="Image" width="35%" /></div>'
                            ),
                            "images": {},
                        },
                        "outputImages": {},
                    }
                ]
            }
        }

        transport = _mock_transport(response)
        result = _run_with_transport(backend, transport, pdf_file)

        page = result.pages[0]
        # The local relative path should be rewritten to markdown syntax.
        assert "imgs/" not in page.markdown
        assert "<img" not in page.markdown
        assert "![Image](images/page_0000_" in page.markdown

    def test_html_img_reuses_mapped_image(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        """HTML <img src="imgs/foo.jpg"> should reuse the same local key as markdown.images["foo.jpg"]."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": (
                                '![md_ref](http://cdn.example.com/fig.jpg)\n\n'
                                '<div><img src="imgs/fig.jpg" alt="Table" /></div>'
                            ),
                            "images": {
                                "fig.jpg": "http://cdn.example.com/fig.jpg",
                            },
                        },
                        "outputImages": {},
                    }
                ]
            }
        }

        transport = _mock_transport(response)
        result = _run_with_transport(backend, transport, pdf_file)

        page = result.pages[0]
        # Only 1 image should be downloaded (not 2 — the HTML img reuses the same).
        assert len(page.images) == 1
        # Both references should point to the same local key.
        assert page.markdown.count("images/page_0000_00_md.jpg") == 2
