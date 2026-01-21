"""CLI commands for markdown translation."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any

import click
import coloredlogs
import httpx
from tqdm import tqdm
from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.config import ProviderConfig, load_config, resolve_api_keys
from deepresearch_flow.paper.extract import parse_model_ref
from deepresearch_flow.paper.utils import (
    discover_markdown,
    estimate_tokens,
    read_text,
    short_hash,
)
from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.engine import DumpSnapshot, MarkdownTranslator, RequestThrottle


logger = logging.getLogger(__name__)


def configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {remainder:.1f}s"


def _language_suffix(target_lang: str) -> str:
    lang = (target_lang or "").lower()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith(("ja", "jp")):
        return "ja"
    return lang or "out"


def _unique_output_name(path: Path, suffix: str, used: set[str]) -> str:
    base = path.stem
    filename = f"{base}.{suffix}.md"
    if filename not in used:
        used.add(filename)
        return filename
    suffix_hash = short_hash(str(path))
    filename = f"{base}.{suffix}.{suffix_hash}.md"
    used.add(filename)
    return filename


class ProgressTracker:
    def __init__(self, doc_total: int) -> None:
        self.doc_bar = tqdm(total=doc_total, desc="documents", unit="doc", position=0)
        self.group_bar = tqdm(total=0, desc="groups", unit="group", position=1, leave=False)
        self.lock = asyncio.Lock()

    async def add_groups(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.group_bar.total = (self.group_bar.total or 0) + count
            self.group_bar.refresh()

    async def advance_groups(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.group_bar.update(count)

    async def advance_docs(self, count: int = 1) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.doc_bar.update(count)

    async def set_group_status(self, text: str) -> None:
        async with self.lock:
            self.group_bar.set_postfix_str(text)
            self.group_bar.refresh()

    async def close(self) -> None:
        async with self.lock:
            self.group_bar.close()
            self.doc_bar.close()


@click.group()
def translator() -> None:
    """Translation workflows for OCR markdown."""


@translator.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown file or directory (repeatable)",
)
@click.option("--count", "count_limit", default=None, type=int, help="Translate up to N files")
@click.option("-g", "--glob", "glob_pattern", default=None, help="Glob filter when input is a directory")
@click.option("-m", "--model", "model_ref", required=True, help="provider/model")
@click.option("--source-lang", "source_lang", default=None, help="Source language hint")
@click.option("--target-lang", "target_lang", default="zh", show_default=True, help="Target language")
@click.option("--output-dir", "output_dir", default=None, help="Directory for translated markdown outputs")
@click.option("--fix-level", "fix_level", default="moderate", type=click.Choice(["off", "moderate", "aggressive"]))
@click.option("--max-chunk-chars", "max_chunk_chars", default=4000, show_default=True, type=int)
@click.option("--max-concurrency", "max_concurrency", default=4, show_default=True, type=int)
@click.option(
    "--group-concurrency",
    "group_concurrency",
    default=1,
    show_default=True,
    type=int,
    help="Concurrent translation groups per document",
)
@click.option("--timeout", "timeout", default=120.0, show_default=True, type=float)
@click.option("--retry-times", "retry_times", default=3, show_default=True, type=int)
@click.option("--fallback-model", "fallback_model_ref", default=None, help="Fallback provider/model")
@click.option(
    "--fallback-model-2",
    "fallback_model_ref_2",
    default=None,
    help="Second fallback provider/model",
)
@click.option(
    "--fallback-retry-times",
    "fallback_retry_times",
    default=None,
    type=int,
    help="Retry rounds for fallback model",
)
@click.option(
    "--fallback-retry-times-2",
    "fallback_retry_times_2",
    default=None,
    type=int,
    help="Retry rounds for second fallback model",
)
@click.option("--sleep-every", "sleep_every", default=None, type=int, help="Sleep after every N requests")
@click.option("--sleep-time", "sleep_time", default=None, type=float, help="Sleep duration in seconds")
@click.option("--debug-dir", "debug_dir", default=None, help="Directory for debug outputs")
@click.option("--dump-protected", "dump_protected", is_flag=True, help="Write protected markdown")
@click.option("--dump-placeholders", "dump_placeholders", is_flag=True, help="Write placeholder mapping JSON")
@click.option("--dump-nodes", "dump_nodes", is_flag=True, help="Write per-node translation JSON")
@click.option(
    "--dump-requests-log",
    "dump_requests_log",
    is_flag=True,
    help="Write request/response attempts to JSON log",
)
@click.option("--no-format", "no_format", is_flag=True, help="Disable rumdl formatting")
@click.option("--dry-run", "dry_run", is_flag=True, help="Discover inputs without calling providers")
@click.option("--force", "force", is_flag=True, help="Overwrite existing outputs")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def translate(
    config_path: str,
    inputs: tuple[str, ...],
    count_limit: int | None,
    glob_pattern: str | None,
    model_ref: str,
    source_lang: str | None,
    target_lang: str,
    output_dir: str | None,
    fix_level: str,
    max_chunk_chars: int,
    max_concurrency: int,
    group_concurrency: int,
    timeout: float,
    retry_times: int,
    fallback_model_ref: str | None,
    fallback_model_ref_2: str | None,
    fallback_retry_times: int | None,
    fallback_retry_times_2: int | None,
    sleep_every: int | None,
    sleep_time: float | None,
    debug_dir: str | None,
    dump_protected: bool,
    dump_placeholders: bool,
    dump_nodes: bool,
    dump_requests_log: bool,
    no_format: bool,
    dry_run: bool,
    force: bool,
    verbose: bool,
) -> None:
    """Translate OCR markdown while preserving structure."""
    configure_logging(verbose)
    config = load_config(config_path)
    provider, model_name = parse_model_ref(model_ref, config.providers)
    if provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    }:
        if not resolve_api_keys(provider.api_keys):
            raise click.ClickException(f"{provider.type} providers require api_keys")
    fallback_provider: ProviderConfig | None = None
    fallback_model_name: str | None = None
    if fallback_model_ref:
        fallback_provider, fallback_model_name = parse_model_ref(
            fallback_model_ref, config.providers
        )
        if fallback_provider.type in {
            "openai_compatible",
            "dashscope",
            "gemini_ai_studio",
            "azure_openai",
            "claude",
        }:
            if not resolve_api_keys(fallback_provider.api_keys):
                raise click.ClickException(
                    f"{fallback_provider.type} fallback providers require api_keys"
                )
    fallback_provider_2: ProviderConfig | None = None
    fallback_model_name_2: str | None = None
    if fallback_model_ref_2:
        fallback_provider_2, fallback_model_name_2 = parse_model_ref(
            fallback_model_ref_2, config.providers
        )
        if fallback_provider_2.type in {
            "openai_compatible",
            "dashscope",
            "gemini_ai_studio",
            "azure_openai",
            "claude",
        }:
            if not resolve_api_keys(fallback_provider_2.api_keys):
                raise click.ClickException(
                    f"{fallback_provider_2.type} fallback providers require api_keys"
                )

    if max_chunk_chars <= 0:
        raise click.ClickException("--max-chunk-chars must be positive")
    if max_concurrency <= 0:
        raise click.ClickException("--max-concurrency must be positive")
    if group_concurrency <= 0:
        raise click.ClickException("--group-concurrency must be positive")
    if timeout <= 0:
        raise click.ClickException("--timeout must be positive")
    if retry_times <= 0:
        raise click.ClickException("--retry-times must be positive")
    if count_limit is not None and count_limit <= 0:
        raise click.ClickException("--count must be positive")
    if fallback_retry_times is not None and fallback_retry_times <= 0:
        raise click.ClickException("--fallback-retry-times must be positive")
    if fallback_retry_times_2 is not None and fallback_retry_times_2 <= 0:
        raise click.ClickException("--fallback-retry-times-2 must be positive")
    if (sleep_every is None) != (sleep_time is None):
        raise click.ClickException("Both --sleep-every and --sleep-time are required")

    markdown_files = discover_markdown(inputs, glob_pattern)
    if not markdown_files:
        raise click.ClickException("No markdown files discovered")
    if count_limit is not None and dry_run:
        markdown_files = markdown_files[:count_limit]

    start_time = time.monotonic()
    input_chars = 0
    for path in markdown_files:
        input_chars += len(read_text(path))

    if dry_run:
        duration = time.monotonic() - start_time
        table = Table(
            title="translator translate summary (dry-run)",
            header_style="bold cyan",
            title_style="bold magenta",
        )
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white", overflow="fold")
        table.add_row("Documents", str(len(markdown_files)))
        if count_limit is not None:
            table.add_row("Limit", str(count_limit))
        table.add_row("Duration", _format_duration(duration))
        table.add_row("Input chars", str(input_chars))
        table.add_row("Est tokens", str(estimate_tokens(input_chars)))
        Console().print(table)
        return

    suffix = _language_suffix(target_lang)
    output_root = Path(output_dir) if output_dir else None
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)

    debug_root = Path(debug_dir) if debug_dir else None
    if debug_root is None and (
        dump_protected or dump_placeholders or dump_nodes or dump_requests_log
    ):
        debug_root = output_root or Path.cwd()
    if debug_root is not None:
        debug_root.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    output_map: dict[Path, Path] = {}
    for path in markdown_files:
        if output_root is None:
            output_map[path] = path.with_name(f"{path.stem}.{suffix}.md")
        else:
            output_name = _unique_output_name(path, suffix, used_names)
            output_map[path] = output_root / output_name

    to_process: list[Path] = []
    skipped = 0
    for path in markdown_files:
        output_path = output_map[path]
        if output_path.exists() and not force:
            skipped += 1
            logger.info("Skip existing output: %s", output_path)
            continue
        to_process.append(path)
    if count_limit is not None:
        to_process = to_process[:count_limit]

    if not to_process:
        table = Table(
            title="translator translate summary",
            header_style="bold cyan",
            title_style="bold magenta",
        )
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white", overflow="fold")
        table.add_row("Documents", str(len(markdown_files)))
        table.add_row("Skipped", str(skipped))
        table.add_row("Processed", "0")
        if count_limit is not None:
            table.add_row("Limit", str(count_limit))
        Console().print(table)
        return
    cfg = TranslateConfig(
        source_lang=source_lang,
        target_lang=target_lang,
        max_chunk_chars=max_chunk_chars,
        retry_times=retry_times,
    )
    translator = MarkdownTranslator(cfg)
    semaphore = asyncio.Semaphore(max_concurrency)

    throttle = None
    if sleep_every is not None or sleep_time is not None:
        if not sleep_every or not sleep_time:
            raise click.ClickException("--sleep-every and --sleep-time must be set together")
        throttle = RequestThrottle(int(sleep_every), float(sleep_time))

    max_tokens = provider.max_tokens if provider.type == "claude" else None
    fallback_max_tokens = (
        fallback_provider.max_tokens if fallback_provider and fallback_provider.type == "claude" else None
    )
    fallback_max_tokens_2 = (
        fallback_provider_2.max_tokens
        if fallback_provider_2 and fallback_provider_2.type == "claude"
        else None
    )

    async def process_one(
        path: Path,
        client: httpx.AsyncClient,
        progress: ProgressTracker,
    ) -> None:
        content = read_text(path)
        request_log: list[dict[str, Any]] = []
        debug_tag = None
        protected_path = None
        placeholders_path = None
        nodes_path = None
        requests_path = None
        if debug_root is not None:
            debug_tag = f"{path.stem}.{short_hash(str(path))}"
            protected_path = debug_root / f"{debug_tag}.protected.md"
            placeholders_path = debug_root / f"{debug_tag}.placeholders.json"
            nodes_path = debug_root / f"{debug_tag}.nodes.json"
            requests_path = debug_root / f"{debug_tag}.requests.json"

        def write_dump(snapshot: DumpSnapshot) -> None:
            if debug_root is None or debug_tag is None:
                return
            if dump_protected and snapshot.protected_text is not None and protected_path:
                protected_path.write_text(snapshot.protected_text, encoding="utf-8")
            if dump_placeholders and snapshot.placeholder_store is not None and placeholders_path:
                snapshot.placeholder_store.save(str(placeholders_path))
            if dump_nodes and snapshot.nodes is not None and nodes_path:
                node_payload = {
                    str(node_id): {
                        "origin_text": node.origin_text,
                        "translated_text": node.translated_text,
                    }
                    for node_id, node in snapshot.nodes.items()
                }
                nodes_path.write_text(
                    json.dumps(node_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if dump_requests_log and snapshot.request_log is not None and requests_path:
                requests_path.write_text(
                    json.dumps(snapshot.request_log, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        result = await translator.translate(
            content,
            provider,
            model_name,
            client,
            provider.api_keys,
            timeout,
            semaphore,
            throttle,
            max_tokens,
            fix_level,
            progress=progress,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model_name,
            fallback_max_tokens=fallback_max_tokens,
            fallback_provider_2=fallback_provider_2,
            fallback_model_2=fallback_model_name_2,
            fallback_max_tokens_2=fallback_max_tokens_2,
            fallback_retry_times=fallback_retry_times,
            fallback_retry_times_2=fallback_retry_times_2,
            format_enabled=not no_format,
            request_log=request_log if dump_requests_log else None,
            dump_callback=write_dump if debug_root is not None else None,
            group_concurrency=group_concurrency,
        )
        output_path = output_map[path]
        output_path.write_text(result.translated_text, encoding="utf-8")
        stats = result.stats
        logger.info(
            "Translated %s | nodes=%d ok=%d fail=%d skip=%d groups=%d retries=%d",
            path.name,
            stats.total_nodes,
            stats.success_nodes,
            stats.failed_nodes,
            stats.skipped_nodes,
            stats.initial_groups,
            stats.retry_groups,
        )
        await progress.set_group_status(
            f"nodes {stats.total_nodes} ok {stats.success_nodes} "
            f"fail {stats.failed_nodes} skip {stats.skipped_nodes}"
        )

        if debug_root is not None:
            write_dump(
                DumpSnapshot(
                    stage="final",
                    nodes=result.nodes,
                    protected_text=result.protected_text,
                    placeholder_store=result.placeholder_store,
                    request_log=request_log if dump_requests_log else None,
                )
            )
        await progress.advance_docs(1)

    async def run() -> None:
        progress = ProgressTracker(len(to_process))
        try:
            async with httpx.AsyncClient() as client:
                for path in to_process:
                    await process_one(path, client, progress)
        finally:
            await progress.close()

    asyncio.run(run())

    duration = time.monotonic() - start_time
    table = Table(
        title="translator translate summary",
        header_style="bold cyan",
        title_style="bold magenta",
    )
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold")
    table.add_row("Documents", str(len(markdown_files)))
    table.add_row("Skipped", str(skipped))
    table.add_row("Processed", str(len(to_process)))
    if count_limit is not None:
        table.add_row("Limit", str(count_limit))
    table.add_row("Duration", _format_duration(duration))
    table.add_row("Output suffix", f".{suffix}.md")
    Console().print(table)
