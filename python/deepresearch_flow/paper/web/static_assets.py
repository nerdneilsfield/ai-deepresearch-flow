"""Static asset export and URL mapping for paper web UI."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any

from deepresearch_flow.paper.db_ops import PaperIndex

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_DATA_URL_PATTERN = re.compile(r"^data:([^;,]+)(;base64)?,(.*)$", re.DOTALL)
_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_PATTERN = re.compile(r"\bsrc\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE | re.DOTALL)

_EXTENSION_OVERRIDES = {
    ".jpe": ".jpg",
}


@dataclass(frozen=True)
class StaticAssetConfig:
    enabled: bool
    base_url: str | None
    images_base_url: str | None
    pdf_urls: dict[str, str]
    md_urls: dict[str, str]
    translated_md_urls: dict[str, dict[str, str]]


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def _extension_from_mime(mime: str) -> str | None:
    ext = mimetypes.guess_extension(mime, strict=False)
    if ext in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[ext]
    return ext


def _parse_data_url(target: str) -> tuple[str, bytes] | None:
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


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_link_target(raw_link: str) -> tuple[str, str, str, str]:
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


class _ImageStore:
    def __init__(self, output_dir: Path | None) -> None:
        self.output_dir = output_dir
        self._written: set[str] = set()
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    def add_image(self, mime: str, data: bytes) -> str | None:
        ext = _extension_from_mime(mime)
        if not ext:
            return None
        digest = _hash_bytes(data)
        filename = f"{digest}{ext}"
        if self.output_dir and filename not in self._written:
            dest = self.output_dir / filename
            if not dest.exists():
                dest.write_bytes(data)
            self._written.add(filename)
        return f"images/{filename}"


def _rewrite_markdown_images(text: str, store: _ImageStore) -> str:
    def replace_md(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        raw_link = match.group(2)
        target, suffix, prefix, postfix = _split_link_target(raw_link)
        parsed = _parse_data_url(target)
        if parsed is None:
            return match.group(0)
        mime, data = parsed
        replacement = store.add_image(mime, data)
        if not replacement:
            return match.group(0)
        new_link = f"{prefix}{replacement}{postfix}{suffix}"
        return f"![{alt_text}]({new_link})"

    text = _IMAGE_PATTERN.sub(replace_md, text)

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
        if parsed is None:
            return tag
        mime, data = parsed
        replacement = store.add_image(mime, data)
        if not replacement:
            return tag
        new_src = f"{quote}{replacement}{quote}" if quote else replacement
        return tag[: src_match.start(1)] + new_src + tag[src_match.end(1) :]

    return _IMG_TAG_PATTERN.sub(replace_img, text)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def build_static_assets(
    index: PaperIndex,
    *,
    static_base_url: str | None,
    static_export_dir: Path | None = None,
    allow_empty_base: bool = False,
) -> StaticAssetConfig:
    if static_base_url is None:
        if not allow_empty_base:
            return StaticAssetConfig(
                enabled=False,
                base_url=None,
                images_base_url=None,
                pdf_urls={},
                md_urls={},
                translated_md_urls={},
            )
        base_url = ""
    else:
        base_url = _normalize_base_url(static_base_url)
        if not base_url and not allow_empty_base:
            return StaticAssetConfig(
                enabled=False,
                base_url=None,
                images_base_url=None,
                pdf_urls={},
                md_urls={},
                translated_md_urls={},
            )

    images_base_url = f"{base_url}/images"

    pdf_urls: dict[str, str] = {}
    md_urls: dict[str, str] = {}
    translated_md_urls: dict[str, dict[str, str]] = {}

    images_dir = static_export_dir / "images" if static_export_dir else None
    md_dir = static_export_dir / "md" if static_export_dir else None
    md_translate_dir = static_export_dir / "md_translate" if static_export_dir else None
    pdf_dir = static_export_dir / "pdf" if static_export_dir else None

    store = _ImageStore(images_dir)

    if md_dir:
        md_dir.mkdir(parents=True, exist_ok=True)
    if md_translate_dir:
        md_translate_dir.mkdir(parents=True, exist_ok=True)
    if pdf_dir:
        pdf_dir.mkdir(parents=True, exist_ok=True)

    for source_hash, md_path in index.md_path_by_hash.items():
        raw = _safe_read_text(md_path)
        rewritten = _rewrite_markdown_images(raw, store)
        md_hash = _hash_text(rewritten)
        md_urls[source_hash] = f"{base_url}/md/{md_hash}.md"
        if md_dir:
            target = md_dir / f"{md_hash}.md"
            if not target.exists():
                target.write_text(rewritten, encoding="utf-8")

    for source_hash, translations in index.translated_md_by_hash.items():
        translated_md_urls[source_hash] = {}
        for lang, md_path in translations.items():
            raw = _safe_read_text(md_path)
            rewritten = _rewrite_markdown_images(raw, store)
            md_hash = _hash_text(rewritten)
            translated_md_urls[source_hash][lang] = f"{base_url}/md_translate/{lang}/{md_hash}.md"
            if md_translate_dir:
                lang_dir = md_translate_dir / lang
                lang_dir.mkdir(parents=True, exist_ok=True)
                target = lang_dir / f"{md_hash}.md"
                if not target.exists():
                    target.write_text(rewritten, encoding="utf-8")

    for source_hash, pdf_path in index.pdf_path_by_hash.items():
        pdf_hash = _hash_file(pdf_path)
        pdf_urls[source_hash] = f"{base_url}/pdf/{pdf_hash}.pdf"
        if pdf_dir:
            target = pdf_dir / f"{pdf_hash}.pdf"
            if not target.exists():
                target.write_bytes(pdf_path.read_bytes())

    return StaticAssetConfig(
        enabled=True,
        base_url=base_url,
        images_base_url=images_base_url,
        pdf_urls=pdf_urls,
        md_urls=md_urls,
        translated_md_urls=translated_md_urls,
    )


def resolve_asset_urls(
    index: PaperIndex,
    source_hash: str,
    asset_config: StaticAssetConfig | None,
    *,
    prefer_local: bool = False,
) -> dict[str, Any]:
    """Resolve asset URLs for a paper based on static asset config or local endpoints."""
    if prefer_local:
        translations = index.translated_md_by_hash.get(source_hash, {})
        images_base_url = asset_config.images_base_url if asset_config and asset_config.enabled else None
        return {
            "pdf_url": f"/api/pdf/{source_hash}" if source_hash in index.pdf_path_by_hash else None,
            "md_url": f"/api/dev/markdown/{source_hash}" if source_hash in index.md_path_by_hash else None,
            "md_translated_url": {
                lang: f"/api/dev/markdown/{source_hash}?lang={lang}" for lang in translations
            },
            "images_base_url": images_base_url,
        }
    if asset_config and asset_config.enabled:
        return {
            "pdf_url": asset_config.pdf_urls.get(source_hash),
            "md_url": asset_config.md_urls.get(source_hash),
            "md_translated_url": asset_config.translated_md_urls.get(source_hash, {}),
            "images_base_url": asset_config.images_base_url,
        }

    translations = index.translated_md_by_hash.get(source_hash, {})
    return {
        "pdf_url": f"/api/pdf/{source_hash}" if source_hash in index.pdf_path_by_hash else None,
        "md_url": f"/api/dev/markdown/{source_hash}" if source_hash in index.md_path_by_hash else None,
        "md_translated_url": {
            lang: f"/api/dev/markdown/{source_hash}?lang={lang}" for lang in translations
        },
        "images_base_url": None,
    }
