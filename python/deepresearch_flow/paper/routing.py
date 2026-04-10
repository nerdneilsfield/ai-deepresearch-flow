"""Shared weighted model routing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Literal, TypeVar
import json

from deepresearch_flow.paper.config import (
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
)


T = TypeVar("T")


@dataclass(frozen=True)
class ParsedModelSelector:
    kind: Literal["single", "pool"]
    fixed_model: str | None
    pool: list[MainModelConfig]


@dataclass(frozen=True)
class RuntimeRoute:
    provider: ProviderConfig
    base: BaseConfig
    key: KeyConfig
    model: ModelCapability


def _find_provider(providers: list[ProviderConfig], provider_name: str) -> ProviderConfig | None:
    for provider in providers:
        if provider.name == provider_name:
            return provider
    return None


def resolve_model_capability(
    provider_name: str,
    model_name: str,
    providers: list[ProviderConfig],
) -> tuple[ProviderConfig, ModelCapability]:
    provider = _find_provider(providers, provider_name)
    if provider is None:
        raise ValueError(
            f"Model reference '{provider_name}/{model_name}' does not resolve to a declared providers[].models[] entry"
        )
    for model in provider.models:
        if model.model_name == model_name:
            return provider, model
    raise ValueError(
        f"Model reference '{provider_name}/{model_name}' does not resolve to a declared providers[].models[] entry"
    )


def _parse_pool_item(item: object, providers: list[ProviderConfig], item_name: str) -> MainModelConfig:
    if not isinstance(item, dict):
        raise ValueError(f"{item_name} entries must be objects")
    model_ref = item.get("model")
    weight = item.get("weight")
    if not isinstance(model_ref, str) or not model_ref:
        raise ValueError(f"{item_name} entries must include model")
    try:
        weight_value = int(weight)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{item_name} entries must include positive integer weight") from exc
    if weight_value <= 0:
        raise ValueError(f"{item_name} entries must include positive integer weight")
    if "/" not in model_ref:
        raise ValueError(f"{item_name} model '{model_ref}' must be in provider/model format")
    provider_name, model_name = model_ref.split("/", 1)
    resolve_model_capability(provider_name, model_name, providers)
    return MainModelConfig(model=model_ref, weight=weight_value)


def load_main_model_override(model_ref: str, providers: list[ProviderConfig]) -> list[MainModelConfig]:
    payload_text = model_ref
    if model_ref.startswith("@"):
        payload_path = Path(model_ref[1:])
        if not payload_path.exists():
            raise ValueError(f"Model pool file does not exist: {payload_path}")
        payload_text = payload_path.read_text(encoding="utf-8")

    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Inline model pool must be valid JSON array") from exc
    if not isinstance(parsed, list):
        raise ValueError("Inline model pool must be a JSON array")
    return [
        _parse_pool_item(item, providers, "main_model")
        for item in parsed
    ]


def parse_model_selector(model_ref: str, config: PaperConfig) -> ParsedModelSelector:
    providers = config.providers
    if model_ref.startswith("@"):
        return ParsedModelSelector(kind="pool", fixed_model=None, pool=load_main_model_override(model_ref, providers))

    try:
        pool = load_main_model_override(model_ref, providers)
    except ValueError:
        pool = None
    if pool is not None:
        return ParsedModelSelector(kind="pool", fixed_model=None, pool=pool)

    if "/" not in model_ref:
        raise ValueError("--model must be in provider/model format or a JSON model pool")
    provider_name, model_name = model_ref.split("/", 1)
    resolve_model_capability(provider_name, model_name, providers)
    return ParsedModelSelector(kind="single", fixed_model=model_ref, pool=[])


def choose_weighted(items: list[T], weights: list[int], *, rng: Random | None = None) -> T:
    if len(items) != len(weights) or not items:
        raise ValueError("Weighted selection requires non-empty items with matching weights")
    selector = rng or Random()
    total = sum(weights)
    ticket = selector.randint(1, total)
    current = 0
    for item, weight in zip(items, weights, strict=True):
        current += weight
        if ticket <= current:
            return item
    return items[-1]


def select_runtime_route(
    config: PaperConfig,
    model_selector: ParsedModelSelector,
    *,
    rng: Random | None = None,
) -> RuntimeRoute:
    if model_selector.kind == "single":
        assert model_selector.fixed_model is not None
        provider_name, model_name = model_selector.fixed_model.split("/", 1)
        provider, model = resolve_model_capability(provider_name, model_name, config.providers)
    else:
        pool = model_selector.pool or config.main_model
        selected = choose_weighted(pool, [item.weight for item in pool], rng=rng)
        provider_name, model_name = selected.model.split("/", 1)
        provider, model = resolve_model_capability(provider_name, model_name, config.providers)

    base = choose_weighted(provider.base, [item.weight for item in provider.base], rng=rng)
    key = choose_weighted(base.key, [item.weight for item in base.key], rng=rng)
    return RuntimeRoute(provider=provider, base=base, key=key, model=model)
