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

from deepresearch_flow.paper.config import load_config, resolve_api_keys
from deepresearch_flow.paper.extract import parse_model_ref
from deepresearch_flow.paper.template_registry import get_stage_definitions
from deepresearch_flow.paper.utils import discover_markdown
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
from deepresearch_flow.recognize.math import (
    MathFixStats,
    extract_math_spans,
    fix_math_text,
    locate_json_field_start,
    require_pylatexenc,
)
from deepresearch_flow.recognize.mermaid import (
    MermaidFixStats,
    extract_mermaid_spans,
    fix_mermaid_text,
    require_mmdc,
    extract_diagrams_from_text,
    repair_all_diagrams_global,
    DiagramTask,
    apply_replacements,
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
        item_count = sum(1 for _ in output_dir.iterdir())
        logger.warning("Output directory not empty: %s (items=%d)", output_dir, item_count)


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


RetryKey = tuple[int, str | None, int | None]


def _load_retry_targets(report_path: Path) -> dict[Path, set[RetryKey]]:
    if not report_path.exists():
        raise click.ClickException(f"Retry report not found: {report_path}")
    try:
        payload = json.loads(read_text(report_path))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Retry report is not valid JSON: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise click.ClickException(f"Retry report is empty: {report_path}")
    targets: dict[Path, set[RetryKey]] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        path_raw = entry.get("path")
        line_raw = entry.get("line")
        if not path_raw or line_raw is None:
            continue
        try:
            line_no = int(line_raw)
        except (TypeError, ValueError):
            continue
        field_path = entry.get("field_path")
        if not isinstance(field_path, str):
            field_path = None
        item_index = entry.get("item_index")
        if not isinstance(item_index, int):
            item_index = None
        key = (line_no, field_path, item_index)
        targets.setdefault(Path(path_raw).resolve(), set()).add(key)
    if not targets:
        raise click.ClickException(f"Retry report has no valid entries: {report_path}")
    return targets


def _filter_retry_spans(
    spans: list[Any],
    line_offset: int,
    field_path: str | None,
    item_index: int | None,
    retry_keys: set[RetryKey] | None,
) -> list[Any]:
    if not retry_keys:
        return spans
    filtered: list[Any] = []
    for span in spans:
        line_no = line_offset + span.line - 1
        if (line_no, field_path, item_index) in retry_keys:
            filtered.append(span)
    return filtered


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


async def _fix_json_items(
    items: list[Any],
    default_template: str | None,
    fix_level: str,
    format_enabled: bool,
    progress: tqdm | None = None,
    progress_lock: asyncio.Lock | None = None,
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
        if progress and progress_lock:
            async with progress_lock:
                progress.update(1)
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
            items,
            template_tag,
            fix_level,
            format_enabled,
            progress,
            progress_lock,
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
        json_inputs: list[str] = []
        md_inputs: list[str] = []
        for raw in inputs:
            path = Path(raw)
            if path.is_file():
                suffix = path.suffix.lower()
                if suffix == ".json":
                    json_inputs.append(raw)
                    continue
                if suffix == ".md":
                    md_inputs.append(raw)
                    continue
                raise click.ClickException(f"Input file must be .md or .json: {path}")
            if path.is_dir():
                json_inputs.append(raw)
                md_inputs.append(raw)
                continue
            raise click.ClickException(f"Input path not found: {path}")
        json_paths = discover_json(json_inputs, recursive=recursive) if json_inputs else []
        md_paths = discover_markdown(md_inputs, None, recursive=recursive) if md_inputs else []
        if json_paths and not md_paths:
            json_mode = True
            paths = json_paths
            click.echo("Detected JSON inputs; enabling --json mode")
        elif md_paths and not json_paths:
            paths = md_paths
        elif json_paths and md_paths:
            raise click.ClickException(
                "Found both markdown and JSON inputs; split inputs or pass --json explicitly"
            )
        else:
            paths = []
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

    progress_total = len(paths)
    progress_unit = "file"
    if json_mode:
        json_items_total = 0
        for path in paths:
            items, _, _ = _load_json_payload(path)
            json_items_total += sum(1 for item in items if isinstance(item, dict))
        progress_total = json_items_total
        progress_unit = "item"
    progress = tqdm(total=progress_total, desc="fix", unit=progress_unit)
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


@recognize.command("fix-math")
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown or JSON file/directory (repeatable)",
)
@click.option("-o", "--output", "output_dir", default=None, help="Output directory")
@click.option("--in-place", "in_place", is_flag=True, help="Fix formulas in place")
@click.option("-r", "--recursive", is_flag=True, help="Recursively discover files")
@click.option("--json", "json_mode", is_flag=True, help="Process JSON inputs instead of markdown")
@click.option("-m", "--model", "model_ref", required=True, help="provider/model")
@click.option("--batch-size", "batch_size", default=10, show_default=True, type=int)
@click.option("--context-chars", "context_chars", default=80, show_default=True, type=int)
@click.option("--max-retries", "max_retries", default=3, show_default=True, type=int)
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
@click.option("--timeout", "timeout", default=120.0, show_default=True, type=float)
@click.option("--retry-failed", "retry_failed", is_flag=True, help="Retry only failed formulas")
@click.option(
    "--only-show-error",
    "only_show_error",
    is_flag=True,
    help="Only validate formulas and report error counts",
)
@click.option("--report", "report_path", default=None, help="Error report output path")
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def recognize_fix_math(
    config_path: str,
    inputs: tuple[str, ...],
    output_dir: str | None,
    in_place: bool,
    recursive: bool,
    json_mode: bool,
    model_ref: str,
    batch_size: int,
    context_chars: int,
    max_retries: int,
    workers: int,
    timeout: float,
    retry_failed: bool,
    only_show_error: bool,
    report_path: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Validate and repair LaTeX formulas in markdown or JSON outputs."""
    configure_logging(verbose)
    if in_place and output_dir:
        raise click.ClickException("--in-place cannot be used with --output")
    if not only_show_error and not in_place and not output_dir:
        raise click.ClickException("Either --in-place or --output is required")
    if batch_size <= 0:
        raise click.ClickException("--batch-size must be positive")
    if context_chars < 0:
        raise click.ClickException("--context-chars must be non-negative")
    if max_retries < 0:
        raise click.ClickException("--max-retries must be non-negative")
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    if retry_failed and only_show_error:
        raise click.ClickException("--retry-failed cannot be used with --only-show-error")
    try:
        require_pylatexenc()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if not json_mode:
        file_types: set[str] = set()
        for raw in inputs:
            path = Path(raw)
            if path.is_file():
                suffix = path.suffix.lower()
                if suffix in {".md", ".json"}:
                    file_types.add(suffix)
        if ".md" in file_types and ".json" in file_types:
            raise click.ClickException(
                "Mixed markdown and JSON inputs. Use --json for JSON or split commands."
            )
        if ".json" in file_types:
            json_mode = True
            logger.info("Detected JSON inputs; enabling --json mode")

    config = load_config(config_path)
    provider, model_name = parse_model_ref(model_ref, config.providers)
    api_keys = resolve_api_keys(provider.api_keys)
    if provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    } and not api_keys:
        raise click.ClickException(f"{provider.type} providers require api_keys")
    api_key = api_keys[0] if api_keys else None

    if json_mode:
        paths = discover_json(inputs, recursive=recursive)
    else:
        paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No files discovered")
        return

    output_path = Path(output_dir) if output_dir else None
    report_target = None
    if report_path:
        report_target = Path(report_path)
    elif not only_show_error:
        if output_path:
            report_target = output_path / "fix-math-errors.json"
        elif in_place:
            report_target = Path.cwd() / "fix-math-errors.json"

    retry_targets: dict[Path, set[RetryKey]] | None = None
    if retry_failed:
        if report_target is None:
            raise click.ClickException("--retry-failed requires an error report path")
        retry_targets = _load_retry_targets(report_target)
        paths = [path for path in paths if path.resolve() in retry_targets]
        if not paths:
            raise click.ClickException("No failed formulas matched the provided inputs")

    if output_path and not dry_run and not only_show_error:
        output_path = _ensure_output_dir(output_dir)
        _warn_if_not_empty(output_path)

    if in_place:
        output_map = {path: path for path in paths}
    elif output_path:
        ext = ".json" if json_mode else ".md"
        output_map = {
            path: (output_path / name)
            for path, name in _map_output_files(paths, [output_path], ext=ext).items()
        }
    else:
        output_map = {path: path for path in paths}

    if dry_run and not only_show_error:
        rows = [
            ("Mode", "json" if json_mode else "markdown"),
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Batch size", str(batch_size)),
            ("Context chars", str(context_chars)),
            ("Max retries", str(max_retries)),
            ("Workers", str(workers)),
            ("Timeout", f"{timeout:.1f}s"),
            ("Retry failed", "yes" if retry_failed else "no"),
            ("Only show error", "yes" if only_show_error else "no"),
            ("In place", "yes" if in_place else "no"),
            ("Output dir", _relative_path(output_path) if output_path else "-"),
            ("Report", _relative_path(report_target) if report_target else "-"),
        ]
        _print_summary("recognize fix-math (dry-run)", rows)
        return

    progress = tqdm(total=len(paths), desc="fix-math", unit="file")
    formula_progress = tqdm(total=0, desc="formulas", unit="formula")
    error_records: list[dict[str, Any]] = []

    async def run() -> MathFixStats:
        semaphore = asyncio.Semaphore(workers)
        progress_lock = asyncio.Lock()
        stats_total = MathFixStats()

        async with httpx.AsyncClient() as client:
            async def handle_path(path: Path) -> MathFixStats:
                stats = MathFixStats()
                if json_mode:
                    raw_text = read_text(path)
                    items, payload, template_tag = _load_json_payload(path)
                    cursor = 0
                    for item_index, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        template = _resolve_item_template(item, template_tag)
                        fields = _template_markdown_fields(template)
                        for field in fields:
                            value = item.get(field)
                            if not isinstance(value, str):
                                continue
                            line_start, cursor = locate_json_field_start(raw_text, value, cursor)
                            field_path = f"papers[{item_index}].{field}"
                            spans = extract_math_spans(value, context_chars)
                            retry_keys = None
                            if retry_targets is not None:
                                retry_keys = retry_targets.get(path.resolve(), set())
                                retry_keys = {
                                    key
                                    for key in retry_keys
                                    if key[1] == field_path and key[2] == item_index
                                }
                            spans = _filter_retry_spans(
                                spans, line_start, field_path, item_index, retry_keys
                            )
                            if not spans:
                                continue
                            formula_progress.total += len(spans)
                            formula_progress.refresh()
                            updated, errors = await fix_math_text(
                                value,
                                str(path),
                                line_start,
                                field_path,
                                item_index,
                                provider,
                                model_name,
                                api_key,
                                timeout,
                                max_retries,
                                batch_size,
                                context_chars,
                                client,
                                stats,
                                repair_enabled=not only_show_error,
                                spans=spans,
                                allowed_keys=retry_keys,
                                progress_cb=lambda: formula_progress.update(1),
                            )
                            if not only_show_error and updated != value:
                                item[field] = updated
                            error_records.extend(errors)
                    if not only_show_error:
                        output_data: Any = items if payload is None else {**payload, "papers": items}
                        output_path = output_map[path]
                        serialized = json.dumps(output_data, ensure_ascii=False, indent=2)
                        await asyncio.to_thread(output_path.write_text, f"{serialized}\n", encoding="utf-8")
                else:
                    content = await asyncio.to_thread(read_text, path)
                    spans = extract_math_spans(content, context_chars)
                    retry_keys = None
                    if retry_targets is not None:
                        retry_keys = retry_targets.get(path.resolve(), set())
                        spans = _filter_retry_spans(spans, 1, None, None, retry_keys)
                        if not spans:
                            return stats
                    if spans:
                        formula_progress.total += len(spans)
                        formula_progress.refresh()
                    updated, errors = await fix_math_text(
                        content,
                        str(path),
                        1,
                        None,
                        None,
                        provider,
                        model_name,
                        api_key,
                        timeout,
                        max_retries,
                        batch_size,
                        context_chars,
                        client,
                        stats,
                        repair_enabled=not only_show_error,
                        spans=spans,
                        allowed_keys=retry_keys,
                        progress_cb=lambda: formula_progress.update(1),
                    )
                    if not only_show_error:
                        output_path = output_map[path]
                        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")
                    error_records.extend(errors)
                return stats

            async def runner(path: Path) -> None:
                async with semaphore:
                    stats = await handle_path(path)
                    stats_total.formulas_total += stats.formulas_total
                    stats_total.formulas_invalid += stats.formulas_invalid
                    stats_total.formulas_cleaned += stats.formulas_cleaned
                    stats_total.formulas_repaired += stats.formulas_repaired
                    stats_total.formulas_failed += stats.formulas_failed
                    async with progress_lock:
                        progress.update(1)

            await asyncio.gather(*(runner(path) for path in paths))
        return stats_total

    try:
        stats = asyncio.run(run())
    finally:
        progress.close()
        formula_progress.close()

    if report_target and error_records:
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(
            json.dumps(error_records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    rows = [
        ("Mode", "json" if json_mode else "markdown"),
        ("Inputs", str(len(paths))),
        ("Outputs", str(len(output_map) if not only_show_error else 0)),
        ("Formulas", str(stats.formulas_total)),
        ("Invalid", str(stats.formulas_invalid)),
        ("Cleaned", str(stats.formulas_cleaned)),
        ("Repaired", str(stats.formulas_repaired)),
        ("Failed", str(stats.formulas_failed)),
        ("Retry failed", "yes" if retry_failed else "no"),
        ("Only show error", "yes" if only_show_error else "no"),
        ("Report", _relative_path(report_target) if report_target else "-"),
    ]
    _print_summary("recognize fix-math", rows)


@recognize.command("fix-mermaid")
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown or JSON file/directory (repeatable)",
)
@click.option("-o", "--output", "output_dir", default=None, help="Output directory")
@click.option("--in-place", "in_place", is_flag=True, help="Fix Mermaid blocks in place")
@click.option("-r", "--recursive", is_flag=True, help="Recursively discover files")
@click.option("--json", "json_mode", is_flag=True, help="Process JSON inputs instead of markdown")
@click.option("-m", "--model", "model_ref", required=True, help="provider/model")
@click.option("--batch-size", "batch_size", default=10, show_default=True, type=int)
@click.option("--context-chars", "context_chars", default=80, show_default=True, type=int)
@click.option("--max-retries", "max_retries", default=3, show_default=True, type=int)
@click.option("--workers", type=int, default=4, show_default=True, help="Concurrent workers")
@click.option("--timeout", "timeout", default=120.0, show_default=True, type=float)
@click.option("--retry-failed", "retry_failed", is_flag=True, help="Retry only failed diagrams")
@click.option(
    "--only-show-error",
    "only_show_error",
    is_flag=True,
    help="Only validate Mermaid blocks and report error counts",
)
@click.option("--report", "report_path", default=None, help="Error report output path")
@click.option("--dry-run", is_flag=True, help="Report actions without writing files")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def recognize_fix_mermaid(
    config_path: str,
    inputs: tuple[str, ...],
    output_dir: str | None,
    in_place: bool,
    recursive: bool,
    json_mode: bool,
    model_ref: str,
    batch_size: int,
    context_chars: int,
    max_retries: int,
    workers: int,
    timeout: float,
    retry_failed: bool,
    only_show_error: bool,
    report_path: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Validate and repair Mermaid diagrams in markdown or JSON outputs."""
    configure_logging(verbose)
    if in_place and output_dir:
        raise click.ClickException("--in-place cannot be used with --output")
    if not only_show_error and not in_place and not output_dir:
        raise click.ClickException("Either --in-place or --output is required")
    if batch_size <= 0:
        raise click.ClickException("--batch-size must be positive")
    if context_chars < 0:
        raise click.ClickException("--context-chars must be non-negative")
    if max_retries < 0:
        raise click.ClickException("--max-retries must be non-negative")
    if workers <= 0:
        raise click.ClickException("--workers must be positive")
    if retry_failed and only_show_error:
        raise click.ClickException("--retry-failed cannot be used with --only-show-error")
    try:
        require_mmdc()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    if not json_mode:
        file_types: set[str] = set()
        for raw in inputs:
            path = Path(raw)
            if path.is_file():
                suffix = path.suffix.lower()
                if suffix in {".md", ".json"}:
                    file_types.add(suffix)
        if ".md" in file_types and ".json" in file_types:
            raise click.ClickException(
                "Mixed markdown and JSON inputs. Use --json for JSON or split commands."
            )
        if ".json" in file_types:
            json_mode = True
            logger.info("Detected JSON inputs; enabling --json mode")

    config = load_config(config_path)
    provider, model_name = parse_model_ref(model_ref, config.providers)
    api_keys = resolve_api_keys(provider.api_keys)
    if provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    } and not api_keys:
        raise click.ClickException(f"{provider.type} providers require api_keys")
    api_key = api_keys[0] if api_keys else None

    if json_mode:
        paths = discover_json(inputs, recursive=recursive)
    else:
        paths = discover_markdown(inputs, None, recursive=recursive)
    if not paths:
        click.echo("No files discovered")
        return

    output_path = Path(output_dir) if output_dir else None
    report_target = None
    if report_path:
        report_target = Path(report_path)
    elif not only_show_error:
        if output_path:
            report_target = output_path / "fix-mermaid-errors.json"
        elif in_place:
            report_target = Path.cwd() / "fix-mermaid-errors.json"

    retry_targets: dict[Path, set[RetryKey]] | None = None
    if retry_failed:
        if report_target is None:
            raise click.ClickException("--retry-failed requires an error report path")
        retry_targets = _load_retry_targets(report_target)
        paths = [path for path in paths if path.resolve() in retry_targets]
        if not paths:
            raise click.ClickException("No failed diagrams matched the provided inputs")

    if output_path and not dry_run and not only_show_error:
        output_path = _ensure_output_dir(output_dir)
        _warn_if_not_empty(output_path)

    if in_place:
        output_map = {path: path for path in paths}
    elif output_path:
        ext = ".json" if json_mode else ".md"
        output_map = {
            path: (output_path / name)
            for path, name in _map_output_files(paths, [output_path], ext=ext).items()
        }
    else:
        output_map = {path: path for path in paths}

    if dry_run and not only_show_error:
        rows = [
            ("Mode", "json" if json_mode else "markdown"),
            ("Inputs", str(len(paths))),
            ("Outputs", str(len(output_map))),
            ("Batch size", str(batch_size)),
            ("Context chars", str(context_chars)),
            ("Max retries", str(max_retries)),
            ("Workers", str(workers)),
            ("Timeout", f"{timeout:.1f}s"),
            ("Retry failed", "yes" if retry_failed else "no"),
            ("Only show error", "yes" if only_show_error else "no"),
            ("In place", "yes" if in_place else "no"),
            ("Output dir", _relative_path(output_path) if output_path else "-"),
            ("Report", _relative_path(report_target) if report_target else "-"),
        ]
        _print_summary("recognize fix-mermaid (dry-run)", rows)
        return

    progress = tqdm(total=len(paths), desc="extract", unit="file")
    field_progress = tqdm(total=0, desc="extract-field", unit="field", disable=not json_mode, leave=False)
    diagram_progress = tqdm(total=0, desc="repair", unit="diagram")
    error_records: list[dict[str, Any]] = []
    
    # Performance metrics
    extract_start_time = time.monotonic()
    repair_start_time = 0.0
    extract_duration = 0.0
    repair_duration = 0.0

    async def run() -> MermaidFixStats:
        stats_total = MermaidFixStats()

        async with httpx.AsyncClient() as client:
            # Phase 1: Extract all diagrams from all files in parallel (flatten to 1D)
            progress_lock = asyncio.Lock()
            field_progress_lock = asyncio.Lock()
            
            async def extract_from_file(path: Path) -> list[DiagramTask]:
                tasks: list[DiagramTask] = []
                
                if json_mode:
                    raw_text = await asyncio.to_thread(read_text, path)
                    items, payload, template_tag = _load_json_payload(path)
                    
                    logger.info("Extracting from JSON: %s (%d papers)", _relative_path(path), len(items))
                    
                    # Pre-calculate all field positions for parallel extraction
                    field_locations: list[tuple[int, str, str, str | None, int]] = []
                    cursor = 0
                    
                    for item_index, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        template = _resolve_item_template(item, template_tag)
                        fields = _template_markdown_fields(template)
                        
                        for field in fields:
                            value = item.get(field)
                            if not isinstance(value, str):
                                continue
                            line_start, cursor = locate_json_field_start(raw_text, value, cursor)
                            field_path = f"papers[{item_index}].{field}"
                            field_locations.append((line_start, value, field_path, None, item_index))
                    
                    logger.info("Pre-calculated %d field locations from %s", len(field_locations), _relative_path(path))
                    
                    # Apply retry filter to field locations if needed
                    if retry_targets is not None:
                        retry_keys = retry_targets.get(path.resolve(), set())
                        # Prefer filtering by (field_path, item_index) to avoid expensive validation / mmdc calls.
                        retry_fields = {
                            (field_path, item_index)
                            for _, field_path, item_index in retry_keys
                            if field_path is not None and item_index is not None
                        }
                        if retry_fields:
                            before = len(field_locations)
                            field_locations = [
                                loc for loc in field_locations if (loc[2], loc[4]) in retry_fields
                            ]
                            logger.info(
                                "Retry filter: %d/%d fields match (by field_path)",
                                len(field_locations),
                                before,
                            )
                        else:
                            # Fallback: filter by line numbers using fast span extraction (no validation).
                            filtered_locations: list[tuple[int, str, str, str | None, int]] = []
                            for line_start, value, field_path, _, item_index in field_locations:
                                spans = extract_mermaid_spans(value, context_chars)
                                if any(
                                    (line_start + span.line - 1, field_path, item_index) in retry_keys
                                    for span in spans
                                ):
                                    filtered_locations.append((line_start, value, field_path, None, item_index))
                            field_locations = filtered_locations
                            logger.info("Retry filter: %d fields match (by line)", len(field_locations))
                    
                    # Parallel extraction from all fields
                    async def extract_from_field(loc: tuple[int, str, str, str | None, int]) -> list[DiagramTask]:
                        line_start, value, field_path, _, item_index = loc
                        field_tasks = extract_diagrams_from_text(
                            value, path, line_start, field_path, item_index, context_chars,
                            skip_validation=not only_show_error  # Skip validation unless validating only
                        )
                        
                        # Apply retry filter to individual tasks
                        if retry_targets is not None:
                            retry_keys = retry_targets.get(path.resolve(), set())
                            field_tasks = [
                                task for task in field_tasks
                                if (task.file_line_offset + task.span.line - 1, task.field_path, task.item_index) in retry_keys
                            ]
                        
                        return field_tasks
                    
                    if field_locations:
                        logger.info("Extracting diagrams from %d fields in parallel...", len(field_locations))

                        async with field_progress_lock:
                            field_progress.total += len(field_locations)
                            field_progress.refresh()

                        # Bounded worker pool (avoid scheduling thousands of coroutines at once).
                        max_field_workers = 50
                        field_workers = min(max_field_workers, len(field_locations))
                        field_queue: asyncio.Queue[tuple[int, str, str, str | None, int] | None] = asyncio.Queue()
                        for loc in field_locations:
                            field_queue.put_nowait(loc)
                        for _ in range(field_workers):
                            field_queue.put_nowait(None)

                        async def field_worker() -> list[DiagramTask]:
                            out: list[DiagramTask] = []
                            while True:
                                loc = await field_queue.get()
                                if loc is None:
                                    break
                                out.extend(await extract_from_field(loc))
                                async with field_progress_lock:
                                    field_progress.update(1)
                            return out

                        worker_results = await asyncio.gather(*[field_worker() for _ in range(field_workers)])
                        for batch in worker_results:
                            tasks.extend(batch)
                    
                    logger.info("Extracted %d diagrams from %s", len(tasks), _relative_path(path))
                else:
                    content = await asyncio.to_thread(read_text, path)
                    
                    logger.info("Extracting from markdown: %s", _relative_path(path))
                    
                    # Extract diagrams from markdown
                    file_tasks = extract_diagrams_from_text(
                        content, path, 1, None, None, context_chars,
                        skip_validation=not only_show_error  # Skip validation unless validating only
                    )
                    
                    # Apply retry filter if needed
                    if retry_targets is not None:
                        retry_keys = retry_targets.get(path.resolve(), set())
                        file_tasks = [
                            task for task in file_tasks
                            if (task.file_line_offset + task.span.line - 1, task.field_path, task.item_index) in retry_keys
                        ]
                    
                    tasks.extend(file_tasks)
                    logger.info("Extracted %d diagrams from %s", len(tasks), _relative_path(path))
                
                async with progress_lock:
                    progress.update(1)
                return tasks
            
            # Parallel extraction with progress
            file_task_lists = await asyncio.gather(*[extract_from_file(path) for path in paths])
            all_tasks = [task for tasks in file_task_lists for task in tasks]
            
            progress.close()
            field_progress.close()
            nonlocal extract_duration, repair_start_time
            extract_duration = time.monotonic() - extract_start_time
            
            # Update diagram progress total
            diagram_progress.total = len(all_tasks)
            diagram_progress.refresh()
            
            if not all_tasks:
                return stats_total
            
            # Phase 2: Global parallel repair (flatten all batches)
            repair_start_time = time.monotonic()
            file_replacements, errors = await repair_all_diagrams_global(
                all_tasks,
                batch_size,
                workers,  # Use workers for global batch concurrency
                provider,
                model_name,
                api_key,
                timeout,
                max_retries,
                client,
                stats_total,
                progress_cb=lambda: diagram_progress.update(1) if not only_show_error else None,
            )
            
            error_records.extend(errors)
            diagram_progress.close()
            nonlocal repair_duration
            repair_duration = time.monotonic() - repair_start_time
            
            # Phase 3: Write back to files
            if not only_show_error:
                write_progress = tqdm(total=len(paths), desc="write", unit="file")
                
                for path in paths:
                    replacements = file_replacements.get(path, [])
                    output_path = output_map[path]
                    
                    if json_mode:
                        # For JSON, apply replacements to fields
                        raw_text = await asyncio.to_thread(read_text, path)
                        items, payload, template_tag = _load_json_payload(path)
                        cursor = 0
                        
                        for item_index, item in enumerate(items):
                            if not isinstance(item, dict):
                                continue
                            template = _resolve_item_template(item, template_tag)
                            fields = _template_markdown_fields(template)
                            
                            for field in fields:
                                value = item.get(field)
                                if not isinstance(value, str):
                                    continue
                                field_path = f"papers[{item_index}].{field}"
                                
                                # Find replacements for this specific field
                                field_replacements = [
                                    (start, end, repl)
                                    for start, end, repl in replacements
                                    if any(
                                        t.field_path == field_path and t.item_index == item_index and t.span.start == start
                                        for t in all_tasks
                                        if t.file_path == path
                                    )
                                ]
                                
                                if field_replacements:
                                    updated_value = apply_replacements(value, field_replacements)
                                    item[field] = updated_value
                        
                        output_data: Any = items if payload is None else {**payload, "papers": items}
                        serialized = json.dumps(output_data, ensure_ascii=False, indent=2)
                        await asyncio.to_thread(output_path.write_text, f"{serialized}\n", encoding="utf-8")
                    else:
                        # For markdown, apply replacements directly
                        content = await asyncio.to_thread(read_text, path)
                        updated = apply_replacements(content, replacements)
                        await asyncio.to_thread(output_path.write_text, updated, encoding="utf-8")
                    
                    write_progress.update(1)
                
                write_progress.close()
        
        return stats_total

    try:
        stats = asyncio.run(run())
    finally:
        progress.close()
        field_progress.close()
        diagram_progress.close()

    if report_target and error_records:
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(
            json.dumps(error_records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    rows = [
        ("Mode", "json" if json_mode else "markdown"),
        ("Inputs", str(len(paths))),
        ("Outputs", str(len(output_map) if not only_show_error else 0)),
        ("Diagrams", str(stats.diagrams_total)),
        ("Invalid", str(stats.diagrams_invalid)),
        ("Repaired", str(stats.diagrams_repaired)),
        ("Failed", str(stats.diagrams_failed)),
        ("Extract time", _format_duration(extract_duration)),
        ("Extract avg", f"{extract_duration / stats.diagrams_total:.3f}s/diagram" if stats.diagrams_total > 0 else "-"),
        ("Repair time", _format_duration(repair_duration)),
        ("Repair avg", f"{repair_duration / stats.diagrams_invalid:.3f}s/diagram" if stats.diagrams_invalid > 0 else "-"),
        ("Retry failed", "yes" if retry_failed else "no"),
        ("Only show error", "yes" if only_show_error else "no"),
        ("Report", _relative_path(report_target) if report_target else "-"),
    ]
    _print_summary("recognize fix-mermaid", rows)
