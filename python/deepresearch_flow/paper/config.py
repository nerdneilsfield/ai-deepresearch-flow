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
    timeout: float
    backoff_base_seconds: float
    backoff_max_seconds: float
    pause_threshold_seconds: float
    truncate_strategy: str
    truncate_max_chars: int
    cost_estimate: bool
    schema_path: str | None
    stage_dag: bool


@dataclass(frozen=True)
class RenderConfig:
    template_path: str | None


@dataclass(frozen=True)
class ApiKeyConfig:
    key: str
    quota_duration: int | None
    reset_time: str | None
    quota_error_tokens: list[str]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base_url: str
    api_keys: list[ApiKeyConfig]
    api_version: str | None
    deployment: str | None
    project_id: str | None
    location: str | None
    credentials_path: str | None
    anthropic_version: str | None
    max_tokens: int | None
    structured_mode: str
    extra_headers: dict[str, str]
    system_prompt: str | None
    user_prompt: str | None
    model_list: list[str]


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
    timeout=60.0,
    backoff_base_seconds=1.0,
    backoff_max_seconds=20.0,
    pause_threshold_seconds=10.0,
    truncate_strategy="head_tail",
    truncate_max_chars=20000,
    cost_estimate=True,
    schema_path=None,
    stage_dag=False,
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


def _parse_api_keys(value: Any) -> list[ApiKeyConfig]:
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    parsed: list[ApiKeyConfig] = []
    for entry in entries:
        if isinstance(entry, dict):
            key = _as_str(entry.get("key"))
            if not key:
                raise ValueError("api_keys object entries must include key")
            quota_duration = entry.get("quota_duration")
            quota_duration_value = int(quota_duration) if quota_duration is not None else None
            if quota_duration_value is not None and quota_duration_value <= 0:
                raise ValueError("quota_duration must be positive seconds")
            reset_time = _as_str(entry.get("reset_time"), None)
            tokens = entry.get("quota_error_tokens")
            if tokens is None:
                quota_error_tokens = []
            elif isinstance(tokens, list):
                quota_error_tokens = [str(token) for token in tokens]
            else:
                quota_error_tokens = [str(tokens)]
            parsed.append(
                ApiKeyConfig(
                    key=key,
                    quota_duration=quota_duration_value,
                    reset_time=reset_time,
                    quota_error_tokens=quota_error_tokens,
                )
            )
        else:
            key = _as_str(entry)
            if not key:
                continue
            parsed.append(
                ApiKeyConfig(
                    key=key,
                    quota_duration=None,
                    reset_time=None,
                    quota_error_tokens=[],
                )
            )
    return parsed


