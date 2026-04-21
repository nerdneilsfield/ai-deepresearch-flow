"""Configuration loading and validation for paper tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from deepresearch_flow.paper.active_window import parse_windows


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
class KeyConfig:
    value: str
    weight: int
    quota_duration: int | None = None
    reset_time: str | None = None
    quota_error_tokens: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Compatibility alias for older call sites."""
        return self.value


ApiKeyConfig = KeyConfig


@dataclass(frozen=True)
class BaseConfig:
    url: str
    weight: int
    key: list[KeyConfig]
    active_windows: list[str] = field(default_factory=list)
    active_timezone: str | None = None


@dataclass(frozen=True)
class ModelCapability:
    model_name: str
    is_stream: bool
    is_support_json_schema: bool
    is_support_json_object: bool

    def __init__(
        self,
        model_name: str,
        is_stream: bool,
        is_support_json_schema: bool,
        is_support_json_object: bool,
        **_: Any,
    ) -> None:
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "is_stream", is_stream)
        object.__setattr__(self, "is_support_json_schema", is_support_json_schema)
        object.__setattr__(self, "is_support_json_object", is_support_json_object)


@dataclass(frozen=True)
class MainModelConfig:
    model: str
    weight: int


@dataclass(frozen=True)
class EmbeddingModelConfig:
    model_name: str
    canonical_name: str
    dimensions: int
    max_context: int

    def __init__(
        self,
        model_name: str,
        dimensions: int,
        max_context: int,
        canonical_name: str | None = None,
        **_: Any,
    ) -> None:
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "canonical_name", (canonical_name or model_name).strip() or model_name)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "max_context", max_context)

    @property
    def is_support_json_schema(self) -> bool:
        return False

    @property
    def is_support_json_object(self) -> bool:
        return False


@dataclass(frozen=True)
class EmbeddingProviderConfig:
    name: str
    type: str
    base: list[BaseConfig]
    models: list[EmbeddingModelConfig]


@dataclass(frozen=True)
class EmbeddingConfig:
    default_model: str
    default_provider: str
    dimensions: int
    normalized: bool
    batch_size: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    providers: list[EmbeddingProviderConfig]

    def __init__(
        self,
        *,
        default_model: str | None = None,
        default_provider: str | None = None,
        dimensions: int = 1024,
        normalized: bool = True,
        batch_size: int = 32,
        chunk_max_tokens: int = 512,
        chunk_overlap_tokens: int = 64,
        providers: list[EmbeddingProviderConfig] | None = None,
        model: str | None = None,
        provider: str | None = None,
        **_: Any,
    ) -> None:
        object.__setattr__(self, "default_model", default_model or model or "")
        object.__setattr__(self, "default_provider", default_provider or provider or "")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "normalized", normalized)
        object.__setattr__(self, "batch_size", batch_size)
        object.__setattr__(self, "chunk_max_tokens", chunk_max_tokens)
        object.__setattr__(self, "chunk_overlap_tokens", chunk_overlap_tokens)
        object.__setattr__(self, "providers", providers or [])

    @property
    def model(self) -> str:
        """Compatibility alias for older call sites."""
        return self.default_model

    @property
    def provider(self) -> str:
        """Compatibility alias for older call sites."""
        return self.default_provider

    def resolve_active(self) -> tuple[EmbeddingProviderConfig, EmbeddingModelConfig]:
        active_provider = next(
            (provider for provider in self.providers if provider.name == self.default_provider),
            None,
        )
        if active_provider is None:
            raise ValueError(f"Embedding provider '{self.default_provider}' not found")

        active_model = next(
            (model for model in active_provider.models if model.model_name == self.default_model),
            None,
        )
        if active_model is None:
            raise ValueError(
                f"Model '{self.default_model}' not found in embedding provider '{self.default_provider}'"
            )
        if self.dimensions > active_model.dimensions:
            raise ValueError(
                f"Requested embedding dimensions ({self.dimensions}) exceed "
                f"model {active_model.model_name} max dimensions ({active_model.dimensions})"
            )
        if self.chunk_max_tokens > active_model.max_context:
            raise ValueError(
                f"chunk_max_tokens ({self.chunk_max_tokens}) exceeds "
                f"model {active_model.model_name} max_context ({active_model.max_context})"
            )
        return active_provider, active_model


