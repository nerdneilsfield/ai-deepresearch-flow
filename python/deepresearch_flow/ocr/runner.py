"""OCR runner — orchestrates file discovery, backend calls, and output writing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from deepresearch_flow.ocr.base import OcrBackend, OcrPage

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_PAGE_SEPARATOR = "\n\n---\n\n"


class OcrStats(TypedDict):
    processed: int
    failed: int
    skipped: int


def discover_files(path: Path) -> list[Path]:
    """Discover OCR-able files from a path (file or directory)."""
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension: {path.suffix}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
            )
        return [path]

    # Directory: collect all supported files.
    files = sorted(
        f
        for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
    )
    return files


def _merge_pages(
    pages: list[OcrPage],
) -> tuple[str, dict[str, bytes], list[str]]:
    """Merge multiple OcrPages into a single markdown string, combined images dict, and missing list."""
    markdown_parts: list[str] = []
    all_images: dict[str, bytes] = {}
    all_missing: list[str] = []

    for page in pages:
        markdown_parts.append(page.markdown)
        all_images.update(page.images)
        all_missing.extend(page.missing_images)

    merged_md = _PAGE_SEPARATOR.join(markdown_parts)
    return merged_md, all_images, all_missing


def _resolve_output_dir(base: Path, stem: str) -> Path:
    """Resolve output directory, appending _N suffix on collision."""
    candidate = base / stem
    if not candidate.exists():
        return candidate

    n = 1
    while True:
        candidate = base / f"{stem}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def _write_output(
    output_dir: Path,
    markdown: str,
    images: dict[str, bytes],
    missing: list[str],
) -> None:
    """Write merged markdown and images to the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write full.md.
    (output_dir / "full.md").write_text(markdown, encoding="utf-8")

    # Write images.
    for rel_path, data in images.items():
        img_path = output_dir / rel_path
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(data)

    # Log missing images.
    for path in missing:
        logger.warning("Missing image in output %s: %s", output_dir.name, path)


def _has_existing_output(base: Path, stem: str) -> bool:
    """Check if output already exists for this file stem."""
    candidate = base / stem
    return (candidate / "full.md").is_file()


def run_ocr(
    backend: OcrBackend,
    input_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> OcrStats:
    """Run OCR on input file(s) and write results to output_dir."""
    files = discover_files(input_path)
    stats: OcrStats = {"processed": 0, "failed": 0, "skipped": 0}

    for file_path in files:
        if not overwrite and _has_existing_output(output_dir, file_path.stem):
            logger.info("Skipping (already exists): %s", file_path.name)
            stats["skipped"] += 1
            continue

        logger.info("Processing: %s", file_path.name)
        try:
            result = backend.ocr(file_path)
        except Exception:
            logger.exception("Failed to OCR %s", file_path.name)
            stats["failed"] += 1
            continue

        if not result.pages:
            logger.warning("Empty OCR result for %s, skipping", file_path.name)
            stats["skipped"] += 1
            continue

        doc_dir = output_dir / file_path.stem
        if overwrite and doc_dir.exists():
            import shutil
            shutil.rmtree(doc_dir)

        doc_dir = _resolve_output_dir(output_dir, file_path.stem)
        markdown, images, missing = _merge_pages(result.pages)
        _write_output(doc_dir, markdown, images, missing)

        stats["processed"] += 1
        logger.info("Written: %s/full.md (%d pages)", doc_dir.name, len(result.pages))

    return stats
