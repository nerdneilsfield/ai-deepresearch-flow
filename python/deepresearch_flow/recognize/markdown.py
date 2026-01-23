"""Markdown image helpers for recognize commands."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse, unquote

import httpx


logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_SECONDS = 60.0

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
EXTENSION_OVERRIDES = {
    ".jpe": ".jpg",
}

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
DATA_URL_PATTERN = re.compile(r"^data:([^;,]+)(;base64)?,(.*)$", re.DOTALL)


class NameRegistry:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        if output_dir.exists():
            self.used = {path.name for path in output_dir.iterdir() if path.is_file()}
        else:
            self.used = set()
        self.lock = asyncio.Lock()

    def reserve(self, base: str, ext: str) -> str:
        base = sanitize_filename(base) or "file"
        ext = ext if ext.startswith(".") else f".{ext}"
        candidate = f"{base}{ext}"
        counter = 0
        while candidate in self.used or (self.output_dir / candidate).exists():
            counter += 1
            candidate = f"{base}_{counter}{ext}"
        self.used.add(candidate)
        return candidate

    async def reserve_async(self, base: str, ext: str) -> str:
        async with self.lock:
            return self.reserve(base, ext)


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", value.strip())
    return cleaned.strip("._-")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def split_link_target(raw_link: str) -> tuple[str, str, str, str]:
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


def is_data_url(target: str) -> bool:
    return target.startswith("data:")


def is_http_url(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https"}


def resolve_local_path(md_path: Path, target: str) -> Path:
    target_path = Path(unquote(target))
    if target_path.is_absolute():
        return target_path
    return (md_path.parent / target_path).resolve()


def extension_from_mime(mime: str) -> Optional[str]:
    ext = mimetypes.guess_extension(mime, strict=False)
    if ext in EXTENSION_OVERRIDES:
        return EXTENSION_OVERRIDES[ext]
    return ext


def mime_from_path(path: Path) -> Optional[str]:
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if path.suffix.lower() == ".png":
        return "image/png"
    if path.suffix.lower() == ".gif":
        return "image/gif"
    if path.suffix.lower() == ".webp":
        return "image/webp"
    if path.suffix.lower() == ".svg":
        return "image/svg+xml"
    return None


def parse_data_url(target: str) -> Optional[tuple[str, bytes]]:
    match = DATA_URL_PATTERN.match(target)
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
    except Exception as exc:  # pragma: no cover - defensive
        message = str(exc).strip() or "unknown error"
        logger.warning(
            "Failed to decode base64 image (mime=%s, chars=%d): %s",
            mime or "<unknown>",
            len(payload),
            message,
        )
        return None


def data_url_from_bytes(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def base_name_from_alt(alt_text: str) -> str:
    if not alt_text:
        return ""
    candidate = sanitize_filename(alt_text)
    if not candidate:
        return ""
    suffix = Path(candidate).suffix.lower()
    if suffix in ALLOWED_IMAGE_EXTS:
        return Path(candidate).stem
    return candidate


def hash_name_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


async def rewrite_markdown_images(
    content: str,
    replacer: Callable[[str, str], Awaitable[Optional[str]]],
) -> str:
    output: list[str] = []
    last_idx = 0
    for match in IMAGE_PATTERN.finditer(content):
        output.append(content[last_idx : match.start()])
        alt_text = match.group(1)
        raw_link = match.group(2)
        target, suffix, prefix, postfix = split_link_target(raw_link)
        new_target = await replacer(alt_text, target)
        if new_target is None:
            output.append(match.group(0))
        else:
            new_link = f"{prefix}{new_target}{postfix}{suffix}"
            output.append(f"![{alt_text}]({new_link})")
        last_idx = match.end()
    output.append(content[last_idx:])
    return "".join(output)


def count_markdown_images(content: str) -> dict[str, int]:
    counts = {"total": 0, "data": 0, "http": 0, "local": 0}
    for match in IMAGE_PATTERN.finditer(content):
        counts["total"] += 1
        raw_link = match.group(2)
        target, _, _, _ = split_link_target(raw_link)
        if not target:
            continue
        if is_data_url(target):
            counts["data"] += 1
        elif is_http_url(target):
            counts["http"] += 1
        else:
            counts["local"] += 1
    return counts


async def embed_markdown_images(
    content: str,
    md_path: Path,
    enable_http: bool,
    http_client: Optional[httpx.AsyncClient],
) -> str:
    async def replacer(alt_text: str, target: str) -> Optional[str]:
        if not target:
            return None
        if is_data_url(target):
            return None
        if is_http_url(target):
            if not enable_http or http_client is None:
                return None
            try:
                response = await http_client.get(target)
            except Exception as exc:
                message = str(exc).strip() or "unknown error"
                logger.warning("Failed to fetch %s (md=%s): %s", target, md_path, message)
                return None
            if response.status_code >= 400:
                logger.warning(
                    "Failed to fetch %s (md=%s): HTTP %d",
                    target,
                    md_path,
                    response.status_code,
                )
                return None
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if not content_type.startswith("image/"):
                guessed = mime_from_path(Path(urlparse(target).path))
                if not guessed or not guessed.startswith("image/"):
                    logger.warning(
                        "Skipping non-image URL %s (md=%s, Content-Type=%s)",
                        target,
                        md_path,
                        content_type,
                    )
                    return None
                content_type = guessed
            return data_url_from_bytes(content_type, response.content)

        local_path = resolve_local_path(md_path, target)
        if not local_path.exists() or not local_path.is_file():
            logger.warning("Image not found: %s (md=%s, target=%s)", local_path, md_path, target)
            return None
        mime = mime_from_path(local_path)
        if not mime or not mime.startswith("image/"):
            logger.warning(
                "Unsupported image type: %s (md=%s, mime=%s)",
                local_path,
                md_path,
                mime or "unknown",
            )
            return None
        data = await asyncio.to_thread(local_path.read_bytes)
        return data_url_from_bytes(mime, data)

    return await rewrite_markdown_images(content, replacer)


async def unpack_markdown_images(
    content: str,
    images_dir: Path,
    name_registry: NameRegistry,
) -> str:
    images_dir.mkdir(parents=True, exist_ok=True)

    async def replacer(alt_text: str, target: str) -> Optional[str]:
        if not is_data_url(target):
            return None
        parsed = parse_data_url(target)
        if parsed is None:
            return None
        mime, data = parsed
        ext = extension_from_mime(mime)
        if not ext:
            logger.warning(
                "Unsupported MIME type: %s (alt=%s)",
                mime,
                alt_text or "<empty>",
            )
            return None
        base_name = base_name_from_alt(alt_text)
        if not base_name:
            base_name = hash_name_from_bytes(data)
        filename = await name_registry.reserve_async(base_name, ext)
        dest_path = images_dir / filename
        await asyncio.to_thread(dest_path.write_bytes, data)
        return f"images/{filename}"

    return await rewrite_markdown_images(content, replacer)