@dataclass(frozen=True)
class RerankModelConfig:
    model_name: str
    max_context: int
    max_chunks_per_doc: int | None = None
    instruction: str | None = None

    @property
    def is_support_json_schema(self) -> bool:
        return False

    @property
    def is_support_json_object(self) -> bool:
        return False


@dataclass(frozen=True)
class RerankProviderConfig:
    name: str
    type: str
    base: list[BaseConfig]
    models: list[RerankModelConfig]


@dataclass(frozen=True)
class RerankConfig:
    enabled: bool
    default_model: str
    default_provider: str
    top_n: int
    providers: list[RerankProviderConfig]

    def __init__(
        self,
        enabled: bool = False,
        *,
        default_model: str | None = None,
        default_provider: str | None = None,
        top_n: int = 10,
        providers: list[RerankProviderConfig] | None = None,
        model: str | None = None,
        provider: str | None = None,
        **_: Any,
    ) -> None:
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "default_model", default_model or model or "")
        object.__setattr__(self, "default_provider", default_provider or provider or "")
        object.__setattr__(self, "top_n", top_n)
        object.__setattr__(self, "providers", providers or [])

    @property
    def model(self) -> str:
        """Compatibility alias for older call sites."""
        return self.default_model

    @property
    def provider(self) -> str:
        """Compatibility alias for older call sites."""
        return self.default_provider

    def resolve_active(self) -> tuple[RerankProviderConfig, RerankModelConfig]:
        active_provider = next(
            (provider for provider in self.providers if provider.name == self.default_provider),
            None,
        )
        if active_provider is None:
            raise ValueError(f"Rerank provider '{self.default_provider}' not found")

        active_model = next(
            (model for model in active_provider.models if model.model_name == self.default_model),
            None,
        )
        if active_model is None:
            raise ValueError(
                f"Model '{self.default_model}' not found in rerank provider '{self.default_provider}'"
            )
        return active_provider, active_model


@dataclass(frozen=True)
class SearchConfig:
    vector_dir: str
    vector_top_k: int
    keyword_top_k: int
    hybrid: bool
    access_token: str | None = None
    advanced_enabled: bool = False
    advanced_rrf_k: int = 60
    advanced_dense_top_k: int = 50
    advanced_sparse_top_k: int = 30
    advanced_post_fusion_top_k: int = 50
    advanced_dedup_cosine_threshold: float = 0.95
    advanced_rerank_top_n: int = 20
    advanced_mmr_lambda_default: float = 0.6
    advanced_rerank_timeout_ms: int = 1500
    advanced_top_n_max: int = 50
    advanced_max_query_length: int = 500


@dataclass(frozen=True)
class TranslatorConfig:
    model: str | None = None
    fallback_model: str | None = None
    fallback_model_2: str | None = None
    document_window: int | None = None
    initial_workers: int | None = None
    retry_workers: int | None = None
    fallback_workers: int | None = None
    fallback_2_workers: int | None = None
    main_concurrency: int | None = None
    fallback_concurrency: int | None = None
    fallback_2_concurrency: int | None = None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base: list[BaseConfig]
    models: list[ModelCapability]
    api_version: str | None
    deployment: str | None
    project_id: str | None
    location: str | None
    credentials_path: str | None
    anthropic_version: str | None
    max_tokens: int | None
    extra_headers: dict[str, str]
    system_prompt: str | None
    user_prompt: str | None

    @property
    def base_url(self) -> str:
        """Compatibility alias for older call sites."""
        return self.base[0].url

    @property
    def api_keys(self) -> list[KeyConfig]:
        """Compatibility alias for older call sites."""
        return [key for base in self.base for key in base.key]

    @property
    def model_list(self) -> list[str]:
        """Compatibility alias for older call sites."""
        return [model.model_name for model in self.models]


@dataclass(frozen=True)
class PaperConfig:
    extract: ExtractConfig
    render: RenderConfig
    providers: list[ProviderConfig]
    main_model: list[MainModelConfig]
    embedding: EmbeddingConfig | None = None
    rerank: RerankConfig | None = None
    search: SearchConfig | None = None
    translator_config: TranslatorConfig | None = None


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

