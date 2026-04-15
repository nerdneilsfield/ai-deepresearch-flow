"""Shared weighted model routing helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Generic, Literal, Protocol, TypeVar, cast
import json
import logging
import math
import re
import time

from deepresearch_flow.paper.config import (
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
    resolve_api_key_configs,
)


T = TypeVar("T")
logger = logging.getLogger(__name__)
ProviderT = TypeVar("ProviderT", bound="_RouteableProvider")
ModelT = TypeVar("ModelT", bound="_RouteableModel")


@dataclass(frozen=True)
class ParsedModelSelector:
    kind: Literal["single", "pool"]
    fixed_model: str | None
    pool: list[MainModelConfig]


class _RouteableModel(Protocol):
    model_name: str
    is_support_json_schema: bool
    is_support_json_object: bool


class _RouteableProvider(Protocol):
    name: str
    type: str
    base: list[BaseConfig]
    models: list[object]


@dataclass(frozen=True)
class RuntimeRoute(Generic[ProviderT, ModelT]):
    provider: ProviderT
    base: BaseConfig
    key: KeyConfig
    model: ModelT
    structured_mode: str
    route_id: str


@dataclass(frozen=True)
class _RouteCandidate(Generic[ProviderT, ModelT]):
    route: RuntimeRoute[ProviderT, ModelT]
    weight: int


class _SupportsActiveRouteConfig(Protocol[ProviderT, ModelT]):
    def resolve_active(self) -> tuple[ProviderT, ModelT]: ...


def structured_mode_for_model(model: _RouteableModel) -> str:
    if model.is_support_json_schema:
        return "json_schema"
    if model.is_support_json_object:
        return "json_object"
    return "none"


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


def _compute_next_reset_epoch(meta: KeyConfig) -> float | None:
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


def _build_route_candidates(
    provider: ProviderT,
    model: ModelT,
    *,
    weight: int,
) -> list[_RouteCandidate[ProviderT, ModelT]]:
    candidates: list[_RouteCandidate[ProviderT, ModelT]] = []
    for base in provider.base:
        resolved_keys = resolve_api_key_configs(base.key)
        for key in resolved_keys:
            routed_base = BaseConfig(url=base.url, weight=base.weight, key=[key])
            routed_provider = cast(ProviderT, replace(provider, base=[routed_base], models=[model]))
            route_id = f"{provider.name}|{model.model_name}|{base.url}|{key.value}"
            route = RuntimeRoute(
                provider=routed_provider,
                base=routed_base,
                key=key,
                model=model,
                structured_mode=structured_mode_for_model(model),
                route_id=route_id,
            )
            candidates.append(
                _RouteCandidate(
                    route=route,
                    weight=weight * base.weight * key.weight,
                )
            )
    return candidates


class RoutePool(Generic[ProviderT, ModelT]):
    def __init__(
        self,
        candidates: list[_RouteCandidate[ProviderT, ModelT]],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("RoutePool requires at least one candidate route")
        self._candidates = candidates
        self._rng = rng or Random()
        self._cooldown_seconds = max(cooldown_seconds, 0.0)
        self._verbose = verbose
        self._lock = asyncio.Lock()
        self._cooldowns: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._quota_until: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._error_counts: dict[str, int] = {candidate.route.route_id: 0 for candidate in candidates}
        self._last_pause_until: float = 0.0
        self._last_key_quota_until: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._key_meta: dict[str, KeyConfig] = {candidate.route.route_id: candidate.route.key for candidate in candidates}

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    @classmethod
    def from_selector(
        cls,
        config: PaperConfig,
        model_selector: ParsedModelSelector,
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
    ) -> "RoutePool[ProviderConfig, ModelCapability]":
        candidates: list[_RouteCandidate[ProviderConfig, ModelCapability]] = []
        if model_selector.kind == "single":
            assert model_selector.fixed_model is not None
            provider_name, model_name = model_selector.fixed_model.split("/", 1)
            provider, model = resolve_model_capability(provider_name, model_name, config.providers)
            pool_items = [MainModelConfig(model=model_selector.fixed_model, weight=1)]
        else:
            pool_items = model_selector.pool or config.main_model

        for pool_item in pool_items:
            provider_name, model_name = pool_item.model.split("/", 1)
            provider, model = resolve_model_capability(provider_name, model_name, config.providers)
            candidates.extend(
                _build_route_candidates(
                    provider,
                    model,
                    weight=pool_item.weight,
                )
            )
        return cls(candidates, cooldown_seconds=cooldown_seconds, verbose=verbose, rng=rng)

    @classmethod
    def _from_active_route_config(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        provider, model = config.resolve_active()
        candidates = _build_route_candidates(provider, model, weight=1)
        return cls(candidates, cooldown_seconds=cooldown_seconds, verbose=verbose, rng=rng)

    @classmethod
    def from_embedding_provider(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        return cls._from_active_route_config(
            config,
            cooldown_seconds=cooldown_seconds,
            verbose=verbose,
            rng=rng,
        )

    @classmethod
    def from_rerank_provider(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        return cls._from_active_route_config(
            config,
            cooldown_seconds=cooldown_seconds,
            verbose=verbose,
            rng=rng,
        )

    async def get(self) -> RuntimeRoute[ProviderT, ModelT]:
        while True:
            wait_for: float | None = None
            wait_until_epoch: float | None = None
            pause_reason: str | None = None
            should_log_pause = False
            async with self._lock:
                now = time.monotonic()
                now_epoch = time.time()
                available = [
                    candidate
                    for candidate in self._candidates
                    if self._cooldowns.get(candidate.route.route_id, 0.0) <= now
                    and self._quota_until.get(candidate.route.route_id, 0.0) <= now_epoch
                ]
                if available:
                    selected = choose_weighted(
                        available,
                        [candidate.weight for candidate in available],
                        rng=self._rng,
                    )
                    return selected.route

                waits: list[float] = []
                has_cooldown_wait = False
                has_quota_wait = False
                for candidate in self._candidates:
                    route_id = candidate.route.route_id
                    cooldown_wait = max(self._cooldowns.get(route_id, 0.0) - now, 0.0)
                    quota_wait = max(self._quota_until.get(route_id, 0.0) - now_epoch, 0.0)
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
                raise RuntimeError("RoutePool has no available candidates")
            wait_for = max(wait_for, 0.01)
            if should_log_pause and wait_until_epoch is not None:
                reset_dt = datetime.fromtimestamp(wait_until_epoch).astimezone().isoformat()
                logger.warning(
                    "All weighted routes unavailable (%s); pausing %.2fs until %s",
                    pause_reason,
                    wait_for,
                    reset_dt,
                )
            elif self._verbose:
                logger.debug("All weighted routes cooling down; waiting %.2fs", wait_for)
            await asyncio.sleep(wait_for)

    async def mark_error(self, route: RuntimeRoute[ProviderT, ModelT]) -> None:
        route_id = route.route_id
        async with self._lock:
            now = time.monotonic()
            self._error_counts[route_id] = self._error_counts.get(route_id, 0) + 1
            cooldown_until = now + self._cooldown_seconds
            current = self._cooldowns.get(route_id, 0.0)
            self._cooldowns[route_id] = max(current, cooldown_until)
            if cooldown_until > current:
                logger.warning(
                    "Route key %s cooling down for %.2fs (errors=%d)",
                    _mask_key(route.key.value),
                    self._cooldown_seconds,
                    self._error_counts[route_id],
                )

    async def mark_quota_exceeded(
        self,
        route: RuntimeRoute[ProviderT, ModelT],
        message: str,
        status_code: int | None,
    ) -> bool:
        route_id = route.route_id
        meta = self._key_meta[route_id]
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
        matched_tokens = [token for token in tokens if token.lower() in lower_msg]
        if not matched_tokens:
            return False
        reset_epoch = _compute_next_reset_epoch(meta)
        if reset_epoch is None:
            logger.warning(
                "Route key %s hit quota trigger but no reset_time/quota_duration configured "
                "(matched=%s, status_code=%s)",
                _mask_key(route.key.value),
                ",".join(matched_tokens) or "<none>",
                status_code if status_code is not None else "unknown",
            )
            return False
        async with self._lock:
            current = self._quota_until.get(route_id, 0.0)
            self._quota_until[route_id] = max(current, reset_epoch)
            if reset_epoch > self._last_key_quota_until.get(route_id, 0.0):
                self._last_key_quota_until[route_id] = reset_epoch
                wait_for = max(reset_epoch - time.time(), 0.0)
                reset_dt = datetime.fromtimestamp(reset_epoch).astimezone().isoformat()
                logger.warning(
                    "Route key %s quota exhausted; pausing %.2fs until %s (matched=%s, status_code=%s)",
                    _mask_key(route.key.value),
                    wait_for,
                    reset_dt,
                    ",".join(matched_tokens) or "<none>",
                    status_code if status_code is not None else "unknown",
                )
        return True


def _mask_key(value: str, *, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return value
    return f"...{value[-keep:]}"


def select_runtime_route(
    config: PaperConfig,
    model_selector: ParsedModelSelector,
    *,
    rng: Random | None = None,
) -> RuntimeRoute:
    pool = RoutePool.from_selector(config, model_selector, rng=rng)
    # Stateless callers still need a synchronous one-shot selection.
    return choose_weighted(
        [candidate.route for candidate in pool._candidates],
        [candidate.weight for candidate in pool._candidates],
        rng=rng,
    )
