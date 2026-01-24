"""Paper extraction pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import importlib.resources as resources
import contextlib
import logging
import re
import signal
import time

import coloredlogs
import click
import httpx
from jsonschema import Draft7Validator
from rich.console import Console
from rich.table import Table
from tqdm import tqdm
from deepresearch_flow.paper.config import (
    ApiKeyConfig,
    PaperConfig,
    ProviderConfig,
    resolve_api_key_configs,
    resolve_api_keys,
)
from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.prompts import DEFAULT_SYSTEM_PROMPT, DEFAULT_USER_PROMPT
from deepresearch_flow.paper.render import render_papers, resolve_render_template
from deepresearch_flow.paper.schema import schema_to_prompt, validate_schema
from deepresearch_flow.paper.template_registry import (
    StageDefinition,
    get_template_bundle,
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


def _mask_key(value: str, *, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return value
    return f"...{value[-keep:]}"


class KeyRotator:
    def __init__(self, keys: list[ApiKeyConfig], *, cooldown_seconds: float, verbose: bool) -> None:
        self._keys = keys
        self._idx = 0
        self._lock = asyncio.Lock()
        self._cooldown_seconds = max(cooldown_seconds, 0.0)
        self._verbose = verbose
        self._cooldowns: dict[str, float] = {key.key: 0.0 for key in keys}
        self._quota_until: dict[str, float] = {key.key: 0.0 for key in keys}
        self._key_meta: dict[str, ApiKeyConfig] = {key.key: key for key in keys}
        self._error_counts: dict[str, int] = {key.key: 0 for key in keys}
        self._last_pause_until: float = 0.0
        self._last_key_quota_until: dict[str, float] = {key.key: 0.0 for key in keys}

    async def next_key(self) -> str | None:
        if not self._keys:
            return None
        while True:
            wait_for: float | None = None
            wait_until_epoch: float | None = None
            pause_reason: str | None = None
            should_log_pause = False
            async with self._lock:
                now = time.monotonic()
                now_epoch = time.time()
                total = len(self._keys)
                for offset in range(total):
                    idx = (self._idx + offset) % total
                    key = self._keys[idx].key
                    if (
                        self._cooldowns.get(key, 0.0) <= now
                        and self._quota_until.get(key, 0.0) <= now_epoch
                    ):
                        self._idx = idx + 1
                        return key
                waits: list[float] = []
                has_cooldown_wait = False
                has_quota_wait = False
                for meta in self._keys:
                    key = meta.key
                    cooldown_wait = max(self._cooldowns.get(key, 0.0) - now, 0.0)
                    quota_wait = max(self._quota_until.get(key, 0.0) - now_epoch, 0.0)
                    if cooldown_wait > 0:
                        has_cooldown_wait = True
                    if quota_wait > 0:
                        has_quota_wait = True
                    waits.append(max(cooldown_wait, quota_wait))
                wait_for = min(waits) if waits else None
                if wait_for is not None:
                    wait_until_epoch = now_epoch + wait_for
                    if wait_until_epoch > self._last_pause_until + 0.5:
                        self._last_pause_until = wait_until_epoch
                        if has_quota_wait and has_cooldown_wait:
                            pause_reason = "quota/cooldown"
                        elif has_quota_wait:
                            pause_reason = "quota"
                        elif has_cooldown_wait:
                            pause_reason = "cooldown"
                        else:
                            pause_reason = "unknown"
                        should_log_pause = True
            if wait_for is None:
                return None
            wait_for = max(wait_for, 0.01)
            if should_log_pause and wait_until_epoch is not None:
                reset_dt = datetime.fromtimestamp(wait_until_epoch).astimezone().isoformat()
                logger.warning(
                    "All API keys unavailable (%s); pausing %.2fs until %s",
                    pause_reason,
                    wait_for,
                    reset_dt,
                )
            elif self._verbose:
                logger.debug("All API keys cooling down; waiting %.2fs", wait_for)
            await asyncio.sleep(wait_for)

    async def key_pool_wait(self) -> tuple[float | None, str | None, float | None]:
        if not self._keys:
            return None, None, None
        async with self._lock:
            now = time.monotonic()
            now_epoch = time.time()
            for meta in self._keys:
                key = meta.key
                if (
                    self._cooldowns.get(key, 0.0) <= now
                    and self._quota_until.get(key, 0.0) <= now_epoch
                ):
                    return 0.0, None, None

            waits: list[float] = []
            has_cooldown_wait = False
            has_quota_wait = False
            for meta in self._keys:
                key = meta.key
                cooldown_wait = max(self._cooldowns.get(key, 0.0) - now, 0.0)
                quota_wait = max(self._quota_until.get(key, 0.0) - now_epoch, 0.0)
                if cooldown_wait > 0:
                    has_cooldown_wait = True
                if quota_wait > 0:
                    has_quota_wait = True
                waits.append(max(cooldown_wait, quota_wait))

            wait_for = min(waits) if waits else None
            if wait_for is None:
                return None, None, None
            if has_quota_wait and has_cooldown_wait:
                reason = "quota/cooldown"
            elif has_quota_wait:
                reason = "quota"
            elif has_cooldown_wait:
                reason = "cooldown"
            else:
                reason = "unknown"
            wait_until_epoch = now_epoch + wait_for
            return wait_for, reason, wait_until_epoch

    async def mark_error(self, key: str) -> None:
        if key not in self._cooldowns:
            return
        async with self._lock:
            now = time.monotonic()
            self._error_counts[key] = self._error_counts.get(key, 0) + 1
            cooldown_until = now + self._cooldown_seconds
            current = self._cooldowns.get(key, 0.0)
            self._cooldowns[key] = max(current, cooldown_until)
            if cooldown_until > current:
                logger.warning(
                    "API key %s cooling down for %.2fs (errors=%d)",
                    _mask_key(key),
                    self._cooldown_seconds,
                    self._error_counts[key],
                )
            elif self._verbose:
                logger.debug(
                    "API key cooldown applied (%.2fs, errors=%d)",
                    self._cooldown_seconds,
                    self._error_counts[key],
                )

    async def mark_quota_exceeded(self, key: str, message: str, status_code: int | None) -> bool:
        if key not in self._key_meta:
            return False
        meta = self._key_meta[key]
        tokens = meta.quota_error_tokens
        if not tokens:
            return False
        candidate = message
        try:
            data = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            collected: list[str] = [message]
            error = data.get("error")
            if isinstance(error, dict):
                for key_name in ("code", "type", "message"):
                    value = error.get(key_name)
                    if isinstance(value, str):
                        collected.append(value)
            for key_name in ("code", "type", "message"):
                value = data.get(key_name)
                if isinstance(value, str):
                    collected.append(value)
            candidate = " ".join(collected)
        lower_msg = candidate.lower()
        tokens_match = any(token.lower() in lower_msg for token in tokens)
        if not tokens_match:
            return False
        matched_tokens = [token for token in tokens if token.lower() in lower_msg]
        reset_epoch = _compute_next_reset_epoch(meta)
        if reset_epoch is None:
            logger.warning(
                "API key %s hit quota trigger but no reset_time/quota_duration configured "
                "(matched=%s, status_code=%s)",
                _mask_key(key),
                ",".join(matched_tokens) or "<none>",
                status_code if status_code is not None else "unknown",
            )
            return False
        async with self._lock:
            current = self._quota_until.get(key, 0.0)
            self._quota_until[key] = max(current, reset_epoch)
            if reset_epoch > self._last_key_quota_until.get(key, 0.0):
                self._last_key_quota_until[key] = reset_epoch
                wait_for = max(reset_epoch - time.time(), 0.0)
                reset_dt = datetime.fromtimestamp(reset_epoch).astimezone().isoformat()
                logger.warning(
                    "API key %s quota exhausted; pausing %.2fs until %s (matched=%s, status_code=%s)",
                    _mask_key(key),
                    wait_for,
                    reset_dt,
                    ",".join(matched_tokens) or "<none>",
                    status_code if status_code is not None else "unknown",
                )
            elif self._verbose:
                wait_for = max(reset_epoch - time.time(), 0.0)
                reset_dt = datetime.fromtimestamp(reset_epoch).astimezone().isoformat()
                logger.debug(
                    "API key %s quota exhausted; cooldown %.2fs until %s",
                    _mask_key(key),
                    wait_for,
                    reset_dt,
                )
        return True


logger = logging.getLogger(__name__)
_console = Console()


def log_extraction_failure(
    path: str,
    error_type: str,
    error_message: str,
    *,
    status_code: int | None = None,
) -> None:
    message = error_message.strip()
    if not message:
        message = "no error message"
    if status_code is not None and f"status_code={status_code}" not in message:
        message = f"{message} (status_code={status_code})"
    console_message = message
    if len(console_message) > 500:
        console_message = f"{console_message[:500]}..."
    logger.warning(
        "Extraction failed for %s (%s): %s",
        path,
        error_type,
        message,
    )
    _console.print(
        f"[bold red]Extraction failed[/] [dim]{path}[/]\n"
        f"[bold yellow]{error_type}[/]: {console_message}"
    )


def _summarize_error_message(message: str, limit: int = 300) -> str:
    text = (message or "").strip() or "no error message"
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def configure_logging(verbose: bool) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")


@dataclass(frozen=True)
class DocTask:
    path: Path
    stage_index: int
    stage_name: str
    stage_fields: list[str]


@dataclass
class DocState:
    total_stages: int
    next_index: int = 0
    failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class DocDagState:
    total_stages: int
    remaining: int
    completed: set[str] = field(default_factory=set)
    in_flight: set[str] = field(default_factory=set)
    failed: bool = False
    finalized: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class DocContext:
    path: Path
    source_path: str
    content: str
    truncated_content: str
    truncation: dict[str, Any] | None
    source_hash: str
    stage_path: Path | None
    stage_state: dict[str, Any] | None
    stages: dict[str, dict[str, Any]]
    stage_meta: dict[str, dict[str, Any]]


def _count_prompt_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(message.get("content") or "") for message in messages)


def _load_prompt_template_sources(name: str) -> tuple[str, str]:
    bundle = get_template_bundle(name)
    system_path = resources.files("deepresearch_flow.paper.prompt_templates").joinpath(
        bundle.prompt_system
    )
    user_path = resources.files("deepresearch_flow.paper.prompt_templates").joinpath(
        bundle.prompt_user
    )
    return (
        system_path.read_text(encoding="utf-8"),
        user_path.read_text(encoding="utf-8"),
    )


def _compute_prompt_hash(
    *,
    prompt_template: str,
    output_language: str,
    stage_name: str | None,
    stage_fields: list[str],
    custom_prompt: bool,
    prompt_system_path: Path | None,
    prompt_user_path: Path | None,
) -> str:
    if custom_prompt and prompt_system_path and prompt_user_path:
        system_text = prompt_system_path.read_text(encoding="utf-8")
        user_text = prompt_user_path.read_text(encoding="utf-8")
    else:
        system_text, user_text = _load_prompt_template_sources(prompt_template)
    payload = {
        "prompt_template": prompt_template,
        "output_language": output_language,
        "stage_name": stage_name or "",
        "stage_fields": stage_fields,
        "system_template": system_text,
        "user_template": user_text,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_stage_dependencies(
    stage_definitions: list[StageDefinition],
) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    for idx, stage_def in enumerate(stage_definitions):
        if stage_def.depends_on is None:
            if idx == 0:
                deps[stage_def.name] = []
            else:
                deps[stage_def.name] = [stage_definitions[idx - 1].name]
        else:
            deps[stage_def.name] = list(stage_def.depends_on)
    return deps


def _build_dependency_graph(
    stage_definitions: list[StageDefinition],
    deps: dict[str, list[str]],
) -> dict[str, list[str]]:
    stage_names = {stage_def.name for stage_def in stage_definitions}
    for stage_name, dependencies in deps.items():
        for dependency in dependencies:
            if dependency not in stage_names:
                raise ValueError(
                    f"Stage '{stage_name}' depends on unknown stage '{dependency}'"
                )

    dependents: dict[str, list[str]] = {name: [] for name in stage_names}
    indegree: dict[str, int] = {name: 0 for name in stage_names}
    for stage_name, dependencies in deps.items():
        indegree[stage_name] = len(dependencies)
        for dependency in dependencies:
            dependents[dependency].append(stage_name)

    queue = deque(name for name, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for child in dependents[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if visited != len(stage_names):
        raise ValueError("Stage dependency cycle detected in template definition")

    return dependents


def _parse_reset_time(reset_time: str) -> datetime | None:
    candidate = reset_time.strip()
    match = re.search(
        r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)(?:\s*(Z|[+-]\d{2}:?\d{2}))?",
        candidate,
    )
    if not match:
        return None
    date_part, time_part, tz_part = match.group(1), match.group(2), match.group(3)
    if tz_part:
        tz_part = tz_part.replace("Z", "+00:00")
        tz_part = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", tz_part)
    iso_str = f"{date_part}T{time_part}{tz_part or ''}"
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        return None


def _compute_next_reset_epoch(meta: ApiKeyConfig) -> float | None:
    if not meta.reset_time or not meta.quota_duration:
        return None
    base = _parse_reset_time(meta.reset_time)
    if not base:
        return None
    duration = meta.quota_duration
    if duration <= 0:
        return None
    now = datetime.now(timezone.utc)
    base_utc = base.astimezone(timezone.utc)
    if now <= base_utc:
        return base_utc.timestamp()
    elapsed = (now - base_utc).total_seconds()
    cycles = math.floor(elapsed / duration) + 1
    return base_utc.timestamp() + cycles * duration


def _estimate_tokens_for_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return estimate_tokens(char_count)


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {remainder:.1f}s"


def _format_rate(value: float, unit: str) -> str:
    if value <= 0:
        return f"0 {unit}"
    return f"{value:.2f} {unit}"


@dataclass
class ExtractionStats:
    doc_bar: tqdm | None
    input_chars: int = 0
    prompt_chars: int = 0
    output_chars: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add_input_chars(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.input_chars += count
            self._update_bar()

    async def add_prompt_chars(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.prompt_chars += count
            self._update_bar()

    async def add_output_chars(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.output_chars += count
            self._update_bar()

    def _update_bar(self) -> None:
        if not self.doc_bar:
            return
        prompt_tokens = _estimate_tokens_for_chars(self.prompt_chars)
        completion_tokens = _estimate_tokens_for_chars(self.output_chars)
        total_tokens = prompt_tokens + completion_tokens
        self.doc_bar.set_postfix_str(f"tok p/c/t {prompt_tokens}/{completion_tokens}/{total_tokens}")

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


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def normalize_response_keys(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return data

    allow_extra = schema.get("additionalProperties", True)
    normalized_map: dict[str, str] = {}
    for prop_key in properties.keys():
        normalized_map.setdefault(_normalized_key(prop_key), prop_key)

    normalized: dict[str, Any] = {}
    renamed: list[tuple[str, str]] = []
    dropped: list[str] = []

    for key, value in data.items():
        if key in properties:
            normalized[key] = value
            continue

        target = normalized_map.get(_normalized_key(key))
        if target:
            if target in normalized:
                dropped.append(key)
            else:
                normalized[target] = value
                renamed.append((key, target))
            continue

        if allow_extra:
            normalized[key] = value
        else:
            dropped.append(key)

    if renamed:
        logger.debug("Normalized response keys: %s", renamed)
    if dropped and not allow_extra:
        logger.debug("Dropped response keys not in schema: %s", dropped)

    return normalized


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and isinstance(data.get("papers"), list):
        return data["papers"]
    if isinstance(data, list):
        return data
    return []


def load_errors(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def load_retry_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise click.ClickException(f"Retry list JSON not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid retry list JSON: {exc}") from exc
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise click.ClickException("Retry list JSON must be a list or contain an 'items' list")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


class RequestThrottle:
    def __init__(self, sleep_every: int, sleep_time: float) -> None:
        if sleep_every <= 0 or sleep_time <= 0:
            raise ValueError("sleep_every and sleep_time must be positive")
        self.sleep_every = sleep_every
        self.sleep_time = sleep_time
        self._count = 0
        self._lock = asyncio.Lock()

    async def tick(self) -> None:
        async with self._lock:
            self._count += 1
            if self._count % self.sleep_every == 0:
                await asyncio.sleep(self.sleep_time)


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
    key_rotator: KeyRotator | None = None,
    throttle: RequestThrottle | None = None,
    stats: ExtractionStats | None = None,
) -> dict[str, Any]:
    attempt = 0
    use_structured = structured_mode
    prompt_chars = _count_prompt_chars(messages)
    while attempt < max_retries:
        attempt += 1
        if key_rotator:
            api_key = await key_rotator.next_key()
        if throttle:
            await throttle.tick()
        if stats:
            await stats.add_prompt_chars(prompt_chars)
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
            if stats:
                await stats.add_output_chars(len(response_text))
        except ProviderError as exc:
            quota_hit = False
            if api_key and key_rotator:
                quota_hit = await key_rotator.mark_quota_exceeded(
                    api_key,
                    str(exc),
                    exc.status_code,
                )
            if exc.structured_error and use_structured != "none":
                logger.warning(
                    "Structured response failed; retrying without structured output "
                    "(provider=%s, model=%s, status_code=%s): %s",
                    provider.name,
                    model,
                    exc.status_code if exc.status_code is not None else "unknown",
                    _summarize_error_message(str(exc)),
                )
                use_structured = "none"
                continue
            if quota_hit:
                attempt -= 1
                continue
            if api_key and key_rotator and not quota_hit and should_retry_error(exc):
                await key_rotator.mark_error(api_key)
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

        data = normalize_response_keys(data, schema)

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
    force_stages: list[str],
    retry_failed: bool,
    retry_failed_stages: bool,
    retry_list_path: Path | None,
    stage_dag: bool,
    start_idx: int,
    end_idx: int,
    dry_run: bool,
    max_concurrency_override: int | None,
    timeout_seconds: float,
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
    sleep_every: int | None,
    sleep_time: float | None,
    verbose: bool,
) -> None:
    start_time = time.monotonic()
    markdown_files = discover_markdown(inputs, glob_pattern, recursive=True)
    template_tag = prompt_template if not custom_prompt else "custom"

    total_files = len(markdown_files)
    if start_idx != 0 or end_idx != -1:
        slice_end = end_idx if end_idx != -1 else None
        markdown_files = markdown_files[start_idx:slice_end]
        logger.info(
            "Applied range filter [%d:%d]. Files: %d -> %d",
            start_idx,
            end_idx,
            total_files,
            len(markdown_files),
        )
        if not markdown_files:
            logger.warning(
                "Range filter yielded 0 files (range=%d:%d, total=%d)",
                start_idx,
                end_idx,
                total_files,
            )

    retry_list_entries = load_retry_list(retry_list_path) if retry_list_path else []
    error_entries = load_errors(errors_path) if retry_failed or retry_failed_stages else []
    retry_stage_map: dict[str, set[str]] = {}
    retry_full_paths: set[str] = set()

    if retry_failed:
        for entry in error_entries:
            source_path = entry.get("source_path")
            if not source_path:
                continue
            retry_full_paths.add(str(Path(source_path).resolve()))

    if retry_failed_stages:
        for entry in error_entries:
            source_path = entry.get("source_path")
            stage_name = entry.get("stage_name")
            if not source_path:
                continue
            resolved = str(Path(source_path).resolve())
            if not stage_name:
                retry_full_paths.add(resolved)
                continue
            retry_stage_map.setdefault(resolved, set()).add(stage_name)

    if retry_list_entries:
        for entry in retry_list_entries:
            source_path = entry.get("source_path")
            if not source_path:
                continue
            resolved = str(Path(source_path).resolve())
            retry_stages = entry.get("retry_stages")
            if isinstance(retry_stages, list) and retry_stages:
                stage_set = {
                    stage for stage in retry_stages if isinstance(stage, str) and stage.strip()
                }
                if stage_set:
                    retry_stage_map.setdefault(resolved, set()).update(stage_set)
                    continue
            retry_full_paths.add(resolved)

    retry_mode = retry_failed or retry_failed_stages or bool(retry_list_entries)
    retry_stages_mode = retry_failed_stages or bool(retry_stage_map)

    if retry_mode:
        retry_paths = set(retry_full_paths) | set(retry_stage_map.keys())
        markdown_files = [
            path for path in markdown_files if str(path.resolve()) in retry_paths
        ]
        logger.debug("Retrying %d markdown files", len(markdown_files))
        if not markdown_files:
            logger.warning("Retry list produced 0 files to process")
    else:
        logger.debug("Discovered %d markdown files", len(markdown_files))

    stage_definitions = get_stage_definitions(prompt_template) if not custom_prompt else []
    multi_stage = bool(stage_definitions)
    stage_dag_enabled = stage_dag and multi_stage

    if dry_run:
        input_chars = 0
        prompt_chars = 0
        metadata_fields = [
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
        ]
        for path in markdown_files:
            content = read_text(path)
            input_chars += len(content)
            truncated_content, _ = truncate_content(
                content, config.extract.truncate_max_chars, config.extract.truncate_strategy
            )
            if multi_stage:
                for stage_def in stage_definitions:
                    required_fields = metadata_fields + stage_def.fields
                    stage_schema = build_stage_schema(schema, required_fields)
                    messages = build_messages(
                        truncated_content,
                        stage_schema,
                        provider,
                        prompt_template,
                        output_language,
                        custom_prompt=False,
                        prompt_system_path=None,
                        prompt_user_path=None,
                        stage_name=stage_def.name,
                        stage_fields=required_fields,
                        previous_outputs="{}",
                    )
                    prompt_chars += _count_prompt_chars(messages)
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
                prompt_chars += _count_prompt_chars(messages)

        if stage_dag_enabled and stage_definitions:
            stage_dependencies = _resolve_stage_dependencies(stage_definitions)
            _build_dependency_graph(stage_definitions, stage_dependencies)
            plan_table = Table(
                title="stage DAG plan (dry-run)",
                header_style="bold cyan",
                title_style="bold magenta",
            )
            plan_table.add_column("Stage", style="cyan", no_wrap=True)
            plan_table.add_column("Depends on", style="white")
            for stage_def in stage_definitions:
                deps = stage_dependencies.get(stage_def.name, [])
                plan_table.add_row(stage_def.name, ", ".join(deps) if deps else "none")
            Console().print(plan_table)

        duration = time.monotonic() - start_time
        prompt_tokens = _estimate_tokens_for_chars(prompt_chars)
        completion_tokens = 0
        total_tokens = prompt_tokens
        doc_count = len(markdown_files)
        avg_time = duration / doc_count if doc_count else 0.0
        docs_per_min = (doc_count / duration) * 60 if duration > 0 else 0.0
        tokens_per_sec = (total_tokens / duration) if duration > 0 else 0.0

        table = Table(
            title="paper extract summary (dry-run)",
            header_style="bold cyan",
            title_style="bold magenta",
        )
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white", overflow="fold")
        table.add_row("Documents", str(doc_count))
        table.add_row("Duration", _format_duration(duration))
        table.add_row("Avg time/doc", _format_duration(avg_time))
        table.add_row("Throughput", _format_rate(docs_per_min, "docs/min"))
        table.add_row("Input chars", str(input_chars))
        table.add_row("Prompt chars", str(prompt_chars))
        table.add_row("Output chars", "0")
        table.add_row("Est prompt tokens", str(prompt_tokens))
        table.add_row("Est completion tokens", str(completion_tokens))
        table.add_row("Est total tokens", str(total_tokens))
        table.add_row("Est tokens/sec", _format_rate(tokens_per_sec, "tok/s"))
        Console().print(table)
        return

    existing = load_existing(output_path)
    existing_by_path = {
        entry.get("source_path"): entry
        for entry in existing
        if isinstance(entry, dict) and entry.get("source_path")
    }

    cooldown_seconds = max(1.0, float(config.extract.backoff_base_seconds))
    resolved_keys = resolve_api_key_configs(provider.api_keys)
    rotator = KeyRotator(
        resolved_keys,
        cooldown_seconds=cooldown_seconds,
        verbose=verbose,
    )
    max_concurrency = max_concurrency_override or config.extract.max_concurrency
    semaphore = asyncio.Semaphore(max_concurrency)

    errors: list[ExtractionError] = []
    results: dict[str, dict[str, Any]] = {}
    stage_output_dir = Path("paper_stage_outputs")
    if multi_stage:
        stage_output_dir.mkdir(parents=True, exist_ok=True)
        if stage_dag_enabled:
            logger.info("Multi-stage scheduler: DAG")
        else:
            logger.info("Multi-stage scheduler: sequential")

    throttle = None
    if sleep_every is not None or sleep_time is not None:
        if not sleep_every or not sleep_time:
            raise ValueError("--sleep-every and --sleep-time must be set together")
        throttle = RequestThrottle(sleep_every, float(sleep_time))

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
    stats = ExtractionStats(doc_bar=doc_bar)

    results_lock = asyncio.Lock()
    logger.info("Request timeout set to %.1fs", timeout_seconds)

    pause_threshold_seconds = max(0.0, float(config.extract.pause_threshold_seconds))
    pause_watchdog_seconds = max(60.0, pause_threshold_seconds * 6.0)
    pause_gate: asyncio.Event | None = None
    pause_task: asyncio.Task[None] | None = None

    shutdown_event = asyncio.Event()
    shutdown_reason: str | None = None

    def request_shutdown(reason: str) -> None:
        nonlocal shutdown_reason
        if shutdown_event.is_set():
            return
        shutdown_reason = reason
        shutdown_event.set()
        if pause_gate:
            pause_gate.set()
        logger.warning("Graceful shutdown requested (%s); draining in-flight tasks", reason)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown, sig.name)
        except (NotImplementedError, RuntimeError, ValueError):
            signal.signal(
                sig,
                lambda *_args, _sig=sig: loop.call_soon_threadsafe(
                    request_shutdown, _sig.name
                ),
            )

    if resolved_keys:
        pause_gate = asyncio.Event()
        pause_gate.set()

        async def pause_watcher() -> None:
            paused = False
            try:
                while True:
                    wait_for, reason, wait_until_epoch = await rotator.key_pool_wait()
                    if wait_for is None or wait_for <= 0:
                        if paused:
                            paused = False
                            pause_gate.set()
                            logger.info("Queue resumed; key pool available")
                        await asyncio.sleep(0.5)
                        continue
                    if wait_for <= pause_threshold_seconds:
                        if paused:
                            paused = False
                            pause_gate.set()
                            logger.info(
                                "Queue resumed; key pool wait %.2fs below threshold %.2fs",
                                wait_for,
                                pause_threshold_seconds,
                            )
                        await asyncio.sleep(min(wait_for, pause_threshold_seconds))
                        continue
                    if not paused:
                        paused = True
                        pause_gate.clear()
                        reset_dt = (
                            datetime.fromtimestamp(wait_until_epoch).astimezone().isoformat()
                            if wait_until_epoch
                            else "unknown"
                        )
                        logger.warning(
                            "Queue paused (keys unavailable: %s); waiting %.2fs until %s",
                            reason or "unknown",
                            wait_for,
                            reset_dt,
                        )
                    await asyncio.sleep(min(wait_for, max(pause_threshold_seconds, 1.0)))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Queue pause watcher failed; releasing pause gate")
                pause_gate.set()

        pause_task = asyncio.create_task(pause_watcher())

    async def await_key_pool_ready() -> None:
        if shutdown_event.is_set():
            return
        if not pause_gate or pause_gate.is_set():
            return
        try:
            pause_wait = asyncio.create_task(pause_gate.wait())
            shutdown_wait = asyncio.create_task(shutdown_event.wait())
            try:
                await asyncio.wait_for(
                    asyncio.wait(
                        [pause_wait, shutdown_wait],
                        return_when=asyncio.FIRST_COMPLETED,
                    ),
                    timeout=pause_watchdog_seconds,
                )
            finally:
                for task in (pause_wait, shutdown_wait):
                    task.cancel()
        except asyncio.TimeoutError:
            logger.warning(
                "Queue pause watchdog timeout; rechecking key pool availability"
            )
            wait_for, _, _ = await rotator.key_pool_wait()
            if wait_for is None or wait_for <= 0:
                pause_gate.set()

    async def drain_queue(queue: asyncio.Queue[Any], drain_lock: asyncio.Lock) -> int:
        drained = 0
        async with drain_lock:
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                queue.task_done()
                drained += 1
        return drained

    async def wait_for_queue(queue: asyncio.Queue[Any], drain_lock: asyncio.Lock) -> None:
        if shutdown_event.is_set():
            await drain_queue(queue, drain_lock)
            await queue.join()
            return
        join_task = asyncio.create_task(queue.join())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, _pending = await asyncio.wait(
            [join_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done:
            await drain_queue(queue, drain_lock)
        await queue.join()
        for task in (join_task, shutdown_task):
            task.cancel()

    def build_output_payload() -> dict[str, Any]:
        final_results: list[dict[str, Any]] = []
        seen = set()
        for entry in existing:
            path = entry.get("source_path") if isinstance(entry, dict) else None
            if path and path in results:
                final_results.append(results[path])
                seen.add(path)
            elif path:
                final_results.append(entry)
                seen.add(path)

        for path, entry in results.items():
            if path not in seen:
                final_results.append(entry)
        return {"template_tag": template_tag, "papers": final_results}

    async def persist_output_snapshot() -> None:
        async with results_lock:
            payload = build_output_payload()
        await asyncio.to_thread(write_json, output_path, payload)

    def build_merged(ctx: DocContext) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for stage_def in stage_definitions:
            merged.update(ctx.stages.get(stage_def.name, {}))
        return merged

    async def update_results(ctx: DocContext) -> None:
        merged = build_merged(ctx)
        data = append_metadata(
            merged,
            source_path=ctx.source_path,
            source_hash=ctx.source_hash,
            provider=provider.name,
            model=model,
            truncation=ctx.truncation,
            prompt_template=prompt_template,
            output_language=output_language,
        )
        async with results_lock:
            results[ctx.source_path] = data
        await persist_output_snapshot()

    async def run_single_stage(client: httpx.AsyncClient) -> None:
        doc_queue: asyncio.Queue[Path] = asyncio.Queue()
        drain_lock = asyncio.Lock()

        async def process_one(path: Path) -> None:
            source_path = str(path.resolve())
            current_stage: str | None = None
            try:
                if shutdown_event.is_set():
                    return
                if verbose:
                    logger.debug("Processing %s", source_path)
                content = read_text(path)
                await stats.add_input_chars(len(content))
                source_hash = compute_source_hash(content)

                if not force and not retry_mode:
                    existing_entry = existing_by_path.get(source_path)
                    if existing_entry and existing_entry.get("source_hash") == source_hash:
                        results[source_path] = existing_entry
                        return

                truncated_content, truncation = truncate_content(
                    content, config.extract.truncate_max_chars, config.extract.truncate_strategy
                )
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
                if shutdown_event.is_set():
                    return
                await await_key_pool_ready()
                async with semaphore:
                    data = await call_with_retries(
                        provider,
                        model,
                        messages,
                        schema,
                        None,
                        timeout=timeout_seconds,
                        structured_mode=provider.structured_mode,
                        max_retries=config.extract.max_retries,
                        backoff_base_seconds=config.extract.backoff_base_seconds,
                        backoff_max_seconds=config.extract.backoff_max_seconds,
                        client=client,
                        validator=validator,
                        key_rotator=rotator,
                        throttle=throttle,
                        stats=stats,
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
                log_extraction_failure(
                    source_path,
                    exc.error_type,
                    str(exc),
                    status_code=exc.status_code,
                )
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

        for path in markdown_files:
            if shutdown_event.is_set():
                break
            doc_queue.put_nowait(path)

        async def worker() -> None:
            while True:
                if shutdown_event.is_set():
                    await drain_queue(doc_queue, drain_lock)
                    return
                try:
                    path = doc_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await process_one(path)
                doc_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_concurrency)]
        if markdown_files:
            await wait_for_queue(doc_queue, drain_lock)
        for w in workers:
            w.cancel()

    async def run_multi_stage(client: httpx.AsyncClient) -> None:
        metadata_fields = [
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
        ]
        force_stage_set = set(force_stages or [])
        doc_contexts: dict[Path, DocContext] = {}
        doc_states: dict[Path, DocState] = {}
        task_queue: asyncio.Queue[DocTask] = asyncio.Queue()
        drain_lock = asyncio.Lock()

        for path in markdown_files:
            if shutdown_event.is_set():
                break
            source_path = str(path.resolve())
            if verbose:
                logger.debug("Preparing %s", source_path)
            content = read_text(path)
            await stats.add_input_chars(len(content))
            source_hash = compute_source_hash(content)
            truncated_content, truncation = truncate_content(
                content, config.extract.truncate_max_chars, config.extract.truncate_strategy
            )
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
                    "stage_meta": {},
                }
            stages: dict[str, dict[str, Any]] = stage_state.get("stages", {})
            stage_meta: dict[str, dict[str, Any]] = stage_state.get("stage_meta", {})
            doc_contexts[path] = DocContext(
                path=path,
                source_path=source_path,
                content=content,
                truncated_content=truncated_content,
                truncation=truncation,
                source_hash=source_hash,
                stage_path=stage_path,
                stage_state=stage_state,
                stages=stages,
                stage_meta=stage_meta,
            )
            doc_states[path] = DocState(total_stages=len(stage_definitions))
            for idx, stage_def in enumerate(stage_definitions):
                required_fields = metadata_fields + stage_def.fields
                task_queue.put_nowait(
                    DocTask(
                        path=path,
                        stage_index=idx,
                        stage_name=stage_def.name,
                        stage_fields=required_fields,
                    )
                )

        async def run_task(task: DocTask) -> None:
            ctx = doc_contexts[task.path]
            state = doc_states[task.path]

            while True:
                if shutdown_event.is_set():
                    async with state.lock:
                        state.failed = True
                        state.event.set()
                    if stage_bar:
                        stage_bar.update(1)
                    return
                async with state.lock:
                    if state.failed:
                        if stage_bar:
                            stage_bar.update(1)
                        return
                    if task.stage_index == state.next_index:
                        break
                    wait_event = state.event
                await wait_event.wait()

            current_stage = task.stage_name
            is_retry_full = ctx.source_path in retry_full_paths
            retry_stages = (
                retry_stage_map.get(ctx.source_path)
                if retry_stages_mode and not is_retry_full
                else None
            )
            if shutdown_event.is_set():
                async with state.lock:
                    state.failed = True
                    state.event.set()
                if stage_bar:
                    stage_bar.update(1)
                return
            stage_record = ctx.stages.get(current_stage)
            if (
                retry_stages_mode
                and not is_retry_full
                and retry_stages is not None
                and current_stage not in retry_stages
                and stage_record is not None
            ):
                if stage_bar:
                    stage_bar.update(1)
                await update_results(ctx)
                final_validation_error: str | None = None
                if not state.failed and task.stage_index == state.total_stages - 1:
                    merged = build_merged(ctx)
                    errors_in_doc = sorted(validator.iter_errors(merged), key=lambda e: e.path)
                    if errors_in_doc:
                        final_validation_error = errors_in_doc[0].message

                async with state.lock:
                    if final_validation_error:
                        errors.append(
                            ExtractionError(
                                path=task.path,
                                provider=provider.name,
                                model=model,
                                error_type="validation_error",
                                error_message=f"Schema validation failed: {final_validation_error}",
                                stage_name=current_stage,
                            )
                        )
                        state.failed = True
                    if not state.failed:
                        state.next_index += 1
                    if state.next_index >= state.total_stages or state.failed:
                        if doc_bar and state.total_stages:
                            doc_bar.update(1)
                    state.event.set()
                    state.event = asyncio.Event()
                return
            prompt_hash = prompt_hash_map[current_stage]
            stage_schema = stage_schema_map[current_stage]
            stage_validator = stage_validator_map[current_stage]
            stage_meta = ctx.stage_meta.get(current_stage, {})
            needs_run = force or current_stage in force_stage_set
            if stage_record is None:
                needs_run = True
            if is_retry_full:
                needs_run = True
            if retry_stages is not None and current_stage in retry_stages:
                needs_run = True
            if stage_meta.get("prompt_hash") != prompt_hash:
                needs_run = True
            if stage_record is not None and not needs_run:
                errors_in_stage = sorted(stage_validator.iter_errors(stage_record), key=lambda e: e.path)
                if errors_in_stage:
                    needs_run = True

            if not needs_run:
                if stage_bar:
                    stage_bar.update(1)
                await update_results(ctx)
            else:
                try:
                    previous_outputs = json.dumps(ctx.stages, ensure_ascii=False)
                    messages = build_messages(
                        ctx.truncated_content,
                        stage_schema,
                        provider,
                        prompt_template,
                        output_language,
                        custom_prompt=False,
                        prompt_system_path=None,
                        prompt_user_path=None,
                        stage_name=current_stage,
                        stage_fields=task.stage_fields,
                        previous_outputs=previous_outputs,
                    )
                    async with semaphore:
                        data = await call_with_retries(
                            provider,
                            model,
                            messages,
                            stage_schema,
                            None,
                            timeout=timeout_seconds,
                            structured_mode=provider.structured_mode,
                            max_retries=config.extract.max_retries,
                            backoff_base_seconds=config.extract.backoff_base_seconds,
                            backoff_max_seconds=config.extract.backoff_max_seconds,
                            client=client,
                            validator=stage_validator,
                            key_rotator=rotator,
                            throttle=throttle,
                            stats=stats,
                        )
                    ctx.stages[current_stage] = data
                    ctx.stage_meta[current_stage] = {"prompt_hash": prompt_hash}
                    ctx.stage_state["stages"] = ctx.stages
                    ctx.stage_state["stage_meta"] = ctx.stage_meta
                    write_json_atomic(ctx.stage_path, ctx.stage_state)
                    if stage_bar:
                        stage_bar.update(1)
                    await update_results(ctx)
                except ProviderError as exc:
                    log_extraction_failure(
                        ctx.source_path,
                        exc.error_type,
                        str(exc),
                        status_code=exc.status_code,
                    )
                    errors.append(
                        ExtractionError(
                            path=task.path,
                            provider=provider.name,
                            model=model,
                            error_type=exc.error_type,
                            error_message=str(exc),
                            stage_name=current_stage,
                        )
                    )
                    async with state.lock:
                        state.failed = True
                    if stage_bar:
                        stage_bar.update(1)
                except Exception as exc:  # pragma: no cover - safety net
                    logger.exception("Unexpected error while processing %s", ctx.source_path)
                    errors.append(
                        ExtractionError(
                            path=task.path,
                            provider=provider.name,
                            model=model,
                            error_type="unexpected_error",
                            error_message=str(exc),
                            stage_name=current_stage,
                        )
                    )
                    async with state.lock:
                        state.failed = True
                    if stage_bar:
                        stage_bar.update(1)

            final_validation_error: str | None = None
            if not state.failed and task.stage_index == state.total_stages - 1:
                merged = build_merged(ctx)
                errors_in_doc = sorted(validator.iter_errors(merged), key=lambda e: e.path)
                if errors_in_doc:
                    final_validation_error = errors_in_doc[0].message

            async with state.lock:
                if final_validation_error:
                    errors.append(
                        ExtractionError(
                            path=task.path,
                            provider=provider.name,
                            model=model,
                            error_type="validation_error",
                            error_message=f"Schema validation failed: {final_validation_error}",
                            stage_name=current_stage,
                        )
                    )
                    state.failed = True
                if not state.failed:
                    state.next_index += 1
                if state.next_index >= state.total_stages or state.failed:
                    if doc_bar and state.total_stages:
                        doc_bar.update(1)
                state.event.set()
                state.event = asyncio.Event()

        async def worker() -> None:
            while True:
                if shutdown_event.is_set():
                    await drain_queue(task_queue, drain_lock)
                    return
                await await_key_pool_ready()
                try:
                    task = task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await run_task(task)
                task_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_concurrency)]
        await wait_for_queue(task_queue, drain_lock)
        for w in workers:
            w.cancel()

    async def run_multi_stage_dag(client: httpx.AsyncClient) -> None:
        metadata_fields = [
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
        ]
        force_stage_set = set(force_stages or [])
        stage_dependencies = _resolve_stage_dependencies(stage_definitions)
        dependents_map = _build_dependency_graph(stage_definitions, stage_dependencies)
        stage_index_map = {stage_def.name: idx for idx, stage_def in enumerate(stage_definitions)}
        stage_fields_map = {
            stage_def.name: metadata_fields + stage_def.fields for stage_def in stage_definitions
        }
        stage_schema_map: dict[str, dict[str, Any]] = {}
        stage_validator_map: dict[str, Draft7Validator] = {}
        prompt_hash_map: dict[str, str] = {}
        for stage_def in stage_definitions:
            stage_name = stage_def.name
            stage_schema = build_stage_schema(schema, stage_fields_map[stage_name])
            stage_schema_map[stage_name] = stage_schema
            stage_validator_map[stage_name] = validate_schema(stage_schema)
            prompt_hash_map[stage_name] = _compute_prompt_hash(
                prompt_template=prompt_template,
                output_language=output_language,
                stage_name=stage_name,
                stage_fields=stage_fields_map[stage_name],
                custom_prompt=custom_prompt,
                prompt_system_path=prompt_system_path,
                prompt_user_path=prompt_user_path,
            )

        total_reused = 0
        fully_completed = 0

        doc_contexts: dict[Path, DocContext] = {}
        doc_states: dict[Path, DocDagState] = {}
        task_queue: asyncio.Queue[DocTask] = asyncio.Queue()
        drain_lock = asyncio.Lock()

        async def enqueue_ready(path: Path, stage_names: Iterable[str]) -> None:
            if shutdown_event.is_set():
                return
            state = doc_states[path]
            for stage_name in stage_names:
                async with state.lock:
                    if state.failed:
                        continue
                    if stage_name in state.completed or stage_name in state.in_flight:
                        continue
                    dependencies = stage_dependencies.get(stage_name, [])
                    if any(dep not in state.completed for dep in dependencies):
                        continue
                    state.in_flight.add(stage_name)
                task_queue.put_nowait(
                    DocTask(
                        path=path,
                        stage_index=stage_index_map[stage_name],
                        stage_name=stage_name,
                        stage_fields=stage_fields_map[stage_name],
                    )
                )

        for path in markdown_files:
            if shutdown_event.is_set():
                break
            source_path = str(path.resolve())
            if verbose:
                logger.debug("Preparing %s", source_path)
            content = read_text(path)
            await stats.add_input_chars(len(content))
            source_hash = compute_source_hash(content)
            truncated_content, truncation = truncate_content(
                content, config.extract.truncate_max_chars, config.extract.truncate_strategy
            )
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
                    "stage_meta": {},
                }
            stages: dict[str, dict[str, Any]] = stage_state.get("stages", {})
            stage_meta: dict[str, dict[str, Any]] = stage_state.get("stage_meta", {})
            doc_contexts[path] = DocContext(
                path=path,
                source_path=source_path,
                content=content,
                truncated_content=truncated_content,
                truncation=truncation,
                source_hash=source_hash,
                stage_path=stage_path,
                stage_state=stage_state,
                stages=stages,
                stage_meta=stage_meta,
            )
            doc_states[path] = DocDagState(
                total_stages=len(stage_definitions),
                remaining=len(stage_definitions),
            )
            state = doc_states[path]
            is_retry_full = source_path in retry_full_paths
            retry_stages = (
                retry_stage_map.get(source_path)
                if retry_stages_mode and not is_retry_full
                else None
            )
            reused_count = 0
            for stage_def in stage_definitions:
                stage_name = stage_def.name
                stage_record = stages.get(stage_name)
                if (
                    retry_stages_mode
                    and not is_retry_full
                    and retry_stages is not None
                    and stage_name not in retry_stages
                    and stage_record is not None
                ):
                    state.completed.add(stage_name)
                    state.remaining -= 1
                    reused_count += 1
                    continue

                stage_meta_entry = stage_meta.get(stage_name, {})
                needs_run = force or stage_name in force_stage_set
                if stage_record is None:
                    needs_run = True
                if is_retry_full:
                    needs_run = True
                if retry_stages is not None and stage_name in retry_stages:
                    needs_run = True
                if stage_meta_entry.get("prompt_hash") != prompt_hash_map[stage_name]:
                    needs_run = True
                if stage_record is not None and not needs_run:
                    errors_in_stage = sorted(
                        stage_validator_map[stage_name].iter_errors(stage_record),
                        key=lambda e: e.path,
                    )
                    if errors_in_stage:
                        needs_run = True

                if not needs_run:
                    state.completed.add(stage_name)
                    state.remaining -= 1
                    reused_count += 1

            if reused_count:
                total_reused += reused_count
                if stage_bar:
                    stage_bar.update(reused_count)

            if state.remaining == 0:
                state.finalized = True
                if doc_bar:
                    doc_bar.update(1)
                await update_results(doc_contexts[path])
                merged = build_merged(doc_contexts[path])
                errors_in_doc = sorted(validator.iter_errors(merged), key=lambda e: e.path)
                if errors_in_doc:
                    errors.append(
                        ExtractionError(
                            path=path,
                            provider=provider.name,
                            model=model,
                            error_type="validation_error",
                            error_message=f"Schema validation failed: {errors_in_doc[0].message}",
                            stage_name=stage_definitions[-1].name if stage_definitions else None,
                        )
                    )
                    state.failed = True
                fully_completed += 1
                continue

            await enqueue_ready(path, [stage_def.name for stage_def in stage_definitions])

        if total_reused:
            logger.info(
                "DAG precheck reused %d/%d stages",
                total_reused,
                len(markdown_files) * len(stage_definitions),
            )
        if fully_completed:
            logger.info("DAG precheck fully satisfied %d docs (no stages queued)", fully_completed)

        async def finalize_stage(
            ctx: DocContext,
            stage_name: str,
            *,
            failed: bool,
        ) -> None:
            state = doc_states[ctx.path]
            skip_count = 0
            doc_done = False
            is_failed = False
            async with state.lock:
                state.in_flight.discard(stage_name)
                if stage_name not in state.completed:
                    state.completed.add(stage_name)
                    state.remaining -= 1
                if failed and not state.failed:
                    state.failed = True
                    skip_count = state.remaining - len(state.in_flight)
                    if skip_count < 0:
                        skip_count = 0
                    state.remaining -= skip_count
                if state.remaining == 0 and not state.finalized:
                    state.finalized = True
                    doc_done = True
                is_failed = state.failed

            if stage_bar:
                stage_bar.update(1)
                if skip_count:
                    stage_bar.update(skip_count)

            if not is_failed:
                await enqueue_ready(ctx.path, dependents_map.get(stage_name, []))

            if doc_done:
                if doc_bar:
                    doc_bar.update(1)
                if not is_failed:
                    merged = build_merged(ctx)
                    errors_in_doc = sorted(validator.iter_errors(merged), key=lambda e: e.path)
                    if errors_in_doc:
                        errors.append(
                            ExtractionError(
                                path=ctx.path,
                                provider=provider.name,
                                model=model,
                                error_type="validation_error",
                                error_message=f"Schema validation failed: {errors_in_doc[0].message}",
                                stage_name=stage_name,
                            )
                        )
                        async with state.lock:
                            state.failed = True

        async def run_task(task: DocTask) -> None:
            ctx = doc_contexts[task.path]
            state = doc_states[task.path]
            current_stage = task.stage_name

            if shutdown_event.is_set():
                await finalize_stage(ctx, current_stage, failed=False)
                return
            async with state.lock:
                if state.failed:
                    state.in_flight.discard(current_stage)
            if state.failed:
                await finalize_stage(ctx, current_stage, failed=False)
                return

            is_retry_full = ctx.source_path in retry_full_paths
            retry_stages = (
                retry_stage_map.get(ctx.source_path)
                if retry_stages_mode and not is_retry_full
                else None
            )
            stage_record = ctx.stages.get(current_stage)
            if (
                retry_stages_mode
                and not is_retry_full
                and retry_stages is not None
                and current_stage not in retry_stages
                and stage_record is not None
            ):
                await update_results(ctx)
                await finalize_stage(ctx, current_stage, failed=False)
                return

            prompt_hash = _compute_prompt_hash(
                prompt_template=prompt_template,
                output_language=output_language,
                stage_name=current_stage,
                stage_fields=task.stage_fields,
                custom_prompt=custom_prompt,
                prompt_system_path=prompt_system_path,
                prompt_user_path=prompt_user_path,
            )
            stage_schema = build_stage_schema(schema, task.stage_fields)
            stage_validator = validate_schema(stage_schema)
            stage_meta = ctx.stage_meta.get(current_stage, {})
            needs_run = force or current_stage in force_stage_set
            if stage_record is None:
                needs_run = True
            if is_retry_full:
                needs_run = True
            if retry_stages is not None and current_stage in retry_stages:
                needs_run = True
            if stage_meta.get("prompt_hash") != prompt_hash:
                needs_run = True
            if stage_record is not None and not needs_run:
                errors_in_stage = sorted(
                    stage_validator.iter_errors(stage_record), key=lambda e: e.path
                )
                if errors_in_stage:
                    needs_run = True

            if not needs_run:
                await update_results(ctx)
                await finalize_stage(ctx, current_stage, failed=False)
                return

            try:
                dependencies = stage_dependencies.get(current_stage, [])
                previous_payload = {
                    dep: ctx.stages.get(dep) for dep in dependencies if dep in ctx.stages
                }
                previous_outputs = (
                    json.dumps(previous_payload, ensure_ascii=False) if previous_payload else ""
                )
                messages = build_messages(
                    ctx.truncated_content,
                    stage_schema,
                    provider,
                    prompt_template,
                    output_language,
                    custom_prompt=False,
                    prompt_system_path=None,
                    prompt_user_path=None,
                    stage_name=current_stage,
                    stage_fields=task.stage_fields,
                    previous_outputs=previous_outputs,
                )
                async with semaphore:
                    data = await call_with_retries(
                        provider,
                        model,
                        messages,
                        stage_schema,
                        None,
                        timeout=timeout_seconds,
                        structured_mode=provider.structured_mode,
                        max_retries=config.extract.max_retries,
                        backoff_base_seconds=config.extract.backoff_base_seconds,
                        backoff_max_seconds=config.extract.backoff_max_seconds,
                        client=client,
                        validator=stage_validator,
                        key_rotator=rotator,
                        throttle=throttle,
                        stats=stats,
                    )
                async with state.lock:
                    ctx.stages[current_stage] = data
                    ctx.stage_meta[current_stage] = {"prompt_hash": prompt_hash}
                    ctx.stage_state["stages"] = ctx.stages
                    ctx.stage_state["stage_meta"] = ctx.stage_meta
                    write_json_atomic(ctx.stage_path, ctx.stage_state)
                await update_results(ctx)
                await finalize_stage(ctx, current_stage, failed=False)
            except ProviderError as exc:
                log_extraction_failure(
                    ctx.source_path,
                    exc.error_type,
                    str(exc),
                    status_code=exc.status_code,
                )
                errors.append(
                    ExtractionError(
                        path=task.path,
                        provider=provider.name,
                        model=model,
                        error_type=exc.error_type,
                        error_message=str(exc),
                        stage_name=current_stage,
                    )
                )
                await finalize_stage(ctx, current_stage, failed=True)
            except Exception as exc:  # pragma: no cover - safety net
                logger.exception("Unexpected error while processing %s", ctx.source_path)
                errors.append(
                    ExtractionError(
                        path=task.path,
                        provider=provider.name,
                        model=model,
                        error_type="unexpected_error",
                        error_message=str(exc),
                        stage_name=current_stage,
                    )
                )
                await finalize_stage(ctx, current_stage, failed=True)

        async def worker() -> None:
            while True:
                if shutdown_event.is_set():
                    await drain_queue(task_queue, drain_lock)
                    return
                await await_key_pool_ready()
                try:
                    task = task_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await run_task(task)
                task_queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_concurrency)]
        await wait_for_queue(task_queue, drain_lock)
        for w in workers:
            w.cancel()

    try:
        async with httpx.AsyncClient() as client:
            if multi_stage:
                if stage_dag_enabled:
                    await run_multi_stage_dag(client)
                else:
                    await run_multi_stage(client)
            else:
                await run_single_stage(client)
    finally:
        if pause_task:
            pause_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pause_task
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
        elif path:
            final_results.append(entry)
            seen.add(path)

    for path, entry in results.items():
        if path not in seen:
            final_results.append(entry)

    output_payload = {"template_tag": template_tag, "papers": final_results}
    write_json(output_path, output_payload)

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

    if shutdown_event.is_set():
        logger.warning(
            "Graceful shutdown completed (%s)",
            shutdown_reason or "signal",
        )

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
            write_json(target_dir / f"{file_name}.json", {"template_tag": template_tag, "papers": [entry]})

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

    duration = time.monotonic() - start_time
    prompt_tokens = _estimate_tokens_for_chars(stats.prompt_chars)
    completion_tokens = _estimate_tokens_for_chars(stats.output_chars)
    total_tokens = prompt_tokens + completion_tokens
    doc_count = len(markdown_files)
    avg_time = duration / doc_count if doc_count else 0.0
    docs_per_min = (doc_count / duration) * 60 if duration > 0 else 0.0
    tokens_per_sec = (total_tokens / duration) if duration > 0 else 0.0

    table = Table(
        title="paper extract summary",
        header_style="bold cyan",
        title_style="bold magenta",
    )
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold")
    table.add_row("Documents", f"{doc_count} total")
    table.add_row("Successful", str(doc_count - len(errors)))
    failed_stage_count = sum(1 for err in errors if err.stage_name)
    retried_stage_count = 0
    if retry_stages_mode:
        retried_stage_count = sum(
            len(retry_stage_map.get(str(path.resolve()), set())) for path in markdown_files
        )
    table.add_row("Errors", str(len(errors)))
    table.add_row("Failed stages", str(failed_stage_count))
    table.add_row("Retried stages", str(retried_stage_count))
    table.add_row("Output JSON", str(output_path))
    table.add_row("Errors JSON", str(errors_path))
    table.add_row("Duration", _format_duration(duration))
    table.add_row("Avg time/doc", _format_duration(avg_time))
    table.add_row("Throughput", _format_rate(docs_per_min, "docs/min"))
    table.add_row("Input chars", str(stats.input_chars))
    table.add_row("Prompt chars", str(stats.prompt_chars))
    table.add_row("Output chars", str(stats.output_chars))
    table.add_row("Est prompt tokens", str(prompt_tokens))
    table.add_row("Est completion tokens", str(completion_tokens))
    table.add_row("Est total tokens", str(total_tokens))
    table.add_row("Est tokens/sec", _format_rate(tokens_per_sec, "tok/s"))
    Console().print(table)
