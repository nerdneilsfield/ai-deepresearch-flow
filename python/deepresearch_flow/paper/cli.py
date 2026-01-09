"""CLI commands for paper workflows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from deepresearch_flow.paper.config import load_config, resolve_api_keys
from deepresearch_flow.paper.extract import extract_documents, parse_model_ref, configure_logging
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
@click.option("-m", "--model", "model_ref", required=True, help="provider/model")
@click.option("-o", "--output", "output_path", default=None, help="Aggregated JSON output path")
@click.option("-e", "--errors", "errors_path", default=None, help="Error JSON output path")
@click.option("--split", is_flag=True, help="Write per-document JSON outputs")
@click.option("--split-dir", "split_dir", default=None, help="Directory for split outputs")
@click.option("--force", is_flag=True, help="Force re-extraction")
@click.option("--retry-failed", is_flag=True, help="Retry only failed documents")
@click.option("--dry-run", is_flag=True, help="Discover inputs without calling providers")
@click.option("--max-concurrency", "max_concurrency", type=int, default=None, help="Override max concurrency")
@click.option("--render-md", "render_md", is_flag=True, help="Render markdown outputs after extraction")
@click.option(
    "--render-output-dir",
    "render_output_dir",
    default="rendered_md",
    help="Output directory for rendered markdown",
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
    glob_pattern: str | None,
    schema_path: str | None,
    prompt_template: str,
    output_language: str,
    prompt_system: str | None,
    prompt_user: str | None,
    template_dir: str | None,
    model_ref: str,
    output_path: str | None,
    errors_path: str | None,
    split: bool,
    split_dir: str | None,
    force: bool,
    retry_failed: bool,
    dry_run: bool,
    max_concurrency: int | None,
    render_md: bool,
    render_output_dir: str,
    render_template_path: str | None,
    render_template_name: str | None,
    render_template_dir: str | None,
    verbose: bool,
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

    if template_dir and (prompt_system or prompt_user or schema_path):
        raise click.ClickException("template-dir cannot be combined with custom prompt or schema flags")

    if (prompt_system and not prompt_user) or (prompt_user and not prompt_system):
        raise click.ClickException("Both --prompt-system and --prompt-user are required")

    custom_prompt = bool(prompt_system or prompt_user or template_dir)
    if custom_prompt and prompt_template != "simple":
        raise click.ClickException("Custom prompts cannot be combined with built-in prompt templates")

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
    if not render_md and render_output_dir != "rendered_md":
        raise click.ClickException("--render-output-dir requires --render-md")
    if render_md and sum(
        bool(item) for item in (render_template_path, render_template_name, render_template_dir)
    ) > 1:
        raise click.ClickException(
            "Use only one of --render-markdown-template/--render-template, --render-template-name, or --render-template-dir"
        )
    if render_template_path and not Path(render_template_path).exists():
        raise click.ClickException(f"Render template not found: {render_template_path}")
    if render_template_dir:
        render_template_dir_path = Path(render_template_dir)
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
    split_out = Path(split_dir) if split_dir else None

    configure_logging(verbose)

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
            custom_prompt=custom_prompt,
            prompt_system_path=prompt_system_path,
            prompt_user_path=prompt_user_path,
            render_md=render_md,
            render_output_dir=Path(render_output_dir),
            render_template_path=render_template_path,
            render_template_name=render_template_name,
            render_template_dir=render_template_dir,
            verbose=verbose,
        )
    )


@paper.group()
def db() -> None:
    """Database management commands."""


register_db_commands(db)