def _ensure_http_scheme(base_url: str, *, default_scheme: str = "http://") -> str:
    normalized = base_url.strip()
    if normalized.startswith(("http://", "https://")):
        scheme, rest = normalized.split("://", 1)
        rest = rest.lstrip("/")
        return f"{scheme}://{rest}" if rest else f"{scheme}://"
    return f"{default_scheme}{normalized.lstrip('/')}"


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
        timeout=_as_float(extract_data.get("timeout"), DEFAULT_EXTRACT.timeout),
        backoff_base_seconds=_as_float(
            extract_data.get("backoff_base_seconds"), DEFAULT_EXTRACT.backoff_base_seconds
        ),
        backoff_max_seconds=_as_float(
            extract_data.get("backoff_max_seconds"), DEFAULT_EXTRACT.backoff_max_seconds
        ),
        pause_threshold_seconds=_as_float(
            extract_data.get("pause_threshold_seconds"), DEFAULT_EXTRACT.pause_threshold_seconds
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
        stage_dag=_as_bool(extract_data.get("stage_dag"), DEFAULT_EXTRACT.stage_dag),
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
        endpoint = _as_str(provider.get("endpoint"))
        if not base_url:
            if provider_type == "ollama":
                base_url = "http://localhost:11434"
            elif provider_type == "openai_compatible":
                base_url = "https://api.openai.com/v1"
            elif provider_type == "azure_openai" and endpoint:
                base_url = endpoint
            elif provider_type in {"dashscope", "gemini_ai_studio", "gemini_vertex", "claude"}:
                base_url = ""
            else:
                raise ValueError(f"Provider '{name}' requires base_url")
        elif provider_type == "azure_openai" and endpoint:
            base_url = endpoint
        if provider_type == "ollama" and base_url:
            base_url = _ensure_http_scheme(base_url)

        api_keys = _parse_api_keys(provider.get("api_keys"))
        if not api_keys:
            api_key_single = provider.get("api_key")
            api_keys = _parse_api_keys(api_key_single)

        structured_mode = _as_str(provider.get("structured_mode"), None)
        if structured_mode is None:
            if provider_type == "ollama":
                structured_mode = "json_object"
            elif provider_type in {"dashscope", "gemini_ai_studio", "gemini_vertex", "claude"}:
                structured_mode = "none"
            else:
                structured_mode = "json_schema"

        extra_headers: dict[str, str] = {}
        headers = provider.get("extra_headers")
        if isinstance(headers, dict):
            extra_headers = {str(k): str(v) for k, v in headers.items()}

        model_list = _as_list(provider.get("model_list"))
        if not model_list:
            raise ValueError(f"Provider '{name}' must include model_list")

        api_version = _as_str(provider.get("api_version"), None)
        deployment = _as_str(provider.get("deployment"), None)
        project_id = _as_str(provider.get("project_id"), None)
        location = _as_str(provider.get("location"), None)
        credentials_path = _as_str(provider.get("credentials_path"), None)
        anthropic_version = _as_str(provider.get("anthropic_version"), None)
        max_tokens = provider.get("max_tokens")
        max_tokens_value = int(max_tokens) if max_tokens is not None else None

        if provider_type == "azure_openai":
            if not base_url:
                raise ValueError(f"Provider '{name}' requires endpoint")
            if not api_version:
                raise ValueError(f"Provider '{name}' requires api_version")
            if not deployment:
                raise ValueError(f"Provider '{name}' requires deployment")
        if provider_type == "gemini_ai_studio" and not api_keys:
            raise ValueError(f"Provider '{name}' requires api_keys")
        if provider_type == "gemini_vertex":
            if not project_id:
                raise ValueError(f"Provider '{name}' requires project_id")
            if not location:
                raise ValueError(f"Provider '{name}' requires location")
        if provider_type == "claude":
            if not api_keys:
                raise ValueError(f"Provider '{name}' requires api_keys")
            if not anthropic_version:
                raise ValueError(f"Provider '{name}' requires anthropic_version")

        providers.append(
            ProviderConfig(
                name=name,
                type=provider_type,
                base_url=base_url,
                api_keys=api_keys,
                api_version=api_version,
                deployment=deployment,
                project_id=project_id,
                location=location,
                credentials_path=credentials_path,
                anthropic_version=anthropic_version,
                max_tokens=max_tokens_value,
                structured_mode=structured_mode,
                extra_headers=extra_headers,
                system_prompt=_as_str(provider.get("system_prompt"), None),
                user_prompt=_as_str(provider.get("user_prompt"), None),
                model_list=model_list,
            )
        )

    if not providers:
        raise ValueError("Config must include at least one [[providers]] entry")

    return PaperConfig(extract=extract, render=render, providers=providers)


def resolve_api_key_configs(entries: list[ApiKeyConfig]) -> list[ApiKeyConfig]:
    resolved: list[ApiKeyConfig] = []
    for entry in entries:
        key = entry.key
        if key.startswith("env:"):
            env_name = key.split(":", 1)[1]
            value = os.environ.get(env_name)
            if not value:
                continue
            key = value
        resolved.append(
            ApiKeyConfig(
                key=key,
                quota_duration=entry.quota_duration,
                reset_time=entry.reset_time,
                quota_error_tokens=entry.quota_error_tokens,
            )
        )
    return resolved


def resolve_api_keys(entries: list[ApiKeyConfig]) -> list[str]:
    return [entry.key for entry in resolve_api_key_configs(entries)]
