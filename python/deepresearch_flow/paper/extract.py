"""Paper extraction pipeline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
import logging

import coloredlogs
import click
import httpx
from jsonschema import Draft7Validator
from tqdm import tqdm
from deepresearch_flow.paper.config import PaperConfig, ProviderConfig, resolve_api_keys
from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT
from deepresearch_flow.paper.render import render_papers, resolve_render_template
from deepresearch_flow.paper.schema import schema_to_prompt, validate_schema
from deepresearch_flow.paper.template_registry import (
    get_stage_definitions,
    load_custom_prompt_templates,
    load_prompt_templates,
)
from deepresearch_flow.paper.utils import (
    compute_source_hash,
    discover_markdown,
    estimate_tokens,
    parse_json,
    read_text,
    stable_hash,
    truncate_content,
    split_output_name,
    unique_split_name,
)
from deepresearch_flow.paper.providers.base import ProviderError


@dataclass
class ExtractionError:
    path: Path
    provider: str
    model: str
    error_type: str
    error_message: str
    stage_name: str | None = None


class KeyRotator:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._idx = 0
        self._lock = asyncio.Lock()

    async def next_key(self) -> str | None:
        if not self._keys:
            return None
        async with self._lock:
            key = self._keys[self._idx % len(self._keys)]
            self._idx += 1
            return key


logger = logging.getLogger(__name__)


def configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")


def parse_model_ref(model_ref: str, providers: list[ProviderConfig]) -> tuple[ProviderConfig, str]:
    if "/" not in model_ref:
        raise click.ClickException("--model must be in provider/model format")
    provider_name, model_name = model_ref.split("/", 1)
    for provider in providers:
        if provider.name == provider_name:
            if provider.model_list and model_name not in provider.model_list:
                raise click.ClickException(
                    f"Model '{model_name}' is not in provider '{provider_name}' model_list"
                )
            return provider, model_name
    raise click.ClickException(f"Unknown provider: {provider_name}")


def build_messages(
    content: str,
    schema: dict[str, Any],
    provider: ProviderConfig,
    prompt_template: str,
    output_language: str,
    custom_prompt: bool,
    prompt_system_path: Path | None,
    prompt_user_path: Path | None,
    stage_name: str | None = None,
    stage_fields: list[str] | None = None,
    previous_outputs: str | None = None,
) -> list[dict[str, str]]:
    prompt_schema = schema_to_prompt(schema)
    if custom_prompt and prompt_system_path and prompt_user_path:
        system_prompt, user_prompt = load_custom_prompt_templates(
            prompt_system_path,
            prompt_user_path,
            {
                "content": content,
                "schema": prompt_schema,
                "output_language": output_language,
                "stage_name": stage_name,
                "stage_fields": stage_fields or [],
                "previous_outputs": previous_outputs or "",
            },
        )
    elif prompt_template:
        system_prompt, user_prompt = load_prompt_templates(
            prompt_template,
            content=content,
            schema=prompt_schema,
            output_language=output_language,
            stage_name=stage_name,
            stage_fields=stage_fields,
            previous_outputs=previous_outputs,
        )
    else:
        system_prompt = provider.system_prompt or DEFAULT_SYSTEM_PROMPT
        if output_language:
            system_prompt = f"{system_prompt} Output language: {output_language}."
        user_prompt_template = provider.user_prompt or DEFAULT_USER_PROMPT
        user_prompt = user_prompt_template.format(content=content, schema=prompt_schema)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def should_retry_error(exc: ProviderError) -> bool:
    return exc.retryable



def append_metadata(
    payload: dict[str, Any],
    source_path: str,
    source_hash: str,
    provider: str,
    model: str,
    truncation: dict[str, Any] | None,
    prompt_template: str,
    output_language: str,
) -> dict[str, Any]:
    payload["source_path"] = source_path
    payload["source_hash"] = source_hash
    payload["provider"] = provider
    payload["model"] = model
    payload["prompt_template"] = prompt_template
    payload["output_language"] = output_language
    payload["extracted_at"] = datetime.utcnow().isoformat() + "Z"
    if truncation:
        payload["source_truncated"] = True
        payload["truncation"] = truncation
    else:
        payload["source_truncated"] = False
    return payload


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def load_errors(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json_atomic(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def build_stage_schema(
    base_schema: dict[str, Any], required_fields: list[str]
) -> dict[str, Any]:
    properties = base_schema.get("properties", {})
    unique_fields: list[str] = []
    for field in required_fields:
        if field not in unique_fields:
            unique_fields.append(field)

    stage_properties: dict[str, Any] = {}
    for field in unique_fields:
        stage_properties[field] = properties.get(field, {"type": "string"})

    return {
        "$schema": base_schema.get("$schema", "http://json-schema.org/draft-07/schema#"),
        "type": "object",
        "additionalProperties": True,
        "required": unique_fields,
        "properties": stage_properties,
    }


def load_stage_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


async def call_with_retries(
    provider: ProviderConfig,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    api_key: str | None,
    timeout: float,
    structured_mode: str,
    max_retries: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
    client: httpx.AsyncClient,
    validator: Draft7Validator,
) -> dict[str, Any]:
    attempt = 0
    use_structured = structured_mode
    while attempt < max_retries:
        attempt += 1
        try:
            response_text = await call_provider(
                provider,
                model,
                messages,
                schema,
                api_key,
                timeout,
                use_structured,
                client,
            )
        except ProviderError as exc:
            if exc.structured_error and use_structured != "none":
                use_structured = "none"
                continue
            if should_retry_error(exc) and attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base_seconds, attempt, backoff_max_seconds))
                continue
            raise

        try:
            data = parse_json(response_text)
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base_seconds, attempt, backoff_max_seconds))
                continue
            raise ProviderError(f"JSON parse failed: {exc}", error_type="parse_error") from exc

        errors_in_doc = sorted(validator.iter_errors(data), key=lambda e: e.path)
        if errors_in_doc:
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base_seconds, attempt, backoff_max_seconds))
                continue
            raise ProviderError(
                f"Schema validation failed: {errors_in_doc[0].message}",
                error_type="validation_error",
            )

        return data

    raise ProviderError("Max retries exceeded", retryable=False)


async def extract_documents(
    inputs: Iterable[str],
    glob_pattern: str | None,
    provider: ProviderConfig,
    model: str,
    schema: dict[str, Any],
    validator: Draft7Validator,
    config: PaperConfig,
    output_path: Path,
    errors_path: Path,
    split: bool,
    split_dir: Path | None,
    force: bool,
    retry_failed: bool,
    dry_run: bool,
    max_concurrency_override: int | None,
    prompt_template: str,
    output_language: str,
    custom_prompt: bool,
    prompt_system_path: Path | None,
    prompt_user_path: Path | None,
    render_md: bool,
    render_output_dir: Path | None,
    render_template_path: str | None,
    render_template_name: str | None,
    render_template_dir: str | None,
    verbose: bool,
) -> None:
    markdown_files = discover_markdown(inputs, glob_pattern)

    if retry_failed:
        error_entries = load_errors(errors_path)
        retry_paths = {Path(entry.get("source_path", "")).resolve() for entry in error_entries}
        markdown_files = [path for path in markdown_files if path in retry_paths]
        logger.debug("Retrying %d markdown files", len(markdown_files))
    else:
        logger.debug("Discovered %d markdown files", len(markdown_files))

    if dry_run:
        total_chars = 0
        for path in markdown_files:
            total_chars += len(read_text(path))
        click.echo(f"Discovered {len(markdown_files)} markdown files")
        if config.extract.cost_estimate:
            click.echo(f"Approx chars: {total_chars}, estimated tokens: {estimate_tokens(total_chars)}")
        return

    existing = load_existing(output_path)
    existing_by_path = {
        entry.get("source_path"): entry
        for entry in existing
        if isinstance(entry, dict) and entry.get("source_path")
    }

    rotator = KeyRotator(resolve_api_keys(provider.api_keys))
    max_concurrency = max_concurrency_override or config.extract.max_concurrency
    semaphore = asyncio.Semaphore(max_concurrency)

    errors: list[ExtractionError] = []
    results: dict[str, dict[str, Any]] = {}
    stage_definitions = get_stage_definitions(prompt_template) if not custom_prompt else []
    multi_stage = bool(stage_definitions)
    stage_output_dir = Path("paper_stage_outputs")
    if multi_stage:
        stage_output_dir.mkdir(parents=True, exist_ok=True)

    doc_bar: tqdm | None = None
    stage_bar: tqdm | None = None
    doc_bar = tqdm(total=len(markdown_files), desc="documents", unit="doc", position=0)
    if multi_stage and markdown_files:
        stage_total = len(markdown_files) * len(stage_definitions)
        stage_bar = tqdm(
            total=stage_total,
            desc="stages",
            unit="stage",
            position=1,
            leave=False,
        )

    async def process_one(path: Path, client: httpx.AsyncClient) -> None:
        source_path = str(path.resolve())
        current_stage: str | None = None
        try:
            if verbose:
                logger.debug("Processing %s", source_path)
            content = read_text(path)
            source_hash = compute_source_hash(content)
            stage_state: dict[str, Any] | None = None
            stage_path: Path | None = None

            if not force and not retry_failed:
                existing_entry = existing_by_path.get(source_path)
                if existing_entry and existing_entry.get("source_hash") == source_hash:
                    results[source_path] = existing_entry
                    if stage_bar:
                        stage_bar.update(len(stage_definitions))
                    return

            truncated_content, truncation = truncate_content(
                content, config.extract.truncate_max_chars, config.extract.truncate_strategy
            )

            api_key = await rotator.next_key()

            if multi_stage:
                stage_path = stage_output_dir / f"{stable_hash(source_path)}.json"
                stage_state = load_stage_state(stage_path) if not force else None
                if stage_state and stage_state.get("source_hash") != source_hash:
                    stage_state = None
                if stage_state is None:
                    stage_state = {
                        "source_path": source_path,
                        "source_hash": source_hash,
                        "prompt_template": prompt_template,
                        "output_language": output_language,
                        "stages": {},
                    }

            if multi_stage and stage_state is not None and stage_path is not None:
                stages: dict[str, dict[str, Any]] = stage_state.get("stages", {})
                metadata_fields = [
                    "paper_title",
                    "paper_authors",
                    "publication_date",
                    "publication_venue",
                ]
                for stage_def in stage_definitions:
                    stage_name = stage_def.name
                    stage_fields = stage_def.fields
                    current_stage = stage_name
                    if stage_name in stages and not force:
                        if stage_bar:
                            stage_bar.update(1)
                        continue
                    try:
                        required_fields = metadata_fields + stage_fields
                        stage_schema = build_stage_schema(schema, required_fields)
                        stage_validator = validate_schema(stage_schema)
                        previous_outputs = json.dumps(stages, ensure_ascii=False)
                        messages = build_messages(
                            truncated_content,
                            stage_schema,
                            provider,
                            prompt_template,
                            output_language,
                            custom_prompt=False,
                            prompt_system_path=None,
                            prompt_user_path=None,
                            stage_name=stage_name,
                            stage_fields=required_fields,
                            previous_outputs=previous_outputs,
                        )
                        async with semaphore:
                            data = await call_with_retries(
                                provider,
                                model,
                                messages,
                                stage_schema,
                                api_key,
                                timeout=60.0,
                                structured_mode=provider.structured_mode,
                                max_retries=config.extract.max_retries,
                                backoff_base_seconds=config.extract.backoff_base_seconds,
                                backoff_max_seconds=config.extract.backoff_max_seconds,
                                client=client,
                                validator=stage_validator,
                            )
                        stages[stage_name] = data
                        stage_state["stages"] = stages
                        write_json_atomic(stage_path, stage_state)
                    finally:
                        if stage_bar:
                            stage_bar.update(1)

                merged: dict[str, Any] = {}
                for stage_def in stage_definitions:
                    merged.update(stages.get(stage_def.name, {}))
                errors_in_doc = sorted(validator.iter_errors(merged), key=lambda e: e.path)
                if errors_in_doc:
                    raise ProviderError(
                        f"Schema validation failed: {errors_in_doc[0].message}",
                        error_type="validation_error",
                    )
                data = append_metadata(
                    merged,
                    source_path=source_path,
                    source_hash=source_hash,
                    provider=provider.name,
                    model=model,
                    truncation=truncation,
                    prompt_template=prompt_template,
                    output_language=output_language,
                )
                results[source_path] = data
            else:
                messages = build_messages(
                    truncated_content,
                    schema,
                    provider,
                    prompt_template if not custom_prompt else "custom",
                    output_language,
                    custom_prompt=custom_prompt,
                    prompt_system_path=prompt_system_path,
                    prompt_user_path=prompt_user_path,
                )
                async with semaphore:
                    data = await call_with_retries(
                        provider,
                        model,
                        messages,
                        schema,
                        api_key,
                        timeout=60.0,
                        structured_mode=provider.structured_mode,
                        max_retries=config.extract.max_retries,
                        backoff_base_seconds=config.extract.backoff_base_seconds,
                        backoff_max_seconds=config.extract.backoff_max_seconds,
                        client=client,
                        validator=validator,
                    )

                data = append_metadata(
                    data,
                    source_path=source_path,
                    source_hash=source_hash,
                    provider=provider.name,
                    model=model,
                    truncation=truncation,
                    prompt_template=prompt_template if not custom_prompt else "custom",
                    output_language=output_language,
                )
                results[source_path] = data
        except ProviderError as exc:
            logger.warning("Extraction failed for %s: %s", source_path, exc)
            errors.append(
                ExtractionError(
                    path=path,
                    provider=provider.name,
                    model=model,
                    error_type=exc.error_type,
                    error_message=str(exc),
                    stage_name=current_stage if multi_stage else None,
                )
            )
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Unexpected error while processing %s", source_path)
            errors.append(
                ExtractionError(
                    path=path,
                    provider=provider.name,
                    model=model,
                    error_type="unexpected_error",
                    error_message=str(exc),
                    stage_name=current_stage if multi_stage else None,
                )
            )
        finally:
            if doc_bar:
                doc_bar.update(1)

    try:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(process_one(path, client) for path in markdown_files))
    finally:
        if doc_bar:
            doc_bar.close()
        if stage_bar:
            stage_bar.close()

    final_results: list[dict[str, Any]] = []
    seen = set()
    for entry in existing:
        path = entry.get("source_path") if isinstance(entry, dict) else None
        if path and path in results:
            final_results.append(results[path])
            seen.add(path)
        elif path and not retry_failed:
            final_results.append(entry)
            seen.add(path)

    for path, entry in results.items():
        if path not in seen:
            final_results.append(entry)

    write_json(output_path, final_results)

    error_payload = [
        {
            "source_path": str(err.path.resolve()),
            "provider": err.provider,
            "model": err.model,
            "error_type": err.error_type,
            "error_message": err.error_message,
            "stage_name": err.stage_name,
        }
        for err in errors
    ]
    write_json(errors_path, error_payload)

    if split:
        target_dir = split_dir or output_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        used_names: set[str] = set()
        for entry in final_results:
            source_path = entry.get("source_path")
            if not source_path:
                continue
            base_name = split_output_name(Path(source_path))
            file_name = unique_split_name(base_name, used_names, source_path)
            write_json(target_dir / f"{file_name}.json", entry)

    if render_md:
        try:
            template = resolve_render_template(
                render_template_path, render_template_name, render_template_dir
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        render_dir = render_output_dir or Path("rendered_md")
        rendered = render_papers(final_results, render_dir, template, output_language)
        click.echo(f"Rendered {rendered} markdown files")

    click.echo(f"Processed {len(results)} documents")
    if errors:
        click.echo(f"Errors: {len(errors)} (see {errors_path})")
