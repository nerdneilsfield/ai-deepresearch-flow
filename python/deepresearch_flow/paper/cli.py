"""CLI commands for paper workflows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from deepresearch_flow.paper.config import load_config, resolve_api_keys
from deepresearch_flow.paper.extract import extract_documents, parse_model_ref
from deepresearch_flow.paper.db import register_db_commands
from deepresearch_flow.paper.schema import load_schema, validate_schema, SchemaError
from deepresearch_flow.paper.template_registry import list_template_names, load_schema_for_template


@click.group()
def paper() -> None:
    """Paper extraction and database commands."""


@paper.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option(
    "-i",
    "--input",
    "inputs",
    multiple=True,
    required=True,
    help="Input markdown file or directory",
)
@click.option("-g", "--glob", "glob_pattern", default=None, help="Glob filter when input is a directory")
@click.option("-s", "--schema", "schema_path", default=None, help="Path to JSON schema")
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
@click.option("-m", "--model", "model_ref", required=True, help="provider/model")
@click.option("-o", "--output", "output_path", default=None, help="Aggregated JSON output path")
@click.option("-e", "--errors", "errors_path", default=None, help="Error JSON output path")
@click.option("--split", is_flag=True, help="Write per-document JSON outputs")
@click.option("--split-dir", "split_dir", default=None, help="Directory for split outputs")
@click.option("--force", is_flag=True, help="Force re-extraction")
@click.option("--retry-failed", is_flag=True, help="Retry only failed documents")
@click.option("--dry-run", is_flag=True, help="Discover inputs without calling providers")
@click.option("--max-concurrency", "max_concurrency", type=int, default=None, help="Override max concurrency")
def extract(
    config_path: str,
    inputs: tuple[str, ...],
    glob_pattern: str | None,
    schema_path: str | None,
    prompt_template: str,
    output_language: str,
    model_ref: str,
    output_path: str | None,
    errors_path: str | None,
    split: bool,
    split_dir: str | None,
    force: bool,
    retry_failed: bool,
    dry_run: bool,
    max_concurrency: int | None,
) -> None:
    """Extract structured information from markdown documents."""
    config = load_config(config_path)
    provider, model_name = parse_model_ref(model_ref, config.providers)

    if provider.structured_mode not in {"json_schema", "json_object", "none"}:
        raise click.ClickException("structured_mode must be json_schema, json_object, or none")

    if config.extract.truncate_strategy not in {"head", "head_tail"}:
        raise click.ClickException("truncate_strategy must be head or head_tail")

    if config.extract.max_concurrency <= 0:
        raise click.ClickException("max_concurrency must be positive")
    if config.extract.max_retries <= 0:
        raise click.ClickException("max_retries must be positive")
    if max_concurrency is not None and max_concurrency <= 0:
        raise click.ClickException("--max-concurrency must be positive")

    if provider.type in {
        "openai_compatible",
        "dashscope",
        "gemini_ai_studio",
        "azure_openai",
        "claude",
    }:
        resolved = resolve_api_keys(provider.api_keys)
        if not resolved:
            raise click.ClickException(f"{provider.type} providers require api_keys")

    if prompt_template != "simple" and (schema_path or config.extract.schema_path):
        raise click.ClickException("Custom schema cannot be combined with built-in prompt templates")

    try:
        if schema_path:
            schema = load_schema(schema_path)
        elif prompt_template:
            schema = load_schema_for_template(prompt_template)
        else:
            schema = load_schema(config.extract.schema_path)
        validator = validate_schema(schema)
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from exc

    output = Path(output_path or config.extract.output)
    errors = Path(errors_path or config.extract.errors)
    split_out = Path(split_dir) if split_dir else None

    asyncio.run(
        extract_documents(
            inputs=inputs,
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
            retry_failed=retry_failed,
            dry_run=dry_run,
            max_concurrency_override=max_concurrency,
            prompt_template=prompt_template,
            output_language=output_language,
        )
    )


@paper.group()
def db() -> None:
    """Database management commands."""


register_db_commands(db)