_LEGACY_PROVIDER_FIELDS = {
    "api_key",
    "api_keys",
    "base_url",
    "endpoint",
    "model_list",
    "structured_mode",
}


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


def resolve_key_value(raw_value: str) -> str:
    if raw_value.startswith("env:"):
        env_name = raw_value.split(":", 1)[1]
        resolved = os.environ.get(env_name)
        if not resolved:
            raise ValueError(f"Environment variable not set: {env_name}")
        return resolved
    return raw_value


def _validate_weight(value: Any, field_name: str) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if weight <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return weight


def _reject_legacy_provider_fields(provider: dict[str, Any], provider_name: str) -> None:
    for field_name in _LEGACY_PROVIDER_FIELDS:
        if field_name in provider:
            raise ValueError(
                f"Provider '{provider_name}' uses legacy provider field '{field_name}'; "
                "use the weighted base/models structure instead"
            )


def _parse_key_config(value: Any, field_name: str) -> KeyConfig:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} entries must be objects")
    raw_value = _as_str(value.get("value"))
    if not raw_value:
        raise ValueError(f"{field_name} entries must include value")
    resolve_key_value(raw_value)

    quota_duration = value.get("quota_duration")
    quota_duration_value = int(quota_duration) if quota_duration is not None else None
    if quota_duration_value is not None and quota_duration_value <= 0:
        raise ValueError(f"{field_name}.quota_duration must be positive seconds")

    tokens = value.get("quota_error_tokens")
    if tokens is None:
        quota_error_tokens: list[str] = []
    elif isinstance(tokens, list):
        quota_error_tokens = [str(token) for token in tokens]
    else:
        quota_error_tokens = [str(tokens)]

    return KeyConfig(
        value=raw_value,
        weight=_validate_weight(value.get("weight"), f"{field_name}.weight"),
        quota_duration=quota_duration_value,
        reset_time=_as_str(value.get("reset_time"), None),
        quota_error_tokens=quota_error_tokens,
    )


def _parse_base_configs(value: Any, provider_path: str) -> list[BaseConfig]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{provider_path} must include non-empty base")

    parsed: list[BaseConfig] = []
    for idx, item in enumerate(value):
        field_name = f"{provider_path}.base[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        url = _as_str(item.get("url"))
        if not url:
            raise ValueError(f"{field_name} must include url")
        keys_raw = item.get("key")
        if not isinstance(keys_raw, list) or not keys_raw:
            raise ValueError(f"{field_name} must include non-empty key")
        raw_windows = item.get("active_windows")
        if raw_windows is None:
            active_windows: list[str] = []
        elif not isinstance(raw_windows, list):
            raise ValueError(f"{field_name}.active_windows must be a list of strings")
        else:
            active_windows = []
            for window_idx, raw_window in enumerate(raw_windows):
                if not isinstance(raw_window, str):
                    raise ValueError(f"{field_name}.active_windows[{window_idx}] must be a string")
                try:
                    parse_windows([raw_window])
                except ValueError as exc:
                    raise ValueError(f"{field_name}.active_windows[{window_idx}]: {exc}") from exc
                active_windows.append(raw_window)

        raw_timezone = item.get("active_timezone")
        if raw_timezone is None:
            active_timezone: str | None = None
        elif not isinstance(raw_timezone, str):
            raise ValueError(f"{field_name}.active_timezone must be a string")
        else:
            try:
                ZoneInfo(raw_timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"{field_name}.active_timezone: unknown timezone '{raw_timezone}'") from exc
            active_timezone = raw_timezone
        parsed.append(
            BaseConfig(
                url=url,
                weight=_validate_weight(item.get("weight"), f"{field_name}.weight"),
                key=[_parse_key_config(entry, f"{field_name}.key[{key_idx}]") for key_idx, entry in enumerate(keys_raw)],
                active_windows=active_windows,
                active_timezone=active_timezone,
            )
        )
    return parsed


