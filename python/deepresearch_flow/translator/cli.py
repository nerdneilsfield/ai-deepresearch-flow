"""CLI commands for markdown translation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from pathlib import Path
import time
from typing import Any

import click
import coloredlogs
import httpx
from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.config import ProviderConfig, load_config, resolve_api_keys
from deepresearch_flow.paper.routing import (
    parse_model_selector,
    provider_window_error_as_click,
    RoutePool,
    select_runtime_route,
)
from deepresearch_flow.paper.utils import (
    discover_markdown,
    estimate_tokens,
    read_text,
    short_hash,
)
from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.engine import MarkdownTranslator, RequestThrottle


logger = logging.getLogger(__name__)


_QUIET_LOGGERS = (
    "httpx",
    "httpx._client",
    "httpx._transports",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
)


def configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")
    # httpx/httpcore DEBUG logs overwhelm translator progress output and
    # duplicate request timing that we already report ourselves.
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


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


def _select_markdown_range(
    markdown_files: list[Path],
    *,
    start_index: int,
    end_index: int | None,
) -> tuple[list[Path], int, int]:
    if start_index > len(markdown_files):
        raise click.ClickException(
            f"--start-index {start_index} exceeds discovered markdown count {len(markdown_files)}"
        )
    effective_end = len(markdown_files) if end_index is None else min(end_index, len(markdown_files))
    selected = markdown_files[start_index - 1 : effective_end]
    return selected, start_index, effective_end


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
    required=False,
    help="Input markdown file or directory (repeatable)",
)
@click.option(
    "--input-list",
    "input_list",
    default=None,
    help="Text file containing markdown file paths (one per line)",
)
@click.option("--count", "count_limit", default=None, type=int, help="Translate up to N files")
@click.option("--start-index", "start_index", default=1, show_default=True, type=int, help="1-based inclusive start index in discovered markdown order")
@click.option("--end-index", "end_index", default=None, type=int, help="1-based inclusive end index in discovered markdown order (default: last discovered file)")
@click.option("-g", "--glob", "glob_pattern", default=None, help="Glob filter when input is a directory")
@click.option("-m", "--model", "model_ref", required=False, help="provider/model")
@click.option("--source-lang", "source_lang", default=None, help="Source language hint")
@click.option("--target-lang", "target_lang", default="zh", show_default=True, help="Target language")
@click.option("--output-dir", "output_dir", default=None, help="Directory for translated markdown outputs")
@click.option("--fix-level", "fix_level", default="moderate", type=click.Choice(["off", "moderate", "aggressive"]))
@click.option("--max-chunk-chars", "max_chunk_chars", default=4000, show_default=True, type=int)
@click.option("--max-concurrency", "max_concurrency", default=4, show_default=True, type=int)
@click.option(
    "--group-concurrency",
    "group_concurrency",
    default=None,
    type=int,
    help="[DEPRECATED: use --initial-workers] Concurrent translation groups per document",
)
@click.option(
    "--document-window",
    "document_window",
    default=None,
    type=int,
    help="Max documents simultaneously in-flight (default: all)",
)
@click.option(
    "--initial-workers",
    "initial_workers",
    default=None,
    type=int,
    help="Worker count for initial translation queue",
)
@click.option(
    "--retry-workers",
    "retry_workers",
    default=None,
    type=int,
    help="Worker count for retry queue",
)
@click.option(
    "--fallback-workers",
    "fallback_workers",
    default=2,
    show_default=True,
    type=int,
    help="Worker count for fallback queue",
)
@click.option(
    "--fallback-2-workers",
    "fallback_2_workers",
    default=2,
    show_default=True,
    type=int,
    help="Worker count for fallback_2 queue",
)
@click.option(
    "--main-concurrency",
    "main_concurrency",
    default=None,
    type=int,
    help="Provider-level concurrency for main model",
)
@click.option(
    "--retry-concurrency",
    "retry_concurrency_val",
    default=None,
    type=int,
    help="Provider-level concurrency for retry model",
)
@click.option(
    "--fallback-concurrency",
    "fallback_concurrency_val",
    default=None,
    type=int,
    help="Provider-level concurrency for fallback model",
)
@click.option(
    "--fallback-2-concurrency",
    "fallback_2_concurrency_val",
    default=None,
    type=int,
    help="Provider-level concurrency for fallback_2 model",
)
@click.option("--timeout", "timeout", default=120.0, show_default=True, type=float)
@click.option("--retry-times", "retry_times", default=3, show_default=True, type=int)
@click.option(
    "--retry-model",
    "retry_model_ref",
    default=None,
    help="Retry provider/model or JSON model pool",
)
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
    input_list: str | None,
    count_limit: int | None,
    start_index: int,
    end_index: int | None,
    glob_pattern: str | None,
    model_ref: str,
    source_lang: str | None,
    target_lang: str,
    output_dir: str | None,
    fix_level: str,
    max_chunk_chars: int,
    max_concurrency: int,
    group_concurrency: int | None,
    document_window: int | None,
    initial_workers: int | None,
    retry_workers: int | None,
    fallback_workers: int,
    fallback_2_workers: int,
    main_concurrency: int | None,
    retry_concurrency_val: int | None,
    fallback_concurrency_val: int | None,
    fallback_2_concurrency_val: int | None,
    timeout: float,
    retry_times: int,
    retry_model_ref: str | None,
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
    all_inputs = list(inputs)
    if input_list:
        list_path = Path(input_list)
        if not list_path.exists():
            raise click.ClickException(f"Input list file not found: {input_list}")
        list_content = list_path.read_text(encoding="utf-8")
        list_items = [line.strip() for line in list_content.splitlines() if line.strip()]

        base_dir = None
        for inp in inputs:
            path = Path(inp)
            if path.is_dir():
                base_dir = path
                break

        for item in list_items:
            item_path = Path(item)
            if not item_path.is_absolute() and base_dir:
                item_path = base_dir / item_path
            all_inputs.append(str(item_path))

    if not all_inputs:
        raise click.ClickException("At least one --input or --input-list is required")

    config = load_config(config_path)
    translator_defaults = config.translator_config
    if model_ref is None and translator_defaults is not None:
        model_ref = translator_defaults.model
    if retry_model_ref is None and translator_defaults is not None:
        retry_model_ref = translator_defaults.retry_model
    if fallback_model_ref is None and translator_defaults is not None:
        fallback_model_ref = translator_defaults.fallback_model
    if fallback_model_ref_2 is None and translator_defaults is not None:
        fallback_model_ref_2 = translator_defaults.fallback_model_2
    if group_concurrency is not None:
        if initial_workers is None:
            click.echo(
                "Warning: --group-concurrency is deprecated, use --initial-workers",
                err=True,
            )
            initial_workers = group_concurrency
        else:
            click.echo(
                "Warning: --group-concurrency ignored because --initial-workers is set",
                err=True,
            )
    if translator_defaults is not None:
        if document_window is None:
            document_window = translator_defaults.document_window
        if initial_workers is None:
            initial_workers = translator_defaults.initial_workers
        if retry_workers is None:
            retry_workers = translator_defaults.retry_workers
        if main_concurrency is None:
            main_concurrency = translator_defaults.main_concurrency
        if retry_concurrency_val is None:
            retry_concurrency_val = translator_defaults.retry_concurrency
        if fallback_concurrency_val is None:
            fallback_concurrency_val = translator_defaults.fallback_concurrency
        if fallback_2_concurrency_val is None:
            fallback_2_concurrency_val = translator_defaults.fallback_2_concurrency
        if fallback_workers == 2 and translator_defaults.fallback_workers is not None:
            fallback_workers = translator_defaults.fallback_workers
        if fallback_2_workers == 2 and translator_defaults.fallback_2_workers is not None:
            fallback_2_workers = translator_defaults.fallback_2_workers
    if retry_concurrency_val is not None and not retry_model_ref:
        raise click.ClickException("retry_concurrency requires retry_model")
    if not model_ref:
        raise click.ClickException("--model is required unless [translator_config].model is set")
    try:
        selector = parse_model_selector(model_ref, config)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    with provider_window_error_as_click():
        route = select_runtime_route(config, selector)
    provider = replace(route.provider, base=[route.base], models=[route.model])
    model_name = route.model.model_name
    route_pool = RoutePool.from_selector(config, selector, cooldown_seconds=1.0, verbose=verbose)
    if provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    }:
        if not resolve_api_keys(provider.api_keys):
            raise click.ClickException(f"{provider.type} providers require api_keys")
    retry_provider = provider
    retry_model_name = model_name
    retry_route_pool = route_pool
    if retry_model_ref:
        try:
            retry_selector = parse_model_selector(retry_model_ref, config)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        with provider_window_error_as_click():
            retry_route = select_runtime_route(config, retry_selector)
        retry_provider = replace(
            retry_route.provider,
            base=[retry_route.base],
            models=[retry_route.model],
        )
        retry_model_name = retry_route.model.model_name
        retry_route_pool = RoutePool.from_selector(
            config,
            retry_selector,
            cooldown_seconds=1.0,
            verbose=verbose,
        )
        if retry_provider.type in {
            "openai_compatible",
            "dashscope",
            "gemini_ai_studio",
            "azure_openai",
            "claude",
        }:
            if not resolve_api_keys(retry_provider.api_keys):
                raise click.ClickException(
                    f"{retry_provider.type} retry providers require api_keys"
                )
    fallback_provider: ProviderConfig | None = None
    fallback_model_name: str | None = None
    fallback_route_pool: RoutePool | None = None
    if fallback_model_ref:
        try:
            fallback_selector = parse_model_selector(fallback_model_ref, config)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        with provider_window_error_as_click():
            fallback_route = select_runtime_route(config, fallback_selector)
        fallback_provider = replace(
            fallback_route.provider,
            base=[fallback_route.base],
            models=[fallback_route.model],
        )
        fallback_model_name = fallback_route.model.model_name
        fallback_route_pool = RoutePool.from_selector(
            config,
            fallback_selector,
            cooldown_seconds=1.0,
            verbose=verbose,
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
    fallback_route_pool_2: RoutePool | None = None
    if fallback_model_ref_2:
        try:
            fallback_selector_2 = parse_model_selector(fallback_model_ref_2, config)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        with provider_window_error_as_click():
            fallback_route_2 = select_runtime_route(config, fallback_selector_2)
        fallback_provider_2 = replace(
            fallback_route_2.provider,
            base=[fallback_route_2.base],
            models=[fallback_route_2.model],
        )
        fallback_model_name_2 = fallback_route_2.model.model_name
        fallback_route_pool_2 = RoutePool.from_selector(
            config,
            fallback_selector_2,
            cooldown_seconds=1.0,
            verbose=verbose,
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
    if initial_workers is None:
        initial_workers = 1
    if retry_workers is None:
        retry_workers = max(initial_workers // 4, 1)
    if initial_workers <= 0:
        raise click.ClickException("--initial-workers must be positive")
    if retry_workers <= 0:
        raise click.ClickException("--retry-workers must be positive")
    if fallback_workers <= 0:
        raise click.ClickException("--fallback-workers must be positive")
    if fallback_2_workers <= 0:
        raise click.ClickException("--fallback-2-workers must be positive")
    if document_window is not None and document_window <= 0:
        raise click.ClickException("--document-window must be positive")
    if main_concurrency is not None and main_concurrency <= 0:
        raise click.ClickException("--main-concurrency must be positive")
    if retry_concurrency_val is not None and retry_concurrency_val <= 0:
        raise click.ClickException("--retry-concurrency must be positive")
    if fallback_concurrency_val is not None and fallback_concurrency_val <= 0:
        raise click.ClickException("--fallback-concurrency must be positive")
    if fallback_2_concurrency_val is not None and fallback_2_concurrency_val <= 0:
        raise click.ClickException("--fallback-2-concurrency must be positive")
    if timeout <= 0:
        raise click.ClickException("--timeout must be positive")
    if retry_times <= 0:
        raise click.ClickException("--retry-times must be positive")
    if count_limit is not None and count_limit <= 0:
        raise click.ClickException("--count must be positive")
    if start_index <= 0:
        raise click.ClickException("--start-index must be positive")
    if end_index is not None and end_index <= 0:
        raise click.ClickException("--end-index must be positive")
    if end_index is not None and end_index < start_index:
        raise click.ClickException("--end-index must be greater than or equal to --start-index")
    if fallback_retry_times is not None and fallback_retry_times <= 0:
        raise click.ClickException("--fallback-retry-times must be positive")
    if fallback_retry_times_2 is not None and fallback_retry_times_2 <= 0:
        raise click.ClickException("--fallback-retry-times-2 must be positive")
    if (sleep_every is None) != (sleep_time is None):
        raise click.ClickException("Both --sleep-every and --sleep-time are required")

    markdown_files = discover_markdown(tuple(all_inputs), glob_pattern)
    if not markdown_files:
        raise click.ClickException("No markdown files discovered")
    markdown_files, effective_start_index, effective_end_index = _select_markdown_range(
        markdown_files,
        start_index=start_index,
        end_index=end_index,
    )
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
        if start_index != 1 or end_index is not None:
            table.add_row("Index range", f"{effective_start_index}-{effective_end_index}")
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
        if start_index != 1 or end_index is not None:
            table.add_row("Index range", f"{effective_start_index}-{effective_end_index}")
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

    throttle = None
    if sleep_every is not None or sleep_time is not None:
        if not sleep_every or not sleep_time:
            raise click.ClickException("--sleep-every and --sleep-time must be set together")
        throttle = RequestThrottle(int(sleep_every), float(sleep_time))

    max_tokens = provider.max_tokens if provider.type == "claude" else None
    retry_max_tokens = retry_provider.max_tokens if retry_provider.type == "claude" else None
    fallback_max_tokens = (
        fallback_provider.max_tokens if fallback_provider and fallback_provider.type == "claude" else None
    )
    fallback_max_tokens_2 = (
        fallback_provider_2.max_tokens
        if fallback_provider_2 and fallback_provider_2.type == "claude"
        else None
    )
    failed_files: list[Path] = []

    async def run_scheduler() -> None:
        from deepresearch_flow.translator.progress import ProgressReporter
        from deepresearch_flow.translator.scheduler import DocStage, QueueConfig, Scheduler

        nonlocal_document_window = document_window if document_window is not None else len(to_process)
        global_sem = asyncio.Semaphore(max_concurrency)
        main_sem = asyncio.Semaphore(main_concurrency or max_concurrency)
        retry_sem = (
            asyncio.Semaphore(retry_concurrency_val)
            if retry_concurrency_val is not None
            else main_sem
        )
        fb_sem = asyncio.Semaphore(fallback_concurrency_val or max_concurrency)
        fb2_sem = asyncio.Semaphore(fallback_2_concurrency_val or max_concurrency)

        queue_configs: list[QueueConfig] = [
            QueueConfig(
                stage=DocStage.TRANSLATING,
                workers=initial_workers,
                provider_semaphore=main_sem,
                route_pool=route_pool,
                provider=provider,
                model=model_name,
                api_keys=provider.api_keys,
                max_tokens=max_tokens,
                retry_limit=max(retry_times, 1),
            ),
            QueueConfig(
                stage=DocStage.RETRYING,
                workers=retry_workers,
                provider_semaphore=retry_sem,
                route_pool=retry_route_pool,
                provider=retry_provider,
                model=retry_model_name,
                api_keys=retry_provider.api_keys,
                max_tokens=retry_max_tokens,
                retry_limit=max(retry_times, 1),
            ),
        ]
        if fallback_provider and fallback_model_name:
            queue_configs.append(
                QueueConfig(
                    stage=DocStage.FALLBACK_1,
                    workers=fallback_workers,
                    provider_semaphore=fb_sem,
                    route_pool=fallback_route_pool,
                    provider=fallback_provider,
                    model=fallback_model_name,
                    api_keys=fallback_provider.api_keys,
                    max_tokens=fallback_max_tokens,
                    retry_limit=fallback_retry_times or max(retry_times, 1),
                )
            )
        if fallback_provider_2 and fallback_model_name_2:
            queue_configs.append(
                QueueConfig(
                    stage=DocStage.FALLBACK_2,
                    workers=fallback_2_workers,
                    provider_semaphore=fb2_sem,
                    route_pool=fallback_route_pool_2,
                    provider=fallback_provider_2,
                    model=fallback_model_name_2,
                    api_keys=fallback_provider_2.api_keys,
                    max_tokens=fallback_max_tokens_2,
                    retry_limit=fallback_retry_times_2 or max(retry_times, 1),
                )
            )

        progress = ProgressReporter(len(to_process), [qc.stage.value for qc in queue_configs])
        try:
            async with httpx.AsyncClient() as client:
                scheduler = Scheduler(
                    translator=translator,
                    document_window=nonlocal_document_window,
                    global_semaphore=global_sem,
                    queue_configs=queue_configs,
                    progress=progress,
                    client=client,
                    throttle=throttle,
                    timeout=timeout,
                )
                failed_files[:] = await scheduler.run(
                    paths=to_process,
                    output_map=output_map,
                    fix_level=fix_level,
                    format_enabled=not no_format,
                    request_log_enabled=dump_requests_log,
                    debug_root=debug_root,
                    dump_protected=dump_protected,
                    dump_placeholders=dump_placeholders,
                    dump_nodes=dump_nodes,
                    dump_requests_log=dump_requests_log,
                )
        finally:
            await progress.close()
        for path in failed_files:
            click.echo(f"Failed {path}", err=True)

    with provider_window_error_as_click():
        asyncio.run(run_scheduler())

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
    table.add_row("Failed files", str(len(failed_files)))
    if start_index != 1 or end_index is not None:
        table.add_row("Index range", f"{effective_start_index}-{effective_end_index}")
    if count_limit is not None:
        table.add_row("Limit", str(count_limit))
    table.add_row("Duration", _format_duration(duration))
    table.add_row("Output suffix", f".{suffix}.md")
    Console().print(table)
