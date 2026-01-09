"""Paper extraction pipeline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import click
import httpx
from jsonschema import Draft7Validator

from deepresearch_flow.paper.config import PaperConfig, ProviderConfig, resolve_api_keys
from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT
from deepresearch_flow.paper.schema import schema_to_prompt
from deepresearch_flow.paper.utils import (
    compute_source_hash,
    discover_markdown,
    estimate_tokens,
    parse_json,
    read_text,
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
) -> list[dict[str, str]]:
    system_prompt = provider.system_prompt or DEFAULT_SYSTEM_PROMPT
    user_prompt_template = provider.user_prompt or DEFAULT_USER_PROMPT
    prompt_schema = schema_to_prompt(schema)
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
) -> dict[str, Any]:
    payload["source_path"] = source_path
    payload["source_hash"] = source_hash
    payload["provider"] = provider
    payload["model"] = model
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
) -> None:
    markdown_files = discover_markdown(inputs, glob_pattern)

    if retry_failed:
        error_entries = load_errors(errors_path)
        retry_paths = {Path(entry.get("source_path", "")).resolve() for entry in error_entries}
        markdown_files = [path for path in markdown_files if path in retry_paths]

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

    async def process_one(path: Path, client: httpx.AsyncClient) -> None:
        source_path = str(path.resolve())
        content = read_text(path)
        source_hash = compute_source_hash(content)

        if not force and not retry_failed:
            existing_entry = existing_by_path.get(source_path)
            if existing_entry and existing_entry.get("source_hash") == source_hash:
                results[source_path] = existing_entry
                return

        truncated_content, truncation = truncate_content(
            content, config.extract.truncate_max_chars, config.extract.truncate_strategy
        )

        messages = build_messages(truncated_content, schema, provider)
        api_key = await rotator.next_key()

        async with semaphore:
            try:
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
                )
                results[source_path] = data
            except ProviderError as exc:
                errors.append(
                    ExtractionError(
                        path=path,
                        provider=provider.name,
                        model=model,
                        error_type=exc.error_type,
                        error_message=str(exc),
                    )
                )
            except Exception as exc:  # pragma: no cover - safety net
                errors.append(
                    ExtractionError(
                        path=path,
                        provider=provider.name,
                        model=model,
                        error_type="unexpected_error",
                        error_message=str(exc),
                    )
                )

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(process_one(path, client) for path in markdown_files))

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

    click.echo(f"Processed {len(results)} documents")
    if errors:
        click.echo(f"Errors: {len(errors)} (see {errors_path})")
