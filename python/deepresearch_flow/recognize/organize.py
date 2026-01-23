"""OCR output organizers for recognize commands."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from deepresearch_flow.translator.fixers import fix_markdown

from deepresearch_flow.recognize.markdown import (
    NameRegistry,
    embed_markdown_images,
    read_text,
    rewrite_markdown_images,
    resolve_local_path,
    is_data_url,
    is_http_url,
)


logger = logging.getLogger(__name__)
_RUMDL_PATH = shutil.which("rumdl")
_RUMDL_WARNED = False


async def _format_markdown(text: str) -> str:
    global _RUMDL_WARNED
    if not _RUMDL_PATH:
        if not _RUMDL_WARNED:
            logger.warning("rumdl not available; skip markdown formatting (recognize)")
            _RUMDL_WARNED = True
        return text

    def run_formatter() -> str:
        try:
            proc = subprocess.run(
                [_RUMDL_PATH, "fmt", "--stdin", "--quiet"],
                input=text,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            message = str(exc).strip() or "unknown error"
            logger.warning("rumdl fmt failed (oserror=%s): %s", type(exc).__name__, message)
            return text
        if proc.returncode != 0:
            logger.warning(
                "rumdl fmt failed (rc=%s): %s",
                proc.returncode,
                proc.stderr.strip() or "unknown error",
            )
            return text
        return proc.stdout or text

    return await asyncio.to_thread(run_formatter)


def _apply_fix(text: str, fix_level: str) -> str:
    if fix_level == "off":
        return text
    return fix_markdown(text, fix_level)


async def fix_markdown_text(
    text: str,
    fix_level: str,
    format_enabled: bool,
) -> str:
    text = _apply_fix(text, fix_level)
    if format_enabled:
        text = await _format_markdown(text)
    return text


def discover_mineru_dirs(inputs: Iterable[str], recursive: bool) -> list[Path]:
    results: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            if path.name != "full.md":
                raise FileNotFoundError(f"Expected full.md file but got: {path}")
            parent = path.parent.resolve()
            if not (parent / "images").is_dir():
                logger.warning(
                    "Missing images/ for %s; continuing (expected=%s)",
                    parent,
                    parent / "images",
                )
            results.add(parent)
            continue
        if not path.exists():
            raise FileNotFoundError(f"Input path not found: {path}")
        if path.is_dir():
            if (path / "full.md").is_file():
                if not (path / "images").is_dir():
                    logger.warning(
                        "Missing images/ for %s; continuing (expected=%s)",
                        path,
                        path / "images",
                    )
                results.add(path.resolve())
            pattern = path.rglob("full.md") if recursive else path.glob("full.md")
            for full_path in pattern:
                parent = full_path.parent.resolve()
                if not (parent / "images").is_dir():
                    logger.warning(
                        "Missing images/ for %s; continuing (expected=%s)",
                        parent,
                        parent / "images",
                    )
                results.add(parent)
            continue
        raise FileNotFoundError(f"Input path not found: {path}")
    return sorted(results)


async def organize_mineru_dir(
    layout_dir: Path,
    output_simple: Path | None,
    output_base64: Path | None,
    output_filename: str,
    image_registry: NameRegistry | None,
    fix_level: str | None,
    format_enabled: bool,
) -> None:
    md_path = layout_dir / "full.md"
    content = await asyncio.to_thread(read_text, md_path)
    if fix_level is not None:
        content = _apply_fix(content, fix_level)

    if output_simple is not None and image_registry is not None:
        images_dir = output_simple / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        image_map: dict[Path, str] = {}

        async def replace_simple(_: str, target: str) -> str | None:
            if not target or is_data_url(target) or is_http_url(target):
                return None
            source_path = resolve_local_path(md_path, target)
            if not source_path.exists() or not source_path.is_file():
                logger.warning(
                    "Image not found: %s (md=%s, target=%s)",
                    source_path,
                    md_path,
                    target,
                )
                return None
            if source_path in image_map:
                return f"images/{image_map[source_path]}"
            filename = await image_registry.reserve_async(source_path.stem, source_path.suffix)
            dest_path = images_dir / filename
            await asyncio.to_thread(shutil.copy2, source_path, dest_path)
            image_map[source_path] = filename
            return f"images/{filename}"

        updated = await rewrite_markdown_images(content, replace_simple)
        if format_enabled:
            updated = await _format_markdown(updated)
        output_path = output_simple / output_filename
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    if output_base64 is not None:
        updated = await embed_markdown_images(content, md_path, False, None)
        if format_enabled:
            updated = await _format_markdown(updated)
        output_path = output_base64 / output_filename
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")
