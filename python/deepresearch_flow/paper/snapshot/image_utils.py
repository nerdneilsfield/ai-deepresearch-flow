"""Image processing utilities for snapshot operations."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any


def _hash_bytes(data: bytes) -> str:
    """Calculate SHA256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    """Calculate SHA256 hash of file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_DATA_URL_PATTERN = re.compile(r"^data:([^;,]+)(;base64)?,(.*)$", re.DOTALL)
_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_PATTERN = re.compile(r"\bsrc\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE | re.DOTALL)
_EXTENSION_OVERRIDES = {".jpe": ".jpg"}


def _extension_from_mime(mime: str) -> str | None:
    """Get file extension from MIME type."""
    ext = mimetypes.guess_extension(mime, strict=False)
    if ext in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[ext]
    return ext


def _parse_data_url(target: str) -> tuple[str, bytes] | None:
    """Parse base64 data URL and return (mime, bytes)."""
    match = _DATA_URL_PATTERN.match(target)
    if not match:
        return None
    mime = match.group(1) or ""
    if not mime.startswith("image/"):
        return None
    if match.group(2) != ";base64":
        return None
    payload = match.group(3) or ""
    try:
        return mime, base64.b64decode(payload)
    except Exception:
        return None


def _is_absolute_url(target: str) -> bool:
    """Check if URL is absolute."""
    lowered = target.lower()
    return lowered.startswith(("http://", "https://", "data:", "mailto:", "file:", "#")) or target.startswith("/")


def _split_link_target(raw_link: str) -> tuple[str, str, str, str]:
    """Split link into (target, suffix, prefix, postfix)."""
    link = raw_link.strip()
    if link.startswith("<"):
        end = link.find(">")
        if end != -1:
            return link[1:end], link[end + 1 :], "<", ">"
    parts = link.split()
    if not parts:
        return "", "", "", ""
    target = parts[0]
    suffix = link[len(target) :]
    return target, suffix, "", ""


def rewrite_markdown_images(
    markdown: str,
    *,
    source_path: Path,
    images_output_dir: Path,
    written: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Extract and rewrite images in markdown.

    - Extracts base64 images and saves to images_output_dir
    - Copies local image files to images_output_dir
    - Rewrites image paths to images/{hash}.{ext} format

    Args:
        markdown: Markdown content
        source_path: Path to the markdown file (for resolving relative image paths)
        images_output_dir: Directory to save extracted images
        written: Set of already written image filenames (to avoid duplicates)

    Returns:
        (rewritten_markdown, images_list)
    """
    images: list[dict[str, Any]] = []

    def store_bytes(mime: str, data: bytes) -> str | None:
        ext = _extension_from_mime(mime)
        if not ext:
            return None
        digest = _hash_bytes(data)
        filename = f"{digest}{ext}"
        rel = f"images/{filename}"
        if filename not in written:
            images_output_dir.mkdir(parents=True, exist_ok=True)
            dest = images_output_dir / filename
            if not dest.exists():
                dest.write_bytes(data)
            written.add(filename)
        images.append({"path": rel, "sha256": digest, "ext": ext.lstrip("."), "status": "available"})
        return rel

    def store_local(target: str) -> str | None:
        cleaned = target.strip()
        while cleaned.startswith("../"):
            cleaned = cleaned[3:]
        cleaned = cleaned.replace("\\", "/")
        cleaned = cleaned.lstrip("./")
        cleaned = cleaned.lstrip("/")

        local_path = (source_path.parent / cleaned).resolve()
        if local_path.exists() and local_path.is_file():
            ext = local_path.suffix.lower()
            digest = _hash_file(local_path)
            filename = f"{digest}{ext}" if ext else digest
            rel = f"images/{filename}"
            if filename not in written:
                images_output_dir.mkdir(parents=True, exist_ok=True)
                dest = images_output_dir / filename
                if not dest.exists():
                    dest.write_bytes(local_path.read_bytes())
                written.add(filename)
            images.append({"path": rel, "sha256": digest, "ext": ext.lstrip("."), "status": "available"})
            return rel

        images.append({"path": cleaned, "sha256": None, "ext": Path(cleaned).suffix.lstrip("."), "status": "missing"})
        return None

    def replace(match) -> str:
        alt_text = match.group(1)
        raw_link = match.group(2)
        target, suffix, prefix, postfix = _split_link_target(raw_link)
        parsed = _parse_data_url(target)
        if parsed is not None:
            mime, data = parsed
            replacement = store_bytes(mime, data)
            if not replacement:
                return match.group(0)
            new_link = f"{prefix}{replacement}{postfix}{suffix}"
            return f"![{alt_text}]({new_link})"
        if not target or _is_absolute_url(target):
            return match.group(0)

        rel = store_local(target)
        if not rel:
            return match.group(0)
        new_link = f"{prefix}{rel}{postfix}{suffix}"
        return f"![{alt_text}]({new_link})"

    rewritten = _MD_IMAGE_RE.sub(replace, markdown)

    def replace_img(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _SRC_ATTR_PATTERN.search(tag)
        if not src_match:
            return tag
        raw_value = src_match.group(1)
        quote = ""
        if raw_value and raw_value[0] in {"\"", "'"}:
            quote = raw_value[0]
            value = raw_value[1:-1]
        else:
            value = raw_value
        parsed = _parse_data_url(value)
        if parsed is not None:
            mime, data = parsed
            replacement = store_bytes(mime, data)
        elif not _is_absolute_url(value):
            replacement = store_local(value)
        else:
            replacement = None
        if not replacement:
            return tag
        new_src = f"{quote}{replacement}{quote}" if quote else replacement
        return tag[: src_match.start(1)] + new_src + tag[src_match.end(1) :]

    rewritten = _IMG_TAG_PATTERN.sub(replace_img, rewritten)
    return rewritten, images
