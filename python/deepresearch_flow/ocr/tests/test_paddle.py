"""Black-box tests for the PaddleOCR jobs API backend."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from deepresearch_flow.ocr.backends.paddle import PaddleOcrBackend
from deepresearch_flow.ocr.config import BackendConfig

JOB_URL = "https://example.com/api/v2/ocr/jobs"
JOB_ID = "job-123"
JSONL_URL = "https://cdn.example.com/results.jsonl"
FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n fake png data"
FAKE_IMAGE_HASH = hashlib.sha256(FAKE_IMAGE_BYTES).hexdigest()[:12]
_HASH_IMG_RE = re.compile(r"images/[0-9a-f]{12}\.\w+")


@pytest.fixture()
def backend() -> PaddleOcrBackend:
    return PaddleOcrBackend(
        BackendConfig(
            type="paddle",
            api_url=JOB_URL,
            token="test-token",
            options={"useDocOrientationClassify": False},
            poll_interval_seconds=0.001,
            job_timeout_seconds=10,
        )
    )


def _layout(markdown: str, images: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "markdown": {"text": markdown, "images": images or {}},
        "outputImages": {},
    }


def _jsonl(*pages_per_line: list[dict[str, object]]) -> str:
    return "\n".join(
        json.dumps({"result": {"layoutParsingResults": pages}}) for pages in pages_per_line
    )


def _transport(
    states: list[dict[str, object]],
    jsonl_text: str,
    *,
    image_status: int = 200,
    captured_requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    pending_states = iter(states)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == JOB_URL:
            if captured_requests is not None:
                captured_requests.append(request)
            return httpx.Response(200, json={"data": {"jobId": JOB_ID}})
        if request.method == "GET" and str(request.url) == f"{JOB_URL}/{JOB_ID}":
            return httpx.Response(200, json={"data": next(pending_states)})
        if request.method == "GET" and str(request.url) == JSONL_URL:
            return httpx.Response(200, text=jsonl_text)
        return httpx.Response(image_status, content=FAKE_IMAGE_BYTES)

    return httpx.MockTransport(handler)


def _run_with_transport(
    backend: PaddleOcrBackend, transport: httpx.MockTransport, file_path: Path
):
    client = httpx.Client(transport=transport)
    with patch("deepresearch_flow.ocr.backends.paddle.httpx.Client", return_value=client):
        return backend.ocr(file_path)


class TestPaddleOcrBackend:
    def test_submits_job_polls_and_parses_multi_line_result(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        captured_requests: list[httpx.Request] = []
        first_page = _layout(
            "# Title\n\n![figure](https://cdn.example.com/figure.png)",
            {"figure.png": "https://cdn.example.com/figure.png"},
        )
        first_page["outputImages"] = {"layout": "https://cdn.example.com/layout.jpg"}
        second_page = _layout("Page two")
        transport = _transport(
            [
                {"state": "pending"},
                {
                    "state": "running",
                    "extractProgress": {"totalPages": 2, "extractedPages": 1},
                },
                {"state": "done", "resultUrl": {"jsonUrl": JSONL_URL}},
            ],
            _jsonl([first_page], [second_page]),
            captured_requests=captured_requests,
        )

        result = _run_with_transport(backend, transport, pdf_file)

        assert len(result.pages) == 2
        assert [page.page_index for page in result.pages] == [0, 1]
        assert "https://cdn.example.com" not in result.pages[0].markdown
        assert _HASH_IMG_RE.search(result.pages[0].markdown)
        assert len(result.pages[0].images) == 2
        assert captured_requests[0].headers["authorization"] == "bearer test-token"
        assert captured_requests[0].headers["content-type"].startswith("multipart/form-data")
        request_body = captured_requests[0].content.decode("utf-8", errors="replace")
        assert "PaddleOCR-VL-1.6" in request_body
        assert "useDocOrientationClassify" in request_body

    def test_job_failure_includes_server_reason(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        transport = _transport(
            [{"state": "failed", "errorMsg": "quota exhausted"}], ""
        )

        with pytest.raises(RuntimeError, match="job-123.*quota exhausted"):
            _run_with_transport(backend, transport, pdf_file)

    def test_job_timeout_includes_job_identifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        timeout_backend = PaddleOcrBackend(
            BackendConfig(
                type="paddle",
                api_url=JOB_URL,
                token="test-token",
                job_timeout_seconds=1,
            )
        )
        monotonic_values = iter([0.0, 0.0, 2.0])
        monkeypatch.setattr(
            "deepresearch_flow.ocr.backends.paddle.time.monotonic",
            lambda: next(monotonic_values),
        )
        monkeypatch.setattr("deepresearch_flow.ocr.backends.paddle.time.sleep", lambda _: None)
        transport = _transport([{"state": "pending"}, {"state": "pending"}], "")

        with pytest.raises(TimeoutError, match="job-123"):
            _run_with_transport(timeout_backend, transport, pdf_file)

    def test_invalid_jsonl_result_raises_clear_error(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        transport = _transport(
            [{"state": "done", "resultUrl": {"jsonUrl": JSONL_URL}}], "not json"
        )

        with pytest.raises(RuntimeError, match="JSONL result at line 1"):
            _run_with_transport(backend, transport, pdf_file)

    def test_image_download_failure_keeps_normalized_reference(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        page = _layout(
            "![missing](https://cdn.example.com/missing.png)",
            {"missing.png": "https://cdn.example.com/missing.png"},
        )
        transport = _transport(
            [{"state": "done", "resultUrl": {"jsonUrl": JSONL_URL}}],
            _jsonl([page]),
            image_status=404,
        )

        result = _run_with_transport(backend, transport, pdf_file)

        assert result.pages[0].images == {}
        assert len(result.pages[0].missing_images) == 1
        assert "https://cdn.example.com" not in result.pages[0].markdown
        assert _HASH_IMG_RE.search(result.pages[0].markdown)

    def test_html_image_and_unmapped_url_are_downloaded_and_normalized(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        page = _layout(
            '<div><img src="https://cdn.example.com/chart.jpg" alt="Chart" /></div>\n'
            "![extra](https://cdn.example.com/extra.png)"
        )
        transport = _transport(
            [{"state": "done", "resultUrl": {"jsonUrl": JSONL_URL}}], _jsonl([page])
        )

        result = _run_with_transport(backend, transport, pdf_file)

        assert "<img" not in result.pages[0].markdown
        assert "<div" not in result.pages[0].markdown
        assert "https://cdn.example.com" not in result.pages[0].markdown
        assert len(result.pages[0].images) == 2

    def test_matching_image_content_is_deduplicated(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        page = _layout(
            "![a](https://cdn.example.com/a.png) ![b](https://cdn.example.com/b.png)",
            {
                "a.png": "https://cdn.example.com/a.png",
                "b.png": "https://cdn.example.com/b.png",
            },
        )
        transport = _transport(
            [{"state": "done", "resultUrl": {"jsonUrl": JSONL_URL}}], _jsonl([page])
        )

        result = _run_with_transport(backend, transport, pdf_file)

        assert result.pages[0].images == {f"images/{FAKE_IMAGE_HASH}.png": FAKE_IMAGE_BYTES}
        assert result.pages[0].markdown.count(f"images/{FAKE_IMAGE_HASH}.png") == 2

    def test_submission_http_error_is_propagated(
        self, backend: PaddleOcrBackend, tmp_path: Path
    ) -> None:
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        with pytest.raises(httpx.HTTPStatusError):
            _run_with_transport(backend, httpx.MockTransport(handler), pdf_file)

    def test_unsupported_extension_is_rejected(self, backend: PaddleOcrBackend, tmp_path: Path) -> None:
        text_file = tmp_path / "paper.txt"
        text_file.write_text("not an OCR input")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            backend.ocr(text_file)