def _parse_model_capabilities(value: Any, provider_name: str) -> list[ModelCapability]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Provider '{provider_name}' must include non-empty models")

    parsed: list[ModelCapability] = []
    for idx, item in enumerate(value):
        field_name = f"providers[{provider_name}].models[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        model_name = _as_str(item.get("model_name"))
        if not model_name:
            raise ValueError(f"{field_name} must include model_name")
        parsed.append(
            ModelCapability(
                model_name=model_name,
                is_stream=_as_bool(item.get("is_stream"), False),
                is_support_json_schema=_as_bool(item.get("is_support_json_schema"), False),
                is_support_json_object=_as_bool(item.get("is_support_json_object"), False),
            )
        )
    return parsed


def _parse_main_model(value: Any) -> list[MainModelConfig]:
    if not isinstance(value, list) or not value:
        raise ValueError("Config must include non-empty main_model")

    parsed: list[MainModelConfig] = []
    for idx, item in enumerate(value):
        field_name = f"main_model[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        model = _as_str(item.get("model"))
        if not model:
            raise ValueError(f"{field_name} must include model")
        parsed.append(
            MainModelConfig(
                model=model,
                weight=_validate_weight(item.get("weight"), f"{field_name}.weight"),
            )
        )
    return parsed


def _model_declared(providers: list[ProviderConfig], model_ref: str) -> bool:
    if "/" not in model_ref:
        return False
    provider_name, model_name = model_ref.split("/", 1)
    for provider in providers:
        if provider.name != provider_name:
            continue
        return any(model.model_name == model_name for model in provider.models)
    return False


def _parse_embedding_model_configs(
    value: Any, provider_name: str, default_dimensions: int
) -> list[EmbeddingModelConfig]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Embedding provider '{provider_name}' must include non-empty models")

    parsed: list[EmbeddingModelConfig] = []
    for idx, item in enumerate(value):
        field_name = f"embedding.providers[{provider_name}].models[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        model_name = _as_str(item.get("model_name"))
        if not model_name:
            raise ValueError(f"{field_name} must include model_name")
        parsed.append(
            EmbeddingModelConfig(
                model_name=model_name,
                canonical_name=_as_str(item.get("canonical_name"), model_name) or model_name,
                dimensions=_as_int(item.get("dimensions"), default_dimensions),
                max_context=_as_int(item.get("max_context"), 8192),
            )
        )
    return parsed


def _parse_embedding_provider_configs(value: Any, default_dimensions: int) -> list[EmbeddingProviderConfig]:
    if not isinstance(value, list) or not value:
        raise ValueError("Config [embedding] must include non-empty providers")

    parsed: list[EmbeddingProviderConfig] = []
    for idx, item in enumerate(value):
        field_name = f"embedding.providers[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        name = _as_str(item.get("name"))
        if not name:
            raise ValueError(f"{field_name} must include name")
        _reject_legacy_provider_fields(item, name)
        parsed.append(
            EmbeddingProviderConfig(
                name=name,
                type=_as_str(item.get("type"), "openai_compatible") or "openai_compatible",
                base=_parse_base_configs(item.get("base"), f"embedding.providers[{name}]"),
                models=_parse_embedding_model_configs(item.get("models"), name, default_dimensions),
            )
        )
    return parsed


def _parse_embedding_config(value: Any) -> EmbeddingConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config [embedding] must be an object")

    default_model = _as_str(value.get("default_model"))
    default_provider = _as_str(value.get("default_provider"))
    if not default_model or not default_provider:
        raise ValueError("Config [embedding] must include default_model and default_provider")

    dimensions = _as_int(value.get("dimensions"), 1024)
    return EmbeddingConfig(
        default_model=default_model,
        default_provider=default_provider,
        dimensions=dimensions,
        normalized=_as_bool(value.get("normalized"), True),
        batch_size=_as_int(value.get("batch_size"), 32),
        chunk_max_tokens=_as_int(value.get("chunk_max_tokens"), 512),
        chunk_overlap_tokens=_as_int(value.get("chunk_overlap_tokens"), 64),
        providers=_parse_embedding_provider_configs(value.get("providers"), dimensions),
    )


