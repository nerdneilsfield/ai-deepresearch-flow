"""PaddleOCR asynchronous jobs API backend."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import httpx

from deepresearch_flow.ocr.base import OcrPage, OcrResult
from deepresearch_flow.ocr.config import BackendConfig, PADDLE_OCR_VL_MODEL

logger = logging.getLogger(__name__)

_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_SUPPORTED_EXTENSIONS = _PDF_EXTENSIONS | _IMAGE_EXTENSIONS
_HTTP_TIMEOUT_SECONDS = 120.0

# Matches markdown image references: ![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Matches HTML img tags: <img src="url" ... />
_HTML_IMG_RE = re.compile(r'<img\s[^>]*src="([^"]+)"[^>]*/?\s*>', re.IGNORECASE)


def _validate_supported_file(path: Path) -> None:
    """Reject file extensions unsupported by the PaddleOCR jobs API."""
    ext = path.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: {ext}")


def _image_ext_from_url(url: str) -> str:
    """Extract file extension from a URL, defaulting to .png."""
    clean = url.split("?")[0]
    ext = Path(clean).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
        return ext
    return ".png"


class PaddleOcrBackend:
    """PaddleOCR-VL-1.6 backend using the asynchronous jobs API."""

    def __init__(self, config: BackendConfig) -> None:
        if config.model != PADDLE_OCR_VL_MODEL:
            raise ValueError(f"Unsupported PaddleOCR model: {config.model}")
        if config.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if config.job_timeout_seconds <= 0:
            raise ValueError("job_timeout_seconds must be positive")

        self._api_url = config.api_url.rstrip("/")
        self._token = config.token
        self._model = config.model
        self._options = dict(config.options)
        self._poll_interval_seconds = config.poll_interval_seconds
        self._job_timeout_seconds = config.job_timeout_seconds

    def ocr(self, file_path: Path) -> OcrResult:
        """Submit a local file, wait for completion, and return parsed OCR pages."""
        _validate_supported_file(file_path)

        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            job_id = self._submit_job(client, file_path)
            jsonl_url = self._wait_for_job(client, job_id)
            return self._download_result(client, jsonl_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self._token}"}

    def _submit_job(self, client: httpx.Client, file_path: Path) -> str:
        """Create an OCR job and return its server-assigned identifier."""
        data = {
            "model": self._model,
            "optionalPayload": json.dumps(self._options),
        }
        with file_path.open("rb") as file_handle:
            response = client.post(
                self._api_url,
                headers=self._headers(),
                data=data,
                files={"file": (file_path.name, file_handle)},
            )
        response.raise_for_status()

        response_data = self._response_data(response, "job submission")
        job_id = response_data.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError("PaddleOCR job submission response did not contain data.jobId")
        return job_id

    def _wait_for_job(self, client: httpx.Client, job_id: str) -> str:
        """Poll a submitted job until it returns a JSONL result URL or fails."""
        deadline = time.monotonic() + self._job_timeout_seconds
        job_url = f"{self._api_url}/{job_id}"

        while True:
            response = client.get(job_url, headers=self._headers())
            response.raise_for_status()
            response_data = self._response_data(response, f"job {job_id}")
            state = response_data.get("state")
            if not isinstance(state, str):
                raise RuntimeError(f"PaddleOCR job {job_id} did not return data.state")

            if state == "done":
                result_url = response_data.get("resultUrl")
                if not isinstance(result_url, dict):
                    raise RuntimeError(f"PaddleOCR job {job_id} did not return data.resultUrl")
                jsonl_url = result_url.get("jsonUrl")
                if not isinstance(jsonl_url, str) or not jsonl_url:
                    raise RuntimeError(f"PaddleOCR job {job_id} did not return data.resultUrl.jsonUrl")
                return jsonl_url

            if state == "failed":
                error_msg = response_data.get("errorMsg", "unknown error")
                raise RuntimeError(f"PaddleOCR job {job_id} failed: {error_msg}")

            if state not in {"pending", "running"}:
                raise RuntimeError(f"PaddleOCR job {job_id} returned unexpected state: {state!r}")

            self._log_job_progress(job_id, state, response_data)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"PaddleOCR job {job_id} did not finish within "
                    f"{self._job_timeout_seconds:g} seconds"
                )
            time.sleep(min(self._poll_interval_seconds, remaining))

    @staticmethod
    def _response_data(response: httpx.Response, context: str) -> dict[str, object]:
        """Extract the ``data`` object from a jobs API response."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON in PaddleOCR {context} response") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"Unexpected PaddleOCR {context} response structure")
        return payload["data"]

    @staticmethod
    def _log_job_progress(job_id: str, state: str, response_data: dict[str, object]) -> None:
        """Log the best available progress information from a pending job."""
        if state == "pending":
            logger.info("PaddleOCR job %s is pending", job_id)
            return

        progress = response_data.get("extractProgress")
        if not isinstance(progress, dict):
            logger.info("PaddleOCR job %s is running", job_id)
            return
        total_pages = progress.get("totalPages")
        extracted_pages = progress.get("extractedPages")
        if isinstance(total_pages, int | float) and isinstance(extracted_pages, int | float):
            logger.info(
                "PaddleOCR job %s is running: %s/%s pages",
                job_id,
                extracted_pages,
                total_pages,
            )
        else:
            logger.info("PaddleOCR job %s is running", job_id)

    def _download_result(self, client: httpx.Client, jsonl_url: str) -> OcrResult:
        """Download and convert a completed job's JSONL result into OCR pages."""
        response = client.get(jsonl_url)
        response.raise_for_status()

        pages: list[OcrPage] = []
        for line_number, line in enumerate(response.text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid PaddleOCR JSONL result at line {line_number}"
                ) from exc
            if not isinstance(entry, dict):
                raise RuntimeError(f"Unexpected PaddleOCR JSONL result at line {line_number}")
            result = entry.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"PaddleOCR JSONL result at line {line_number} lacks result")
            layout_results = result.get("layoutParsingResults")
            if not isinstance(layout_results, list):
                raise RuntimeError(
                    f"PaddleOCR JSONL result at line {line_number} lacks layoutParsingResults"
                )

            for layout in layout_results:
                if not isinstance(layout, dict):
                    raise RuntimeError(
                        f"PaddleOCR JSONL result at line {line_number} has an invalid page"
                    )
                pages.append(self._process_page(client, len(pages), layout))

        return OcrResult(pages=pages)

    def _process_page(
        self,
        client: httpx.Client,
        page_idx: int,
        layout: dict[str, object],
    ) -> OcrPage:
        """Process one layout-parsing result into an OCR output page."""
        md_section = layout.get("markdown")
        if not isinstance(md_section, dict):
            raise RuntimeError("PaddleOCR layout result lacks markdown")
        raw_markdown = md_section.get("text")
        if not isinstance(raw_markdown, str):
            raise RuntimeError("PaddleOCR layout markdown lacks text")
        raw_images = self._string_mapping(md_section.get("images"), "markdown.images")
        output_images = self._string_mapping(layout.get("outputImages"), "outputImages")

        url_to_local: dict[str, str] = {}
        images: dict[str, bytes] = {}
        missing: list[str] = []

        for name, url in raw_images.items():
            local_key = url_to_local.get(url)
            if local_key is None:
                local_key = self._download_image(client, url, _image_ext_from_url(url), images, missing)
                url_to_local[url] = local_key
            url_to_local[name] = local_key
            if "/" not in name:
                url_to_local[f"imgs/{name}"] = local_key

        for url in output_images.values():
            local_key = url_to_local.get(url)
            if local_key is None:
                local_key = self._download_image(client, url, _image_ext_from_url(url), images, missing)
                url_to_local[url] = local_key

        for match in _IMAGE_RE.finditer(raw_markdown):
            ref = match.group(2)
            if ref not in url_to_local and ref.startswith(("http://", "https://")):
                url_to_local[ref] = self._download_image(
                    client, ref, _image_ext_from_url(ref), images, missing
                )

        for match in _HTML_IMG_RE.finditer(raw_markdown):
            ref = match.group(1)
            if ref not in url_to_local and ref.startswith(("http://", "https://")):
                url_to_local[ref] = self._download_image(
                    client, ref, _image_ext_from_url(ref), images, missing
                )

        def replace_markdown_image(match: re.Match[str]) -> str:
            alt = match.group(1)
            ref = match.group(2)
            return f"![{alt}]({url_to_local.get(ref, ref)})"

        normalized_markdown = _IMAGE_RE.sub(replace_markdown_image, raw_markdown)

        def replace_html_image(match: re.Match[str]) -> str:
            original = match.group(0)
            ref = match.group(1)
            alt_match = re.search(r'alt="([^"]*)"', original, re.IGNORECASE)
            alt = alt_match.group(1) if alt_match else ""
            return f"![{alt}]({url_to_local.get(ref, ref)})"

        normalized_markdown = _HTML_IMG_RE.sub(replace_html_image, normalized_markdown)
        normalized_markdown = re.sub(
            r"<div[^>]*>\s*(!\[[^\]]*\]\([^)]+\))\s*</div>",
            r"\1",
            normalized_markdown,
        )

        return OcrPage(
            page_index=page_idx,
            markdown=normalized_markdown,
            images=images,
            missing_images=tuple(missing),
        )

    @staticmethod
    def _string_mapping(value: object, field_name: str) -> dict[str, str]:
        """Validate an optional object whose keys and values are strings."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise RuntimeError(f"PaddleOCR {field_name} must be a string mapping")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise RuntimeError(f"PaddleOCR {field_name} must be a string mapping")
            result[key] = item
        return result

    @staticmethod
    def _download_image(
        client: httpx.Client,
        url: str,
        ext: str,
        images: dict[str, bytes],
        missing: list[str],
    ) -> str:
        """Download an image and return its content-hash local path."""
        try:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
            digest = hashlib.sha256(content).hexdigest()[:12]
            local_key = f"images/{digest}{ext}"
            images[local_key] = content
            return local_key
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Failed to download image %s: %s", url, exc)
            digest = hashlib.sha256(url.encode()).hexdigest()[:12]
            local_key = f"images/{digest}{ext}"
            missing.append(local_key)
            return local_key
