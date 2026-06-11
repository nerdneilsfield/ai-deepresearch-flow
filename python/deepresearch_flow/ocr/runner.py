"""OCR runner — orchestrates file discovery, backend calls, and output writing."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict

from deepresearch_flow.ocr.base import OcrBackend, OcrPage, OcrResult

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
_PAGE_SEPARATOR = "\n\n---\n\n"


class OcrStats(TypedDict):
    processed: int
    failed: int
    skipped: int


class ProgressBarLike(Protocol):
    """Minimal progress API needed by the runner."""

    def update(self, amount: int) -> None: ...


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
        f for f in path.iterdir() if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
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


def _ocr_with_retry(
    backend: OcrBackend,
    file_path: Path,
    max_retries: int,
) -> OcrResult:
    """Call backend.ocr with exponential backoff retries."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return backend.ocr(file_path)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.warning(
                    "OCR attempt %d/%d failed for %s, retrying in %ds: %s",
                    attempt,
                    max_retries,
                    file_path.name,
                    wait,
                    exc,
                )
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def run_ocr(
    backend: OcrBackend,
    input_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    max_retries: int = 1,
    progress: ProgressBarLike | None = None,
) -> tuple[OcrStats, list[OcrFileResult]]:
    """Run OCR on input file(s) and write results to output_dir."""
    files = discover_files(input_path)
    stats: OcrStats = {"processed": 0, "failed": 0, "skipped": 0}
    results: list[OcrFileResult] = []

    for file_path in files:
        try:
            if not overwrite and _has_existing_output(output_dir, file_path.stem):
                logger.info("Skipping (already exists): %s", file_path.name)
                stats["skipped"] += 1
                results.append(OcrFileResult(name=file_path.name, status="skipped"))
                continue

            logger.info("Processing: %s", file_path.name)
            try:
                result = _ocr_with_retry(backend, file_path, max_retries)
            except Exception as exc:
                logger.error(
                    "Failed to OCR %s after %d attempt(s): %s", file_path.name, max_retries, exc
                )
                stats["failed"] += 1
                results.append(
                    OcrFileResult(
                        name=file_path.name,
                        status="failed",
                        error=str(exc),
                    )
                )
                continue

            if not result.pages:
                logger.warning("Empty OCR result for %s, skipping", file_path.name)
                stats["skipped"] += 1
                results.append(OcrFileResult(name=file_path.name, status="skipped"))
                continue

            doc_dir = output_dir / file_path.stem
            if overwrite and doc_dir.exists():
                import shutil

                shutil.rmtree(doc_dir)

            doc_dir = _resolve_output_dir(output_dir, file_path.stem)
            markdown, images, missing = _merge_pages(result.pages)
            _write_output(doc_dir, markdown, images, missing)

            stats["processed"] += 1
            results.append(
                OcrFileResult(
                    name=file_path.name,
                    status="processed",
                    pages=len(result.pages),
                    images=len(images),
                )
            )
            logger.info("Written: %s/full.md (%d pages)", doc_dir.name, len(result.pages))
        finally:
            if progress is not None:
                progress.update(1)

    return stats, results


# ---------------------------------------------------------------------------
# Migration: rename old page_XXXX_XX_*.ext images to content-hash names
# ---------------------------------------------------------------------------

_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class OcrFileResult:
    """Result for a single file OCR processing."""

    name: str
    status: str  # "processed" | "skipped" | "failed"
    pages: int = 0
    images: int = 0
    error: str = ""


@dataclass
class MigrateResult:
    """Result for a single document directory migration."""

    name: str
    status: str  # "migrated" | "skipped" | "failed"
    images_renamed: int = 0
    error: str = ""


def migrate_to_hash_names(
    ocr_output_dir: Path, *, dry_run: bool = False
) -> tuple[dict[str, int], list[MigrateResult]]:
    """Migrate existing OCR output from sequential names to content-hash names.

    Scans all subdirectories of *ocr_output_dir* for full.md + images/.
    For each image file, computes sha256 hash of content and renames to
    ``images/{hash12}.{ext}``. Updates references in full.md accordingly.

    Returns (stats dict, list of per-directory results).
    """
    stats = {"migrated": 0, "skipped": 0, "failed": 0}
    results: list[MigrateResult] = []

    if not ocr_output_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {ocr_output_dir}")

    for doc_dir in sorted(ocr_output_dir.iterdir()):
        full_md = doc_dir / "full.md"
        images_dir = doc_dir / "images"
        if not full_md.is_file():
            continue

        # Build rename map for all image files.
        rename_map: dict[str, str] = {}  # old_relative -> new_relative
        if images_dir.is_dir():
            for img_file in sorted(images_dir.iterdir()):
                if not img_file.is_file():
                    continue
                content = img_file.read_bytes()
                digest = hashlib.sha256(content).hexdigest()[:12]
                new_name = f"{digest}{img_file.suffix.lower()}"
                old_rel = f"images/{img_file.name}"
                new_rel = f"images/{new_name}"
                if old_rel != new_rel:
                    rename_map[old_rel] = new_rel

        if not rename_map:
            logger.info("Already migrated: %s", doc_dir.name)
            stats["skipped"] += 1
            results.append(MigrateResult(name=doc_dir.name, status="skipped"))
            continue

        if dry_run:
            stats["migrated"] += 1
            results.append(
                MigrateResult(
                    name=doc_dir.name,
                    status="migrated",
                    images_renamed=len(rename_map),
                )
            )
            continue

        try:
            # 1) Update full.md references.
            md_text = full_md.read_text(encoding="utf-8")
            for old_rel, new_rel in rename_map.items():
                md_text = md_text.replace(old_rel, new_rel)
            full_md.write_text(md_text, encoding="utf-8")

            # 2) Rename image files (use temp names to avoid collisions).
            temp_map: dict[Path, Path] = {}
            for old_rel, new_rel in rename_map.items():
                old_path = doc_dir / old_rel
                tmp_path = old_path.with_suffix(old_path.suffix + ".tmp_migrate")
                if old_path.exists():
                    old_path.rename(tmp_path)
                    temp_map[tmp_path] = doc_dir / new_rel

            for tmp_path, new_path in temp_map.items():
                tmp_path.rename(new_path)

            stats["migrated"] += 1
            results.append(
                MigrateResult(
                    name=doc_dir.name,
                    status="migrated",
                    images_renamed=len(rename_map),
                )
            )
            logger.info("Migrated: %s (%d images renamed)", doc_dir.name, len(rename_map))

        except Exception as exc:
            logger.exception("Failed to migrate %s", doc_dir.name)
            stats["failed"] += 1
            results.append(
                MigrateResult(
                    name=doc_dir.name,
                    status="failed",
                    error=str(exc),
                )
            )

    return stats, results
