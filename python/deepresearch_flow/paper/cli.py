"""CLI commands for paper workflows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Generic, TypeVar

import click
from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.config import (
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    RerankModelConfig,
    RerankProviderConfig,
    load_config,
    resolve_api_keys,
)
from deepresearch_flow.paper.embedding import call_embedding_with_route_pool
from deepresearch_flow.paper.extract import extract_documents, configure_logging
from deepresearch_flow.paper.routing import (
    ParsedModelSelector,
    RoutePool,
    parse_model_selector,
    provider_window_error_as_click,
    resolve_model_capability,
)
from deepresearch_flow.paper.db import register_db_commands
from deepresearch_flow.paper.schema import load_schema, validate_schema, SchemaError
from deepresearch_flow.paper.template_registry import list_template_names, load_schema_for_template


@click.group()
def paper() -> None:
    """Paper extraction and database commands."""


ProviderT = TypeVar("ProviderT")
ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class _ResolvedRouteConfig(Generic[ProviderT, ModelT]):
    provider: ProviderT
    model: ModelT

    def resolve_active(self) -> tuple[ProviderT, ModelT]:
        return self.provider, self.model


def _resolve_provider_model_override(
    providers: list[EmbeddingProviderConfig] | list[RerankProviderConfig],
    model_ref: str,
    *,
    section_name: str,
) -> tuple[EmbeddingProviderConfig | RerankProviderConfig, EmbeddingModelConfig | RerankModelConfig]:
    if "/" not in model_ref:
        raise click.ClickException(f"--{section_name} must be in provider/model format")
    provider_name, model_name = model_ref.split("/", 1)
    provider = next((item for item in providers if item.name == provider_name), None)
    if provider is None:
        raise click.ClickException(f"{section_name} provider '{provider_name}' not found")
    model = next((item for item in provider.models if item.model_name == model_name), None)
    if model is None:
        raise click.ClickException(
            f"Model '{model_name}' not found in {section_name} provider '{provider_name}'"
        )
    return provider, model


async def _run_search(
    *,
    config,
    vector_dir: Path,
    query_text: str,
    top_n: int,
    year: int | None,
    venue: str | None,
    no_rerank: bool,
    no_hybrid: bool,
    verbose: bool = False,
    embedding_override: str | None = None,
    rerank_override: str | None = None,
) -> None:
    import httpx

    from deepresearch_flow.paper.reranker import RoutedReranker
    from deepresearch_flow.paper.search import (
        SearchProgress,
        hybrid_search,
        rank_keyword_rows,
        validate_venue_filter,
    )
    from deepresearch_flow.paper.vector_store import open_store, scan_rows

    if not config.embedding:
        raise click.ClickException("Config missing [embedding] section")

    if embedding_override:
        embedding_provider, embedding_model = _resolve_provider_model_override(
            config.embedding.providers,
            embedding_override,
            section_name="embedding",
        )
        embedding_config = _ResolvedRouteConfig(provider=embedding_provider, model=embedding_model)
    else:
        embedding_provider, embedding_model = config.embedding.resolve_active()
        embedding_config = config.embedding
    embedding_route_pool = RoutePool.from_embedding_provider(embedding_config, verbose=verbose)
    db = open_store(vector_dir)

    where_parts: list[str] = []
    if year is not None:
        where_parts.append(f"year = {year}")
    if venue:
        try:
            validated_venue = validate_venue_filter(venue)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        where_parts.append(f"venue = {json.dumps(validated_venue)}")
    where = " AND ".join(where_parts) if where_parts else None
    keyword_rows = scan_rows(db)
    if year is not None:
        keyword_rows = [row for row in keyword_rows if int(row.get("year") or 0) == year]
    if venue:
        try:
            venue_lower = validate_venue_filter(venue).lower()
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        keyword_rows = [
            row for row in keyword_rows if venue_lower in str(row.get("venue") or "").strip().lower()
        ]

    keyword_search_fn = None
    document_text_by_doc_id = {
        str(row.get("doc_id") or "").strip(): "\n".join(
            part
            for part in (
                str(row.get("title") or "").strip(),
                str(row.get("text") or "").strip(),
                str(row.get("authors") or "").strip(),
                str(row.get("venue") or "").strip(),
                str(row.get("tags") or "").strip(),
            )
            if part
        )
        for row in keyword_rows
        if str(row.get("doc_id") or "").strip()
    }
    if not no_hybrid and keyword_rows:
        keyword_search_fn = lambda q, limit=30: rank_keyword_rows(keyword_rows, q, limit=limit)

    reranker = None
    rerank_model_label: str | None = None
    if not no_rerank and config.rerank and config.rerank.enabled:
        if rerank_override:
            rerank_provider, rerank_model = _resolve_provider_model_override(
                config.rerank.providers,
                rerank_override,
                section_name="rerank",
            )
            rerank_config = _ResolvedRouteConfig(provider=rerank_provider, model=rerank_model)
        else:
            rerank_provider, rerank_model = config.rerank.resolve_active()
            rerank_config = config.rerank
        reranker = RoutedReranker(route_pool=RoutePool.from_rerank_provider(rerank_config, verbose=verbose))
        rerank_model_label = f"{rerank_provider.name}/{rerank_model.model_name}"

    if verbose:
        click.echo("Embedding query: starting.")

    async with httpx.AsyncClient() as client:
        embedding = await call_embedding_with_route_pool(
            route_pool=embedding_route_pool,
            texts=[query_text],
            dimensions=config.embedding.dimensions,
            client=client,
        )
        if verbose:
            click.echo("Embedding query: completed.")
            if rerank_model_label is not None:
                click.echo(f"Rerank: enabled with model {rerank_model_label}.")
            elif no_rerank:
                click.echo("Rerank: explicitly disabled.")
            else:
                click.echo("Rerank: not enabled.")
        progress = SearchProgress()
        results = await hybrid_search(
            query_vector=embedding.vectors[0],
            query_text=query_text,
            vector_store_db=db,
            keyword_search_fn=keyword_search_fn,
            reranker=reranker,
            vector_top_k=(config.search.vector_top_k if config.search else 50),
            keyword_top_k=(config.search.keyword_top_k if config.search else 30),
            rerank_top_n=top_n,
            hybrid=not no_hybrid and bool(config.search.hybrid if config.search else True),
            where=where,
            document_text_resolver=lambda doc_id: document_text_by_doc_id.get(doc_id, doc_id),
            client=client,
            progress=progress,
        )

    if verbose:
        click.echo(
            f"Vector retrieval: completed with {progress.vector_candidates} candidate documents."
        )
        if not no_hybrid and keyword_search_fn is not None:
            click.echo(
                f"Keyword retrieval: completed with {progress.keyword_candidates} candidate documents."
            )
            click.echo(
                f"Candidate fusion: completed with {progress.fused_candidates} candidate documents."
            )
        else:
            click.echo("Keyword retrieval: not enabled.")
        if progress.rerank_requested:
            if progress.rerank_applied:
                click.echo("Rerank: completed.")
            else:
                reason = progress.rerank_reason or "未知错误"
                click.echo(
                    f"Rerank: failed, falling back to the original ranking. Reason: {reason}"
                )
        click.echo(f"Search completed: returned {len(results)} results.")

    table = Table(title="paper search")
    table.add_column("doc_id", style="cyan")
    table.add_column("score", style="white")
    table.add_column("score_type", style="magenta")
    table.add_column("matched_field", style="green")
    for result in results:
        table.add_row(
            result.doc_id,
            f"{result.score:.4f}",
            result.score_type,
            result.matched_field,
        )
    Console().print(table)


@paper.command()
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
@click.option("-g", "--glob", "glob_pattern", default=None, help="Glob filter when input is a directory")
@click.option(
    "-s",
    "--schema-json",
    "--schema",
    "schema_path",
    default=None,
    help="Path to JSON schema",
)
@click.option("--prompt-system", "prompt_system", default=None, help="Custom system prompt template path")
@click.option("--prompt-user", "prompt_user", default=None, help="Custom user prompt template path")
@click.option(
    "--template-dir",
    "template_dir",
    default=None,
    help="Directory containing system.j2, user.j2, schema.json, render.j2",
)
@click.option(
    "--prompt-template",
    "prompt_template",
    default="simple",
    type=click.Choice(list_template_names()),
    show_default=True,
    help="Built-in prompt template",
)
@click.option(
    "--language",
    "output_language",
    default="en",
    show_default=True,
    help="Output language hint for prompts",
)
@click.option(
    "-m",
    "--model",
    "model_ref",
    required=False,
    default=None,
    help="provider/model, inline JSON model pool, or @file JSON model pool",
)
@click.option("-o", "--output", "output_path", default=None, help="Aggregated JSON output path")
@click.option("-e", "--errors", "errors_path", default=None, help="Error JSON output path")
@click.option("--split", is_flag=True, help="Write per-document JSON outputs")
@click.option("--split-dir", "split_dir", default=None, help="Directory for split outputs")
@click.option("--force", is_flag=True, help="Force re-extraction")
@click.option(
    "--force-stage",
    "force_stages",
    multiple=True,
    help="Force re-run specific stages (multi-stage templates only)",
)
@click.option("--retry-failed", is_flag=True, help="Retry only failed documents")
@click.option(
    "--retry-failed-stages",
    is_flag=True,
    help="Retry only failed stages per document (multi-stage templates only)",
)
@click.option(
    "--retry-list-json",
    "retry_list_json",
    default=None,
    help="Retry only documents listed in a verification report",
)
@click.option(
    "--stage-dag",
    is_flag=True,
    help="Enable dependency-aware DAG scheduling (multi-stage templates only)",
)
@click.option("--start-idx", "start_idx", type=int, default=0, help="Start index for inputs")
@click.option(
    "--end-idx",
    "end_idx",
    type=int,
    default=-1,
    help="End index (exclusive); -1 means to the last item",
)
@click.option("--dry-run", is_flag=True, help="Discover inputs without calling providers")
@click.option("--max-concurrency", "max_concurrency", type=int, default=None, help="Override max concurrency")
@click.option("--timeout", "timeout_seconds", type=float, default=None, help="Request timeout in seconds")
@click.option("--sleep-every", "sleep_every", type=int, default=None, help="Sleep after every N requests")
@click.option("--sleep-time", "sleep_time", type=float, default=None, help="Sleep duration in seconds")
@click.option("--render-md", "render_md", is_flag=True, help="Render markdown outputs after extraction")
@click.option(
    "--render-output-dir",
    "render_output_dir",
    default=None,
    help="Output directory for rendered markdown (defaults to --output parent when provided)",
)
@click.option(
    "--render-markdown-template",
    "--render-template",
    "render_template_path",
    default=None,
    help="Jinja2 template path for extract-time rendering",
)
@click.option(
    "--render-template-name",
    "render_template_name",
    default=None,
    type=click.Choice(list_template_names()),
    help="Built-in render template name",
)
@click.option(
    "--render-template-dir",
    "render_template_dir",
    default=None,
    help="Directory containing render.j2 for extract-time rendering",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def extract(
    config_path: str,
    inputs: tuple[str, ...],
    input_list: str | None,
    glob_pattern: str | None,
    schema_path: str | None,
    prompt_template: str,
    output_language: str,
    prompt_system: str | None,
    prompt_user: str | None,
    template_dir: str | None,
    model_ref: str | None,
    output_path: str | None,
    errors_path: str | None,
    split: bool,
    split_dir: str | None,
    force: bool,
    force_stages: tuple[str, ...],
    retry_failed: bool,
    retry_failed_stages: bool,
    retry_list_json: str | None,
    stage_dag: bool,
    start_idx: int,
    end_idx: int,
    dry_run: bool,
    max_concurrency: int | None,
    timeout_seconds: float | None,
    sleep_every: int | None,
    sleep_time: float | None,
    render_md: bool,
    render_output_dir: str,
    render_template_path: str | None,
    render_template_name: str | None,
    render_template_dir: str | None,
    verbose: bool,
) -> None:
    """Extract structured information from markdown documents."""
    # Process input_list if provided
    all_inputs = list(inputs)
    if input_list:
        list_path = Path(input_list)
        if not list_path.exists():
            raise click.ClickException(f"Input list file not found: {input_list}")
        list_content = list_path.read_text(encoding="utf-8")
        list_items = [line.strip() for line in list_content.splitlines() if line.strip()]
        
        # Find base directory from inputs if available
        base_dir = None
        for inp in inputs:
            path = Path(inp)
            if path.is_dir():
                base_dir = path
                break
        
        # Resolve relative paths against base_dir
        for item in list_items:
            item_path = Path(item)
            if not item_path.is_absolute() and base_dir:
                item_path = base_dir / item_path
            all_inputs.append(str(item_path))
    
    if not all_inputs:
        raise click.ClickException("At least one --input or --input-list is required")
    
    config = load_config(config_path)
    if model_ref is None:
        model_selector = ParsedModelSelector(
            kind="pool",
            fixed_model=None,
            pool=config.main_model,
        )
    else:
        try:
            model_selector = parse_model_selector(model_ref, config)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    provider = None
    model_name = None
    if model_selector is not None and model_selector.kind == "single" and model_selector.fixed_model:
        provider_name, selected_model_name = model_selector.fixed_model.split("/", 1)
        provider, _model_capability = resolve_model_capability(
            provider_name,
            selected_model_name,
            config.providers,
        )
        model_name = selected_model_name

    if config.extract.truncate_strategy not in {"head", "head_tail"}:
        raise click.ClickException("truncate_strategy must be head or head_tail")

    if config.extract.max_concurrency <= 0:
        raise click.ClickException("max_concurrency must be positive")
    if config.extract.max_retries <= 0:
        raise click.ClickException("max_retries must be positive")
    if config.extract.timeout <= 0:
        raise click.ClickException("timeout must be positive")
    if max_concurrency is not None and max_concurrency <= 0:
        raise click.ClickException("--max-concurrency must be positive")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise click.ClickException("--timeout must be positive")
    if sleep_every is not None and sleep_every <= 0:
        raise click.ClickException("--sleep-every must be positive")
    if sleep_time is not None and sleep_time <= 0:
        raise click.ClickException("--sleep-time must be positive")
    if (sleep_every is None) != (sleep_time is None):
        raise click.ClickException("Both --sleep-every and --sleep-time are required")
    if start_idx < 0:
        raise click.ClickException("--start-idx must be >= 0")
    if end_idx < -1:
        raise click.ClickException("--end-idx must be -1 or >= 0")
    if retry_failed and retry_failed_stages:
        raise click.ClickException("--retry-failed and --retry-failed-stages are mutually exclusive")
    if retry_list_json and (retry_failed or retry_failed_stages):
        raise click.ClickException(
            "--retry-list-json cannot be combined with --retry-failed or --retry-failed-stages"
        )

    if provider is not None and provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    }:
        resolved = resolve_api_keys(provider.api_keys)
        if not resolved:
            raise click.ClickException(f"{provider.type} providers require api_keys")

    if template_dir and (prompt_system or prompt_user or schema_path):
        raise click.ClickException("template-dir cannot be combined with custom prompt or schema flags")

    if (prompt_system and not prompt_user) or (prompt_user and not prompt_system):
        raise click.ClickException("Both --prompt-system and --prompt-user are required")

    custom_prompt = bool(prompt_system or prompt_user or template_dir)
    if custom_prompt and prompt_template != "simple":
        raise click.ClickException("Custom prompts cannot be combined with built-in prompt templates")
    if stage_dag and custom_prompt:
        raise click.ClickException("--stage-dag requires a built-in multi-stage prompt template")

    schema_override = schema_path or None
    prompt_system_path = Path(prompt_system) if prompt_system else None
    prompt_user_path = Path(prompt_user) if prompt_user else None
    template_dir_path = Path(template_dir) if template_dir else None
    if template_dir_path:
        prompt_system_path = template_dir_path / "system.j2"
        prompt_user_path = template_dir_path / "user.j2"
        schema_override = str(template_dir_path / "schema.json")

    for prompt_path in (prompt_system_path, prompt_user_path):
        if prompt_path and not prompt_path.exists():
            raise click.ClickException(f"Prompt template not found: {prompt_path}")

    if not render_md and any(
        item is not None
        for item in (render_template_path, render_template_name, render_template_dir)
    ):
        raise click.ClickException("Render template options require --render-md")
    if not render_md and render_output_dir is not None:
        raise click.ClickException("--render-output-dir requires --render-md")
    if render_md and sum(
        bool(item) for item in (render_template_path, render_template_name, render_template_dir)
    ) > 1:
        raise click.ClickException(
            "Use only one of --render-markdown-template/--render-template, --render-template-name, or --render-template-dir"
        )
    render_template_path_effective = render_template_path
    render_template_name_effective = render_template_name
    render_template_dir_effective = render_template_dir
    render_output_dir_effective: Path | None = None

    if render_md and not any(
        item is not None
        for item in (render_template_path, render_template_name, render_template_dir)
    ):
        if template_dir:
            render_template_dir_effective = template_dir
        elif not custom_prompt:
            render_template_name_effective = prompt_template
    if render_md:
        if render_output_dir is not None:
            render_output_dir_effective = Path(render_output_dir)
        elif output_path is not None:
            render_output_dir_effective = Path(output_path).parent
        else:
            render_output_dir_effective = Path("rendered_md")

    if render_template_path_effective and not Path(render_template_path_effective).exists():
        raise click.ClickException(f"Render template not found: {render_template_path_effective}")
    if render_template_dir_effective:
        render_template_dir_path = Path(render_template_dir_effective)
        render_template_file = render_template_dir_path / "render.j2"
        if not render_template_file.exists():
            raise click.ClickException(f"Render template not found: {render_template_file}")

    try:
        if schema_override:
            schema = load_schema(schema_override)
        elif prompt_template:
            schema = load_schema_for_template(prompt_template)
        else:
            schema = load_schema(config.extract.schema_path)
        validator = validate_schema(schema)
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from exc

    output = Path(output_path or config.extract.output)
    errors = Path(errors_path or config.extract.errors)
    retry_list_path = Path(retry_list_json) if retry_list_json else None
    split_out = Path(split_dir) if split_dir else None
    timeout_seconds_effective = timeout_seconds if timeout_seconds is not None else config.extract.timeout

    configure_logging(verbose)

    with provider_window_error_as_click():
        asyncio.run(
            extract_documents(
            inputs=tuple(all_inputs) if all_inputs else inputs,
            glob_pattern=glob_pattern,
            provider=provider,
            model=model_name,
            schema=schema,
            validator=validator,
            config=config,
            output_path=output,
            errors_path=errors,
            split=split,
            split_dir=split_out,
            force=force,
            force_stages=list(force_stages),
            retry_failed=retry_failed,
            retry_failed_stages=retry_failed_stages,
            retry_list_path=retry_list_path,
            stage_dag=stage_dag or config.extract.stage_dag,
            start_idx=start_idx,
            end_idx=end_idx,
            dry_run=dry_run,
            max_concurrency_override=max_concurrency,
            timeout_seconds=timeout_seconds_effective,
            prompt_template=prompt_template,
            output_language=output_language,
            custom_prompt=custom_prompt,
            prompt_system_path=prompt_system_path,
            prompt_user_path=prompt_user_path,
            render_md=render_md,
            render_output_dir=render_output_dir_effective,
            render_template_path=render_template_path_effective,
            render_template_name=render_template_name_effective,
            render_template_dir=render_template_dir_effective,
            sleep_every=sleep_every,
            sleep_time=sleep_time,
            verbose=verbose,
            model_selector=model_selector,
            )
        )


@paper.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option("-i", "--input", "input_paths", multiple=True, help="Input paper_infos JSON (repeatable)")
@click.option("--snapshot-db", "snapshot_db", default=None, help="Snapshot SQLite database path")
@click.option("--static-export-dir", "static_export_dir", default=None, help="Snapshot static export directory")
@click.option("--md-root", "md_roots", multiple=True, help="Source markdown root directory")
@click.option("--md-translated-root", "md_translated_roots", multiple=True, help="Translated markdown root directory")
@click.option("--output-embed-db", "output_embed_db", default=None, help="LanceDB output directory")
@click.option("--embedding", "embedding_override", default=None, help="Override embedding provider/model")
@click.option(
    "--max-concurrency",
    "max_concurrency",
    type=int,
    default=None,
    help="Embedding request concurrency",
)
@click.option("--template-tag", "template_tag", default=None, help="Override template tag for all JSON inputs")
@click.option("--force", is_flag=True, help="Delete existing index and rebuild from scratch")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def embed(
    config_path: str,
    input_paths: tuple[str, ...],
    snapshot_db: str | None,
    static_export_dir: str | None,
    md_roots: tuple[str, ...],
    md_translated_roots: tuple[str, ...],
    output_embed_db: str | None,
    embedding_override: str | None,
    max_concurrency: int | None,
    template_tag: str | None,
    force: bool,
    verbose: bool,
) -> None:
    """Build vector embeddings for paper search."""
    config = load_config(config_path)
    if not config.embedding:
        raise click.ClickException("Config missing [embedding] section")

    has_json = bool(input_paths)
    has_snapshot = snapshot_db is not None
    if not has_json and not has_snapshot:
        raise click.ClickException("Provide -i <json> or --snapshot-db <db>")
    if has_json and has_snapshot:
        raise click.ClickException("-i and --snapshot-db are mutually exclusive")
    if has_snapshot and not static_export_dir:
        raise click.ClickException("--snapshot-db requires --static-export-dir")
    if max_concurrency is not None and max_concurrency <= 0:
        raise click.ClickException("--max-concurrency must be positive")
    if embedding_override:
        provider, model = _resolve_provider_model_override(
            config.embedding.providers,
            embedding_override,
            section_name="embedding",
        )
        config = replace(
            config,
            embedding=replace(
                config.embedding,
                default_provider=provider.name,
                default_model=model.model_name,
            ),
        )

    vector_dir = Path(output_embed_db or (config.search.vector_dir if config.search else "paper_vectors"))
    if force and vector_dir.exists():
        import shutil

        click.echo(f"Removing existing vector index at {vector_dir} (--force)")
        shutil.rmtree(vector_dir)

    from deepresearch_flow.paper.vector_store import preflight_vector_store
    try:
        preflight_vector_store(vector_dir, dimensions=config.embedding.dimensions)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

    with provider_window_error_as_click():
        asyncio.run(
            run_embed_pipeline(
            config=config,
            input_paths=[Path(p) for p in input_paths] if has_json else None,
            snapshot_db=Path(snapshot_db) if snapshot_db else None,
            static_export_dir=Path(static_export_dir) if static_export_dir else None,
            md_roots=[Path(p) for p in md_roots],
            md_translated_roots=[Path(p) for p in md_translated_roots],
            vector_dir=vector_dir,
            template_tag_override=template_tag,
            max_concurrency_override=max_concurrency,
            verbose=verbose,
            )
        )
    click.echo("Embedding complete.")


@paper.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option("--embed-db", "embed_db", default=None, help="LanceDB directory to query")
@click.option("--embedding", "embedding_override", default=None, help="Override embedding provider/model")
@click.option("--rerank", "rerank_override", default=None, help="Override rerank provider/model")
@click.option("-q", "--query", "query_text", required=True, help="Search query")
@click.option("--top-n", "top_n", type=int, default=10, help="Number of results")
@click.option("--year", "year", type=int, default=None, help="Filter by year")
@click.option("--venue", "venue", default=None, help="Filter by venue")
@click.option("--no-rerank", "no_rerank", is_flag=True, help="Disable reranking")
@click.option("--no-hybrid", "no_hybrid", is_flag=True, help="Vector-only, no keyword recall")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def search(
    config_path: str,
    embed_db: str | None,
    embedding_override: str | None,
    rerank_override: str | None,
    query_text: str,
    top_n: int,
    year: int | None,
    venue: str | None,
    no_rerank: bool,
    no_hybrid: bool,
    verbose: bool,
) -> None:
    """Search papers using hybrid semantic + keyword search."""
    config = load_config(config_path)
    if not config.embedding:
        raise click.ClickException("Config missing [embedding] section")

    vector_dir = Path(embed_db or (config.search.vector_dir if config.search else "paper_vectors"))
    if not vector_dir.exists():
        raise click.ClickException(f"Vector index not found at {vector_dir}. Run 'paper embed' first.")

    with provider_window_error_as_click():
        asyncio.run(
            _run_search(
            config=config,
            vector_dir=vector_dir,
            query_text=query_text,
            top_n=top_n,
            year=year,
            venue=venue,
            no_rerank=no_rerank,
            no_hybrid=no_hybrid,
            verbose=verbose,
            embedding_override=embedding_override,
            rerank_override=rerank_override,
            )
        )


@paper.group()
def db() -> None:
    """Database management commands."""


register_db_commands(db)
