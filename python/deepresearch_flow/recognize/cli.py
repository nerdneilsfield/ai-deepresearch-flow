"""CLI commands for recognize workflows."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import click
import httpx

from deepresearch_flow.paper.utils import discover_markdown
from deepresearch_flow.recognize.markdown import (
    DEFAULT_USER_AGENT,
    HTTP_TIMEOUT_SECONDS,
    NameRegistry,
    embed_markdown_images,
    read_text,
    sanitize_filename,
    unpack_markdown_images,
)
from deepresearch_flow.recognize.organize import discover_mineru_dirs, organize_mineru_dir


logger = logging.getLogger(__name__)


def _ensure_output_dir(path_str: str) -> Path:
    output_dir = Path(path_str)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _unique_output_filename(
    base: str,
    output_dirs: Iterable[Path],
    used: set[str],
) -> str:
    base = sanitize_filename(base) or "document"
    candidate = f"{base}.md"
    counter = 0
    while candidate in used or any((directory / candidate).exists() for directory in output_dirs):
        counter += 1
        candidate = f"{base}_{counter}.md"
    used.add(candidate)
    return candidate


def _map_output_files(paths: Iterable[Path], output_dirs: list[Path]) -> dict[Path, str]:
    used: set[str] = set()
    mapping: dict[Path, str] = {}
    for path in paths:
        base = path.stem
        mapping[path] = _unique_output_filename(base, output_dirs, used)
    return mapping


async def _run_with_workers(
    items: Iterable[Path],
    workers: int,
    handler: Callable[[Path], Awaitable[None]],
) -> None:
    semaphore = asyncio.Semaphore(workers)

    async def runner(item: Path) -> None:
        async with semaphore:
            await handler(item)

    await asyncio.gather(*(runner(item) for item in items))


async def _run_md_embed(
    paths: list[Path],
    output_dir: Path,
    output_map: dict[Path, str],
    enable_http: bool,
    workers: int,
) -> None:
    timeout = httpx.Timeout(HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    client: httpx.AsyncClient | None = None
    if enable_http:
        client = httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)

    async def handler(path: Path) -> None:
        content = await asyncio.to_thread(read_text, path)
        updated = await embed_markdown_images(content, path, enable_http, client)
        output_path = output_dir / output_map[path]
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    try:
        await _run_with_workers(paths, workers, handler)
    finally:
        if client is not None:
            await client.aclose()


async def _run_md_unpack(
    paths: list[Path],
    output_dir: Path,
    output_map: dict[Path, str],
    workers: int,
) -> None:
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    name_registry = NameRegistry(images_dir)

    async def handler(path: Path) -> None:
        content = await asyncio.to_thread(read_text, path)
        updated = await unpack_markdown_images(content, images_dir, name_registry)
        output_path = output_dir / output_map[path]
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    await _run_with_workers(paths, workers, handler)


async def _run_organize(
    layout_dirs: list[Path],
    output_simple: Path | None,
    output_base64: Path | None,
    output_map: dict[Path, str],
    workers: int,
) -> None:
    image_registry = None
    if output_simple is not None:
        images_dir = output_simple / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        image_registry = NameRegistry(images_dir)

    async def handler(layout_dir: Path) -> None:
        output_filename = output_map[layout_dir]
        await organize_mineru_dir(
            layout_dir,
            output_simple,
            output_base64,
            output_filename,
            image_registry,
        )

    await _run_with_workers(layout_dirs, workers, handler)


@click.group()
def recognize() -> None:
    """OCR recognition and Markdown post-processing commands."""


@recognize.group()
def md() -> None:
    """Markdown image utilities."""


@md.command()
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown file or directory (repeatable)",
)
@click.option("-o", "--output", "output_dir", required=True, help="Output directory")
@click.option("-r", "--recursive", is_flag=True, help="Recursively discover markdown files")
@click.option("--enable-http", is_flag=True, help="Allow embedding HTTP(S) images")
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
def embed(
    inputs: tuple[str, ...],
    output_dir: str,
    recursive: bool,
    enable_http: bool,
    workers: int,
) -> None:
    """Embed images into markdown as data URLs."""
    logging.basicConfig(level=logging.INFO)
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    output_path = _ensure_output_dir(output_dir)
    paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No markdown files discovered")
        return
    output_map = _map_output_files(paths, [output_path])
    asyncio.run(_run_md_embed(paths, output_path, output_map, enable_http, workers))


@md.command()
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown file or directory (repeatable)",
)
@click.option("-o", "--output", "output_dir", required=True, help="Output directory")
@click.option("-r", "--recursive", is_flag=True, help="Recursively discover markdown files")
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
def unpack(
    inputs: tuple[str, ...],
    output_dir: str,
    recursive: bool,
    workers: int,
) -> None:
    """Extract embedded data URLs into image files."""
    logging.basicConfig(level=logging.INFO)
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    output_path = _ensure_output_dir(output_dir)
    paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No markdown files discovered")
        return
    output_map = _map_output_files(paths, [output_path])
    asyncio.run(_run_md_unpack(paths, output_path, output_map, workers))


@recognize.command()
@click.option(
    "--layout",
    "layout",
    type=click.Choice(["mineru"]),
    default="mineru",
    show_default=True,
    help="OCR output layout type",
)
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input directory (repeatable)",
)
@click.option("-r", "--recursive", is_flag=True, help="Recursively search for layout folders")
@click.option("--output-simple", "output_simple", default=None, help="Output directory for copied markdown")
@click.option("--output-base64", "output_base64", default=None, help="Output directory for embedded markdown")
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
def organize(
    layout: str,
    inputs: tuple[str, ...],
    recursive: bool,
    output_simple: str | None,
    output_base64: str | None,
    workers: int,
) -> None:
    """Organize OCR outputs into markdown files."""
    logging.basicConfig(level=logging.INFO)
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    if output_simple is None and output_base64 is None:
        raise click.ClickException("At least one of --output-simple or --output-base64 is required")

    if layout != "mineru":
        raise click.ClickException(f"Unsupported layout: {layout}")

    output_simple_path = _ensure_output_dir(output_simple) if output_simple else None
    output_base64_path = _ensure_output_dir(output_base64) if output_base64 else None
    output_dirs = [path for path in (output_simple_path, output_base64_path) if path]

    layout_dirs = discover_mineru_dirs(inputs, recursive)
    if not layout_dirs:
        click.echo("No layout directories discovered")
        return

    output_map = _map_output_files(layout_dirs, output_dirs)
    asyncio.run(_run_organize(layout_dirs, output_simple_path, output_base64_path, output_map, workers))
