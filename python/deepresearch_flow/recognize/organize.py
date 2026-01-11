"""OCR output organizers for recognize commands."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Iterable

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


def discover_mineru_dirs(inputs: Iterable[str], recursive: bool) -> list[Path]:
    results: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            if path.name != "full.md":
                raise FileNotFoundError(f"Expected full.md file but got: {path}")
            parent = path.parent.resolve()
            if (parent / "images").is_dir():
                results.add(parent)
            else:
                logger.warning("Skipping %s (missing images/)", parent)
            continue
        if not path.exists():
            raise FileNotFoundError(f"Input path not found: {path}")
        if path.is_dir():
            if (path / "full.md").is_file():
                if (path / "images").is_dir():
                    results.add(path.resolve())
                else:
                    logger.warning("Skipping %s (missing images/)", path)
            pattern = path.rglob("full.md") if recursive else path.glob("full.md")
            for full_path in pattern:
                parent = full_path.parent.resolve()
                if (parent / "images").is_dir():
                    results.add(parent)
                else:
                    logger.warning("Skipping %s (missing images/)", parent)
            continue
        raise FileNotFoundError(f"Input path not found: {path}")
    return sorted(results)


async def organize_mineru_dir(
    layout_dir: Path,
    output_simple: Path | None,
    output_base64: Path | None,
    output_filename: str,
    image_registry: NameRegistry | None,
) -> None:
    md_path = layout_dir / "full.md"
    content = await asyncio.to_thread(read_text, md_path)

    if output_simple is not None and image_registry is not None:
        images_dir = output_simple / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        image_map: dict[Path, str] = {}

        async def replace_simple(_: str, target: str) -> str | None:
            if not target or is_data_url(target) or is_http_url(target):
                return None
            source_path = resolve_local_path(md_path, target)
            if not source_path.exists() or not source_path.is_file():
                logger.warning("Image not found: %s", source_path)
                return None
            if source_path in image_map:
                return f"images/{image_map[source_path]}"
            filename = await image_registry.reserve_async(source_path.stem, source_path.suffix)
            dest_path = images_dir / filename
            await asyncio.to_thread(shutil.copy2, source_path, dest_path)
            image_map[source_path] = filename
            return f"images/{filename}"

        updated = await rewrite_markdown_images(content, replace_simple)
        output_path = output_simple / output_filename
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    if output_base64 is not None:
        updated = await embed_markdown_images(content, md_path, False, None)
        output_path = output_base64 / output_filename
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")
