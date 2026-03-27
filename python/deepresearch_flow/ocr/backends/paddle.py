"""PaddleOCR synchronous cloud API backend."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx

from deepresearch_flow.ocr.base import OcrPage, OcrResult
from deepresearch_flow.ocr.config import BackendConfig

logger = logging.getLogger(__name__)

_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_SUPPORTED_EXTENSIONS = _PDF_EXTENSIONS | _IMAGE_EXTENSIONS

# Matches markdown image references: ![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Matches HTML img tags: <img src="url" ... />
_HTML_IMG_RE = re.compile(r'<img\s[^>]*src="([^"]+)"[^>]*/?\s*>', re.IGNORECASE)


def _file_type_for(path: Path) -> int:
    """Return PaddleOCR fileType: 0 for PDF, 1 for images."""
    ext = path.suffix.lower()
    if ext in _PDF_EXTENSIONS:
        return 0
    if ext in _IMAGE_EXTENSIONS:
        return 1
    raise ValueError(f"Unsupported file extension: {ext}")


def _image_ext_from_url(url: str) -> str:
    """Extract file extension from a URL, defaulting to .png."""
    # Strip query params.
    clean = url.split("?")[0]
    ext = Path(clean).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
        return ext
    return ".png"


class PaddleOcrBackend:
    """PaddleOCR layout-parsing synchronous API backend."""

    def __init__(self, config: BackendConfig) -> None:
        self._api_url = config.api_url
        self._token = config.token
        self._options = dict(config.options)

    def ocr(self, file_path: Path) -> OcrResult:
        """Run OCR on a file and return structured results."""
        file_type = _file_type_for(file_path)
        file_data = base64.b64encode(file_path.read_bytes()).decode("ascii")

        payload: dict[str, object] = {
            "file": file_data,
            "fileType": file_type,
        }
        if self._options:
            payload["optionalPayload"] = self._options

        headers = {
            "Authorization": f"token {self._token}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=120.0) as client:
            self._client = client
            resp = client.post(self._api_url, json=payload, headers=headers)
            resp.raise_for_status()

            data = resp.json()
            layout_results = data["result"]["layoutParsingResults"]

            pages: list[OcrPage] = []
            for page_idx, layout in enumerate(layout_results):
                page = self._process_page(page_idx, layout)
                pages.append(page)

        return OcrResult(pages=pages)

    def _process_page(self, page_idx: int, layout: dict) -> OcrPage:
        """Process a single layoutParsingResult into an OcrPage."""
        md_section = layout["markdown"]
        raw_markdown: str = md_section["text"]
        raw_images: dict[str, str] = md_section.get("images", {})
        output_images: dict[str, str] = layout.get("outputImages", {})

        # Build mapping: original URL -> local_key for ALL image URLs.
        counter = 0
        url_to_local: dict[str, str] = {}
        images: dict[str, bytes] = {}
        missing: list[str] = []

        # 1) Process images from API mapping.
        #    Map both the remote URL and the local name/key variants so that
        #    references like <img src="imgs/foo.jpg"> also resolve.
        for name, url in raw_images.items():
            ext = _image_ext_from_url(url)
            local_key = f"images/page_{page_idx:04d}_{counter:02d}_md{ext}"
            counter += 1
            url_to_local[url] = local_key
            url_to_local[name] = local_key
            # Also map with common path prefixes the API may use.
            if "/" not in name:
                url_to_local[f"imgs/{name}"] = local_key
            self._download_image(url, local_key, images, missing)

        # 2) Process output images (layout visualizations etc.).
        for kind, url in output_images.items():
            ext = _image_ext_from_url(url)
            local_key = f"images/page_{page_idx:04d}_{counter:02d}_{kind}{ext}"
            counter += 1
            url_to_local[url] = local_key
            self._download_image(url, local_key, images, missing)

        # 3) Scan markdown for image refs not covered by API mappings.
        #    Covers both ![alt](url) and <img src="url"> patterns.
        for match in _IMAGE_RE.finditer(raw_markdown):
            ref = match.group(2)
            if ref not in url_to_local:
                ext = _image_ext_from_url(ref)
                local_key = f"images/page_{page_idx:04d}_{counter:02d}_md{ext}"
                counter += 1
                url_to_local[ref] = local_key
                if ref.startswith(("http://", "https://")):
                    self._download_image(ref, local_key, images, missing)

        for match in _HTML_IMG_RE.finditer(raw_markdown):
            ref = match.group(1)
            if ref not in url_to_local:
                ext = _image_ext_from_url(ref)
                local_key = f"images/page_{page_idx:04d}_{counter:02d}_md{ext}"
                counter += 1
                url_to_local[ref] = local_key
                if ref.startswith(("http://", "https://")):
                    self._download_image(ref, local_key, images, missing)

        # Rewrite markdown image refs: ![alt](url) → ![alt](local)
        def _replace_md_ref(match: re.Match[str]) -> str:
            alt = match.group(1)
            ref = match.group(2)
            local = url_to_local.get(ref, ref)
            return f"![{alt}]({local})"

        normalized_markdown = _IMAGE_RE.sub(_replace_md_ref, raw_markdown)

        # Rewrite HTML img tags: <img src="url" alt="X" ...> → ![X](local)
        def _replace_html_ref(match: re.Match[str]) -> str:
            original = match.group(0)
            ref = match.group(1)
            local = url_to_local.get(ref, ref)
            # Extract alt text if present.
            alt_match = re.search(r'alt="([^"]*)"', original, re.IGNORECASE)
            alt = alt_match.group(1) if alt_match else ""
            return f"![{alt}]({local})"

        # Also strip wrapping <div> around standalone HTML img tags.
        normalized_markdown = _HTML_IMG_RE.sub(_replace_html_ref, normalized_markdown)
        # Clean up empty <div ...>  </div> wrappers left behind.
        normalized_markdown = re.sub(
            r'<div[^>]*>\s*(!\[[^\]]*\]\([^)]+\))\s*</div>',
            r'\1',
            normalized_markdown,
        )

        return OcrPage(
            page_index=page_idx,
            markdown=normalized_markdown,
            images=images,
            missing_images=tuple(missing),
        )

    def _download_image(
        self,
        url: str,
        local_key: str,
        images: dict[str, bytes],
        missing: list[str],
    ) -> None:
        """Download an image URL. On success, add to images; on failure, add to missing."""
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            images[local_key] = resp.content
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Failed to download image %s: %s", url, exc)
            missing.append(local_key)
