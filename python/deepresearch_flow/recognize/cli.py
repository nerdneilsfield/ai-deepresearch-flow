"""CLI commands for recognize workflows."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import click
import coloredlogs
import httpx
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

from deepresearch_flow.paper.utils import discover_markdown
from deepresearch_flow.paper.template_registry import get_stage_definitions
from deepresearch_flow.recognize.markdown import (
    DEFAULT_USER_AGENT,
    HTTP_TIMEOUT_SECONDS,
    NameRegistry,
    count_markdown_images,
    embed_markdown_images,
    read_text,
    sanitize_filename,
    unpack_markdown_images,
)
from deepresearch_flow.recognize.organize import (
    discover_mineru_dirs,
    fix_markdown_text,
    organize_mineru_dir,
)


logger = logging.getLogger(__name__)


def configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")


def _ensure_output_dir(path_str: str) -> Path:
    output_dir = Path(path_str)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def _warn_if_not_empty(output_dir: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        logger.warning("Output directory not empty: %s", output_dir)


def _print_summary(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(title=title, header_style="bold cyan", title_style="bold magenta")
    table.add_column("Item", style="cyan", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold")
    for key, value in rows:
        table.add_row(key, value)
    Console().print(table)


def _unique_output_filename(
    base: str,
    output_dirs: Iterable[Path],
    used: set[str],
    ext: str,
) -> str:
    base = sanitize_filename(base) or "document"
    candidate = f"{base}{ext}"
    counter = 0
    while candidate in used or any((directory / candidate).exists() for directory in output_dirs):
        counter += 1
        candidate = f"{base}_{counter}{ext}"
    used.add(candidate)
    return candidate


def _map_output_files(
    paths: Iterable[Path],
    output_dirs: list[Path],
    ext: str = ".md",
) -> dict[Path, str]:
    used: set[str] = set()
    mapping: dict[Path, str] = {}
    for path in paths:
        base = path.stem
        mapping[path] = _unique_output_filename(base, output_dirs, used, ext)
    return mapping


def _aggregate_image_counts(paths: Iterable[Path]) -> dict[str, int]:
    totals = {"total": 0, "data": 0, "http": 0, "local": 0}
    for path in paths:
        content = read_text(path)
        counts = count_markdown_images(content)
        for key in totals:
            totals[key] += counts.get(key, 0)
    return totals


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {remainder:.1f}s"


def discover_json(inputs: Iterable[str], recursive: bool) -> list[Path]:
    files: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            if path.suffix.lower() != ".json":
                raise ValueError(f"Input file is not a json file: {path}")
            files.add(path.resolve())
            continue

        if path.is_dir():
            pattern = path.rglob("*.json") if recursive else path.glob("*.json")
            for match in pattern:
                if match.is_file():
                    files.add(match.resolve())
            continue

        raise FileNotFoundError(f"Input path not found: {path}")

    return sorted(files)


def _load_json_payload(path: Path) -> tuple[list[Any], dict[str, Any] | None, str | None]:
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {path}: {exc}") from exc

    if isinstance(data, list):
        return data, None, None
    if isinstance(data, dict):
        papers = data.get("papers")
        if isinstance(papers, list):
            template_tag = data.get("template_tag")
            return papers, data, template_tag if isinstance(template_tag, str) else None
        raise click.ClickException(f"JSON object missing 'papers' list: {path}")

    raise click.ClickException(f"Unsupported JSON structure in {path}")


def _resolve_item_template(item: dict[str, Any], default_template: str | None) -> str | None:
    raw = item.get("template_tag") or item.get("prompt_template") or default_template
    if isinstance(raw, str) and raw:
        return raw
    return None


def _template_markdown_fields(template: str | None) -> list[str]:
    if template:
        stages = get_stage_definitions(template)
        if stages:
            return [field for stage in stages for field in stage.fields]
    return ["summary", "abstract"]


async def _fix_json_items(
    items: list[Any],
    default_template: str | None,
    fix_level: str,
    format_enabled: bool,
) -> tuple[int, int, int, int]:
    items_total = 0
    items_updated = 0
    fields_total = 0
    fields_updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        items_total += 1
        template = _resolve_item_template(item, default_template)
        fields = _template_markdown_fields(template)
        item_updated = False
        for field in fields:
            value = item.get(field)
            if not isinstance(value, str):
                continue
            fields_total += 1
            updated = await fix_markdown_text(value, fix_level, format_enabled)
            if updated != value:
                item[field] = updated
                fields_updated += 1
                item_updated = True
        if item_updated:
            items_updated += 1
    return items_total, items_updated, fields_total, fields_updated


async def _run_with_workers(
    items: Iterable[Path],
    workers: int,
    handler: Callable[[Path], Awaitable[None]],
    progress: tqdm | None = None,
) -> None:
    semaphore = asyncio.Semaphore(workers)
    progress_lock = asyncio.Lock() if progress else None

    async def runner(item: Path) -> None:
        async with semaphore:
            await handler(item)
            if progress and progress_lock:
                async with progress_lock:
                    progress.update(1)

    await asyncio.gather(*(runner(item) for item in items))


async def _run_md_embed(
    paths: list[Path],
    output_dir: Path,
    output_map: dict[Path, str],
    enable_http: bool,
    workers: int,
    progress: tqdm | None,
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
        await _run_with_workers(paths, workers, handler, progress=progress)
    finally:
        if client is not None:
            await client.aclose()


async def _run_md_unpack(
    paths: list[Path],
    output_dir: Path,
    output_map: dict[Path, str],
    workers: int,
    progress: tqdm | None,
) -> None:
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    name_registry = NameRegistry(images_dir)

    async def handler(path: Path) -> None:
        content = await asyncio.to_thread(read_text, path)
        updated = await unpack_markdown_images(content, images_dir, name_registry)
        output_path = output_dir / output_map[path]
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    await _run_with_workers(paths, workers, handler, progress=progress)


async def _run_organize(
    layout_dirs: list[Path],
    output_simple: Path | None,
    output_base64: Path | None,
    output_map: dict[Path, str],
    workers: int,
    fix_level: str | None,
    format_enabled: bool,
    progress: tqdm | None,
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
            fix_level,
            format_enabled,
        )

    await _run_with_workers(layout_dirs, workers, handler, progress=progress)


async def _run_fix(
    paths: list[Path],
    output_map: dict[Path, Path],
    fix_level: str,
    format_enabled: bool,
    workers: int,
    progress: tqdm | None,
) -> None:
    async def handler(path: Path) -> None:
        content = await asyncio.to_thread(read_text, path)
        updated = await fix_markdown_text(content, fix_level, format_enabled)
        output_path = output_map[path]
        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")

    await _run_with_workers(paths, workers, handler, progress=progress)


async def _run_fix_json(
    paths: list[Path],
    output_map: dict[Path, Path],
    fix_level: str,
    format_enabled: bool,
    workers: int,
    progress: tqdm | None,
) -> list[tuple[int, int, int, int, int]]:
    semaphore = asyncio.Semaphore(workers)
    progress_lock = asyncio.Lock() if progress else None
    results: list[tuple[int, int, int, int, int]] = []

    async def handler(path: Path) -> tuple[int, int, int, int, int]:
        items, payload, template_tag = _load_json_payload(path)
        items_total, items_updated, fields_total, fields_updated = await _fix_json_items(
            items, template_tag, fix_level, format_enabled
        )
        output_data: Any
        if payload is None:
            output_data = items
        else:
            payload["papers"] = items
            output_data = payload
        output_path = output_map[path]
        serialized = json.dumps(output_data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(output_path.write_text, f"{serialized}\n", encoding="utf-8")
        return len(items), items_total, items_updated, fields_total, fields_updated

    async def runner(path: Path) -> None:
        async with semaphore:
            result = await handler(path)
            results.append(result)
            if progress and progress_lock:
                async with progress_lock:
                    progress.update(1)

    await asyncio.gather(*(runner(path) for path in paths))
    return results


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
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def embed(
    inputs: tuple[str, ...],
    output_dir: str,
    recursive: bool,
    enable_http: bool,
    workers: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Embed images into markdown as data URLs."""
    configure_logging(verbose)
    start_time = time.monotonic()
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    output_path = Path(output_dir)
    if not dry_run:
        output_path = _ensure_output_dir(output_dir)
    _warn_if_not_empty(output_path)
    paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No markdown files discovered")
        return
    output_map = _map_output_files(paths, [output_path])
    image_counts = _aggregate_image_counts(paths)
    embed_count = image_counts["local"] + (image_counts["http"] if enable_http else 0)
    if dry_run:
        _print_summary(
            "recognize md embed (dry-run)",
            [
                ("Inputs", str(len(paths))),
                ("Outputs", str(len(output_map))),
                ("Images total", str(image_counts["total"])),
                ("Images to embed", str(embed_count)),
                ("Images data", str(image_counts["data"])),
                ("Images http", str(image_counts["http"])),
                ("Images local", str(image_counts["local"])),
                ("Output dir", _relative_path(output_path)),
                ("HTTP enabled", "yes" if enable_http else "no"),
                ("Duration", _format_duration(time.monotonic() - start_time)),
            ],
        )
        return

    progress = tqdm(total=len(paths), desc="embed", unit="file")
    try:
        asyncio.run(_run_md_embed(paths, output_path, output_map, enable_http, workers, progress))
    finally:
        progress.close()
    _print_summary(
        "recognize md embed",
        [
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Images total", str(image_counts["total"])),
            ("Images to embed", str(embed_count)),
            ("Images data", str(image_counts["data"])),
            ("Images http", str(image_counts["http"])),
            ("Images local", str(image_counts["local"])),
            ("Output dir", _relative_path(output_path)),
            ("HTTP enabled", "yes" if enable_http else "no"),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ],
    )


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
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def unpack(
    inputs: tuple[str, ...],
    output_dir: str,
    recursive: bool,
    workers: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Extract embedded data URLs into image files."""
    configure_logging(verbose)
    start_time = time.monotonic()
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    output_path = Path(output_dir)
    if not dry_run:
        output_path = _ensure_output_dir(output_dir)
    _warn_if_not_empty(output_path)
    paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No markdown files discovered")
        return
    output_map = _map_output_files(paths, [output_path])
    image_counts = _aggregate_image_counts(paths)
    if dry_run:
        _print_summary(
            "recognize md unpack (dry-run)",
            [
                ("Inputs", str(len(paths))),
                ("Outputs", str(len(output_map))),
                ("Images total", str(image_counts["total"])),
                ("Images embedded", str(image_counts["data"])),
                ("Images http", str(image_counts["http"])),
                ("Images local", str(image_counts["local"])),
                ("Output dir", _relative_path(output_path)),
                ("Duration", _format_duration(time.monotonic() - start_time)),
            ],
        )
        return

    progress = tqdm(total=len(paths), desc="unpack", unit="file")
    try:
        asyncio.run(_run_md_unpack(paths, output_path, output_map, workers, progress))
    finally:
        progress.close()
    _print_summary(
        "recognize md unpack",
        [
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Images total", str(image_counts["total"])),
            ("Images embedded", str(image_counts["data"])),
            ("Images http", str(image_counts["http"])),
            ("Images local", str(image_counts["local"])),
            ("Output dir", _relative_path(output_path)),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ],
    )


@recognize.group(invoke_without_command=True)
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
    required=False,
    help="Input directory (repeatable)",
)
@click.option("-r", "--recursive", is_flag=True, help="Recursively search for layout folders")
@click.option("--output-simple", "output_simple", default=None, help="Output directory for copied markdown")
@click.option("--output-base64", "output_base64", default=None, help="Output directory for embedded markdown")
@click.option("--fix", "enable_fix", is_flag=True, help="Apply OCR fix and rumdl formatting")
@click.option(
    "--fix-level",
    "fix_level",
    default="moderate",
    type=click.Choice(["off", "moderate", "aggressive"]),
    show_default=True,
    help="OCR fix level",
)
@click.option("--no-format", "no_format", is_flag=True, help="Disable rumdl formatting")
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def organize(
    ctx: click.Context,
    layout: str,
    inputs: tuple[str, ...],
    recursive: bool,
    output_simple: str | None,
    output_base64: str | None,
    enable_fix: bool,
    fix_level: str,
    no_format: bool,
    workers: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Organize OCR outputs into markdown files."""
    if ctx.invoked_subcommand:
        return
    configure_logging(verbose)
    start_time = time.monotonic()
    if not inputs:
        raise click.ClickException("--input is required")
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    if output_simple is None and output_base64 is None:
        raise click.ClickException("At least one of --output-simple or --output-base64 is required")

    if layout != "mineru":
        raise click.ClickException(f"Unsupported layout: {layout}")

    output_simple_path = Path(output_simple) if output_simple else None
    output_base64_path = Path(output_base64) if output_base64 else None
    if not dry_run:
        output_simple_path = _ensure_output_dir(output_simple) if output_simple else None
        output_base64_path = _ensure_output_dir(output_base64) if output_base64 else None
    output_dirs = [path for path in (output_simple_path, output_base64_path) if path]
    for output_dir in output_dirs:
        _warn_if_not_empty(output_dir)

    layout_dirs = discover_mineru_dirs(inputs, recursive)
    if not layout_dirs:
        click.echo("No layout directories discovered")
        return

    output_map = _map_output_files(layout_dirs, output_dirs)
    image_counts = _aggregate_image_counts([path / "full.md" for path in layout_dirs])
    fix_value = fix_level if enable_fix else None
    format_enabled = enable_fix and not no_format
    if dry_run:
        rows = [
            ("Layout", layout),
            ("Inputs", str(len(layout_dirs))),
            ("Outputs", str(len(output_map))),
            ("Images total", str(image_counts["total"])),
            ("Images data", str(image_counts["data"])),
            ("Images http", str(image_counts["http"])),
            ("Images local", str(image_counts["local"])),
            ("Fix", "yes" if enable_fix else "no"),
            ("Fix level", fix_level if enable_fix else "-"),
            ("Format", "no" if no_format else ("yes" if enable_fix else "-")),
            ("Output simple", _relative_path(output_simple_path) if output_simple_path else "-"),
            ("Output base64", _relative_path(output_base64_path) if output_base64_path else "-"),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ]
        _print_summary("recognize organize (dry-run)", rows)
        return

    progress = tqdm(total=len(layout_dirs), desc="organize", unit="doc")
    try:
        asyncio.run(
            _run_organize(
                layout_dirs,
                output_simple_path,
                output_base64_path,
                output_map,
                workers,
                fix_value,
                format_enabled,
                progress,
            )
        )
    finally:
        progress.close()
    rows = [
        ("Layout", layout),
        ("Inputs", str(len(layout_dirs))),
        ("Outputs", str(len(output_map))),
        ("Images total", str(image_counts["total"])),
        ("Images data", str(image_counts["data"])),
        ("Images http", str(image_counts["http"])),
        ("Images local", str(image_counts["local"])),
        ("Fix", "yes" if enable_fix else "no"),
        ("Fix level", fix_level if enable_fix else "-"),
        ("Format", "no" if no_format else ("yes" if enable_fix else "-")),
        ("Output simple", _relative_path(output_simple_path) if output_simple_path else "-"),
        ("Output base64", _relative_path(output_base64_path) if output_base64_path else "-"),
        ("Duration", _format_duration(time.monotonic() - start_time)),
    ]
    _print_summary("recognize organize", rows)


@recognize.command("fix")
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown or JSON file/directory (repeatable)",
)
@click.option("-o", "--output", "output_dir", default=None, help="Output directory")
@click.option("--in-place", "in_place", is_flag=True, help="Fix markdown files in place")
@click.option("-r", "--recursive", is_flag=True, help="Recursively discover files")
@click.option("--json", "json_mode", is_flag=True, help="Fix markdown fields inside JSON outputs")
@click.option(
    "--fix-level",
    "fix_level",
    default="moderate",
    type=click.Choice(["off", "moderate", "aggressive"]),
    show_default=True,
    help="OCR fix level",
)
@click.option("--no-format", "no_format", is_flag=True, help="Disable rumdl formatting")
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def recognize_fix(
    inputs: tuple[str, ...],
    output_dir: str | None,
    in_place: bool,
    recursive: bool,
    json_mode: bool,
    fix_level: str,
    no_format: bool,
    workers: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Fix and format OCR markdown outputs (markdown or JSON)."""
    configure_logging(verbose)
    start_time = time.monotonic()
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    if in_place and output_dir:
        raise click.ClickException("--in-place cannot be used with --output")
    if not in_place and not output_dir:
        raise click.ClickException("Either --in-place or --output is required")

    output_path = Path(output_dir) if output_dir else None
    if output_path and not dry_run:
        output_path = _ensure_output_dir(output_dir)
        _warn_if_not_empty(output_path)

    if json_mode:
        paths = discover_json(inputs, recursive=recursive)
    else:
        paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No files discovered")
        return

    format_enabled = not no_format
    if in_place:
        output_map = {path: path for path in paths}
    else:
        ext = ".json" if json_mode else ".md"
        output_map = {
            path: (output_path / name)
            for path, name in _map_output_files(paths, [output_path], ext=ext).items()
        }

    if dry_run:
        rows = [
            ("Mode", "json" if json_mode else "markdown"),
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Fix level", fix_level),
            ("Format", "no" if no_format else "yes"),
            ("In place", "yes" if in_place else "no"),
            ("Output dir", _relative_path(output_path) if output_path else "-"),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ]
        _print_summary("recognize fix (dry-run)", rows)
        return

    progress = tqdm(total=len(paths), desc="fix", unit="file")
    try:
        if json_mode:
            results = asyncio.run(
                _run_fix_json(
                    paths,
                    output_map,
                    fix_level,
                    format_enabled,
                    workers,
                    progress,
                )
            )
        else:
            asyncio.run(
                _run_fix(
                    paths,
                    output_map,
                    fix_level,
                    format_enabled,
                    workers,
                    progress,
                )
            )
    finally:
        progress.close()
    if json_mode:
        total_items = sum(result[0] for result in results)
        items_processed = sum(result[1] for result in results)
        items_updated = sum(result[2] for result in results)
        fields_total = sum(result[3] for result in results)
        fields_updated = sum(result[4] for result in results)
        items_skipped = total_items - items_processed
        rows = [
            ("Mode", "json"),
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Items", str(total_items)),
            ("Items processed", str(items_processed)),
            ("Items skipped", str(items_skipped)),
            ("Items updated", str(items_updated)),
            ("Fields processed", str(fields_total)),
            ("Fields updated", str(fields_updated)),
            ("Fix level", fix_level),
            ("Format", "no" if no_format else "yes"),
            ("In place", "yes" if in_place else "no"),
            ("Output dir", _relative_path(output_path) if output_path else "-"),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ]
    else:
        rows = [
            ("Mode", "markdown"),
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Fix level", fix_level),
            ("Format", "no" if no_format else "yes"),
            ("In place", "yes" if in_place else "no"),
            ("Output dir", _relative_path(output_path) if output_path else "-"),
            ("Duration", _format_duration(time.monotonic() - start_time)),
        ]
    _print_summary("recognize fix", rows)
