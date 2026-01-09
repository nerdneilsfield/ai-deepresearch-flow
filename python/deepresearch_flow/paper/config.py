"""Configuration loading and validation for paper tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import tomllib


@dataclass(frozen=True)
class ExtractConfig:
    output: str
    errors: str
    max_concurrency: int
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    truncate_strategy: str
    truncate_max_chars: int
    cost_estimate: bool
    schema_path: str | None


@dataclass(frozen=True)
class RenderConfig:
    template_path: str | None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base_url: str
    api_keys: list[str]
    structured_mode: str
    extra_headers: dict[str, str]
    system_prompt: str | None
    user_prompt: str | None
    model_list: list[str] | None


@dataclass(frozen=True)
class PaperConfig:
    extract: ExtractConfig
    render: RenderConfig
    providers: list[ProviderConfig]


DEFAULT_EXTRACT = ExtractConfig(
    output="paper_infos.json",
    errors="paper_errors.json",
    max_concurrency=6,
    max_retries=3,
    backoff_base_seconds=1.0,
    backoff_max_seconds=20.0,
    truncate_strategy="head_tail",
    truncate_max_chars=20000,
    cost_estimate=True,
    schema_path=None,
)

DEFAULT_RENDER = RenderConfig(template_path=None)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _as_str(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    return str(value)


def load_config(path: str) -> PaperConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))

    extract_data = data.get("extract", {})
    extract = ExtractConfig(
        output=_as_str(extract_data.get("output"), DEFAULT_EXTRACT.output) or DEFAULT_EXTRACT.output,
        errors=_as_str(extract_data.get("errors"), DEFAULT_EXTRACT.errors) or DEFAULT_EXTRACT.errors,
        max_concurrency=_as_int(extract_data.get("max_concurrency"), DEFAULT_EXTRACT.max_concurrency),
        max_retries=_as_int(extract_data.get("max_retries"), DEFAULT_EXTRACT.max_retries),
        backoff_base_seconds=_as_float(
            extract_data.get("backoff_base_seconds"), DEFAULT_EXTRACT.backoff_base_seconds
        ),
        backoff_max_seconds=_as_float(
            extract_data.get("backoff_max_seconds"), DEFAULT_EXTRACT.backoff_max_seconds
        ),
        truncate_strategy=_as_str(
            extract_data.get("truncate_strategy"), DEFAULT_EXTRACT.truncate_strategy
        )
        or DEFAULT_EXTRACT.truncate_strategy,
        truncate_max_chars=_as_int(
            extract_data.get("truncate_max_chars"), DEFAULT_EXTRACT.truncate_max_chars
        ),
        cost_estimate=_as_bool(extract_data.get("cost_estimate"), DEFAULT_EXTRACT.cost_estimate),
        schema_path=_as_str(extract_data.get("schema_path"), DEFAULT_EXTRACT.schema_path),
    )

    render_data = data.get("render", {})
    render = RenderConfig(template_path=_as_str(render_data.get("template_path"), DEFAULT_RENDER.template_path))

    providers_data = data.get("providers", [])
    providers: list[ProviderConfig] = []
    for provider in providers_data:
        name = _as_str(provider.get("name"))
        provider_type = _as_str(provider.get("type"))
        if not name or not provider_type:
            raise ValueError("Each provider must include name and type")

        base_url = _as_str(provider.get("base_url"))
        if not base_url:
            if provider_type == "ollama":
                base_url = "http://localhost:11434"
            elif provider_type == "openai_compatible":
                base_url = "https://api.openai.com/v1"
            else:
                raise ValueError(f"Provider '{name}' requires base_url")

        api_keys = _as_list(provider.get("api_keys"))
        if not api_keys:
            api_key_single = provider.get("api_key")
            api_keys = _as_list(api_key_single)

        structured_mode = _as_str(provider.get("structured_mode"), None)
        if structured_mode is None:
            structured_mode = "json_object" if provider_type == "ollama" else "json_schema"

        extra_headers: dict[str, str] = {}
        headers = provider.get("extra_headers")
        if isinstance(headers, dict):
            extra_headers = {str(k): str(v) for k, v in headers.items()}

        providers.append(
            ProviderConfig(
                name=name,
                type=provider_type,
                base_url=base_url,
                api_keys=api_keys,
                structured_mode=structured_mode,
                extra_headers=extra_headers,
                system_prompt=_as_str(provider.get("system_prompt"), None),
                user_prompt=_as_str(provider.get("user_prompt"), None),
                model_list=_as_list(provider.get("model_list")) or None,
            )
        )

    if not providers:
        raise ValueError("Config must include at least one [[providers]] entry")

    return PaperConfig(extract=extract, render=render, providers=providers)


def resolve_api_keys(entries: list[str]) -> list[str]:
    resolved: list[str] = []
    for entry in entries:
        entry = str(entry)
        if entry.startswith("env:"):
            env_name = entry.split(":", 1)[1]
            value = os.environ.get(env_name)
            if value:
                resolved.append(value)
        else:
            resolved.append(entry)
    return resolved