def _parse_rerank_model_configs(value: Any, provider_name: str) -> list[RerankModelConfig]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Rerank provider '{provider_name}' must include non-empty models")

    parsed: list[RerankModelConfig] = []
    for idx, item in enumerate(value):
        field_name = f"rerank.providers[{provider_name}].models[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        model_name = _as_str(item.get("model_name"))
        if not model_name:
            raise ValueError(f"{field_name} must include model_name")
        parsed.append(
            RerankModelConfig(
                model_name=model_name,
                max_context=_as_int(item.get("max_context"), 8192),
                max_chunks_per_doc=(
                    _as_int(item.get("max_chunks_per_doc"), 0)
                    if item.get("max_chunks_per_doc") is not None
                    else None
                ),
                instruction=_as_str(item.get("instruction"), None),
            )
        )
    return parsed


def _parse_rerank_provider_configs(value: Any, *, required: bool) -> list[RerankProviderConfig]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError("Config [rerank].providers must be an array")
    if required and not value:
        raise ValueError("Config [rerank] must include non-empty providers")
    if not value:
        return []

    parsed: list[RerankProviderConfig] = []
    for idx, item in enumerate(value):
        field_name = f"rerank.providers[{idx}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        name = _as_str(item.get("name"))
        if not name:
            raise ValueError(f"{field_name} must include name")
        _reject_legacy_provider_fields(item, name)
        parsed.append(
            RerankProviderConfig(
                name=name,
                type=_as_str(item.get("type"), "openai_compatible") or "openai_compatible",
                base=_parse_base_configs(item.get("base"), f"rerank.providers[{name}]"),
                models=_parse_rerank_model_configs(item.get("models"), name),
            )
        )
    return parsed


def _parse_rerank_config(value: Any) -> RerankConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config [rerank] must be an object")

    enabled = _as_bool(value.get("enabled"), False)
    default_model = _as_str(value.get("default_model"), "") or ""
    default_provider = _as_str(value.get("default_provider"), "") or ""
    if enabled and (not default_model or not default_provider):
        raise ValueError("Config [rerank] must include default_model and default_provider")

    return RerankConfig(
        enabled=enabled,
        default_model=default_model,
        default_provider=default_provider,
        top_n=_as_int(value.get("top_n"), 10),
        providers=_parse_rerank_provider_configs(value.get("providers"), required=enabled),
    )


def _parse_search_config(value: Any) -> SearchConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config [search] must be an object")

    advanced_enabled = _as_bool(value.get("advanced_enabled"), False)
    vector_dir = _as_str(value.get("vector_dir"))
    if not vector_dir and not advanced_enabled:
        raise ValueError("Config [search] must include vector_dir")

    return SearchConfig(
        vector_dir=vector_dir or "",
        vector_top_k=_as_int(value.get("vector_top_k"), 0),
        keyword_top_k=_as_int(value.get("keyword_top_k"), 0),
        hybrid=_as_bool(value.get("hybrid"), False),
        access_token=(
            resolve_key_value(raw_token)
            if (raw_token := _as_str(value.get("access_token"), None))
            else None
        ),
        advanced_enabled=advanced_enabled,
        advanced_rrf_k=_as_int(value.get("advanced_rrf_k"), 60),
        advanced_dense_top_k=_as_int(value.get("advanced_dense_top_k"), 50),
        advanced_sparse_top_k=_as_int(value.get("advanced_sparse_top_k"), 30),
        advanced_post_fusion_top_k=_as_int(
            value.get("advanced_post_fusion_top_k"), 50
        ),
        advanced_dedup_cosine_threshold=_as_float(
            value.get("advanced_dedup_cosine_threshold"), 0.95
        ),
        advanced_rerank_top_n=_as_int(value.get("advanced_rerank_top_n"), 20),
        advanced_mmr_lambda_default=_as_float(
            value.get("advanced_mmr_lambda_default"), 0.6
        ),
        advanced_rerank_timeout_ms=_as_int(
            value.get("advanced_rerank_timeout_ms"), 1500
        ),
        advanced_top_n_max=_as_int(value.get("advanced_top_n_max"), 50),
        advanced_max_query_length=_as_int(
            value.get("advanced_max_query_length"), 500
        ),
    )


def _parse_translator_config(value: Any) -> TranslatorConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config [translator_config] must be an object")

    def opt_int(name: str) -> int | None:
        raw = value.get(name)
        if raw is None:
            return None
        parsed = int(raw)
        if parsed <= 0:
            raise ValueError(f"Config [translator_config].{name} must be positive")
        return parsed

    return TranslatorConfig(
        model=_as_str(value.get("model"), None),
        fallback_model=_as_str(value.get("fallback_model"), None),
        fallback_model_2=_as_str(value.get("fallback_model_2"), None),
        document_window=opt_int("document_window"),
        initial_workers=opt_int("initial_workers"),
        retry_workers=opt_int("retry_workers"),
        fallback_workers=opt_int("fallback_workers"),
        fallback_2_workers=opt_int("fallback_2_workers"),
        main_concurrency=opt_int("main_concurrency"),
        fallback_concurrency=opt_int("fallback_concurrency"),
        fallback_2_concurrency=opt_int("fallback_2_concurrency"),
    )


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
    if not isinstance(providers_data, list) or not providers_data:
        raise ValueError("Config must include at least one [[providers]] entry")

    providers: list[ProviderConfig] = []
    for provider in providers_data:
        if not isinstance(provider, dict):
            raise ValueError("Each provider must be an object")
        name = _as_str(provider.get("name"))
        provider_type = _as_str(provider.get("type"))
        if not name or not provider_type:
            raise ValueError("Each provider must include name and type")

        _reject_legacy_provider_fields(provider, name)

        extra_headers: dict[str, str] = {}
        headers = provider.get("extra_headers")
        if isinstance(headers, dict):
            extra_headers = {str(k): str(v) for k, v in headers.items()}

        max_tokens = provider.get("max_tokens")
        max_tokens_value = int(max_tokens) if max_tokens is not None else None

        parsed_provider = ProviderConfig(
            name=name,
            type=provider_type,
            base=_parse_base_configs(provider.get("base"), f"providers[{name}]"),
            models=_parse_model_capabilities(provider.get("models"), name),
            api_version=_as_str(provider.get("api_version"), None),
            deployment=_as_str(provider.get("deployment"), None),
            project_id=_as_str(provider.get("project_id"), None),
            location=_as_str(provider.get("location"), None),
            credentials_path=_as_str(provider.get("credentials_path"), None),
            anthropic_version=_as_str(provider.get("anthropic_version"), None),
            max_tokens=max_tokens_value,
            extra_headers=extra_headers,
            system_prompt=_as_str(provider.get("system_prompt"), None),
            user_prompt=_as_str(provider.get("user_prompt"), None),
        )

        if parsed_provider.type == "azure_openai":
            if not parsed_provider.api_version:
                raise ValueError(f"Provider '{name}' requires api_version")
            if not parsed_provider.deployment:
                raise ValueError(f"Provider '{name}' requires deployment")
        if parsed_provider.type == "gemini_vertex":
            if not parsed_provider.project_id:
                raise ValueError(f"Provider '{name}' requires project_id")
            if not parsed_provider.location:
                raise ValueError(f"Provider '{name}' requires location")
        if parsed_provider.type == "claude" and not parsed_provider.anthropic_version:
            raise ValueError(f"Provider '{name}' requires anthropic_version")

        providers.append(parsed_provider)

    embedding = _parse_embedding_config(data.get("embedding"))
    rerank = _parse_rerank_config(data.get("rerank"))
    search = _parse_search_config(data.get("search"))
    translator_config = _parse_translator_config(data.get("translator_config"))
    main_model = _parse_main_model(data.get("main_model"))
    for item in main_model:
        if not _model_declared(providers, item.model):
            raise ValueError(
                f"main_model reference '{item.model}' does not resolve to a declared providers[].models[] entry"
            )

    return PaperConfig(
        extract=extract,
        render=render,
        providers=providers,
        main_model=main_model,
        embedding=embedding,
        rerank=rerank,
        search=search,
        translator_config=translator_config,
    )


def resolve_api_key_configs(entries: list[KeyConfig]) -> list[KeyConfig]:
    resolved: list[KeyConfig] = []
    for entry in entries:
        resolved.append(
            KeyConfig(
                value=resolve_key_value(entry.value),
                weight=entry.weight,
                quota_duration=entry.quota_duration,
                reset_time=entry.reset_time,
                quota_error_tokens=entry.quota_error_tokens,
            )
        )
    return resolved


def resolve_api_keys(entries: list[KeyConfig]) -> list[str]:
    return [entry.value for entry in resolve_api_key_configs(entries)]
