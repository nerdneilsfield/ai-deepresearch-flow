"""Shared weighted model routing helpers."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, time as time_of_day, timezone, tzinfo
from pathlib import Path
from random import Random
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar, cast
import json
import logging
import math
import re
import time
from zoneinfo import ZoneInfo

from deepresearch_flow.paper.active_window import is_active, next_active_start, parse_windows
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
ProviderT = TypeVar("ProviderT")
ModelT = TypeVar("ModelT")
ProviderT_co = TypeVar("ProviderT_co", covariant=True)
ModelT_co = TypeVar("ModelT_co", covariant=True)


class ProviderOutOfActiveWindow(RuntimeError):
    def __init__(self, urls: list[str], next_available: datetime | None) -> None:
        self.urls = urls
        self.next_available = next_available
        next_text = str(next_available) if next_available is not None else "unknown"
        super().__init__(
            f"All provider URLs are outside their active window: [{', '.join(urls)}]; "
            f"next available at {next_text}"
        )


@contextmanager
def provider_window_error_as_click():
    try:
        yield
    except ProviderOutOfActiveWindow as exc:
        import click  # type: ignore[import-not-found]

        raise click.ClickException(str(exc)) from exc


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


class _SupportsActiveRouteConfig(Protocol[ProviderT_co, ModelT_co]):
    def resolve_active(self) -> tuple[ProviderT_co, ModelT_co]: ...


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
    if not isinstance(weight, (int, str)):
        raise ValueError(f"{item_name} entries must include positive integer weight")
    try:
        weight_value = int(weight)
    except ValueError as exc:
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


def _current_local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _resolve_window_config(
    base: BaseConfig,
    *,
    fallback_tz: tzinfo | None = None,
) -> tuple[list[tuple[time_of_day, time_of_day]], tzinfo]:
    local_tz = fallback_tz or _current_local_tz()
    return (
        parse_windows(base.active_windows),
        ZoneInfo(base.active_timezone) if base.active_timezone else local_tz,
    )


def _parse_windows_for_candidates(
    candidates: list[_RouteCandidate[ProviderT, ModelT]],
    *,
    fallback_tz: tzinfo | None = None,
) -> tuple[dict[str, list[tuple[time_of_day, time_of_day]]], dict[str, tzinfo]]:
    local_tz = fallback_tz or _current_local_tz()
    windows: dict[str, list[tuple[time_of_day, time_of_day]]] = {}
    timezones: dict[str, tzinfo] = {}
    for candidate in candidates:
        route_id = candidate.route.route_id
        parsed_windows, parsed_tz = _resolve_window_config(candidate.route.base, fallback_tz=local_tz)
        windows[route_id] = parsed_windows
        timezones[route_id] = parsed_tz
    return windows, timezones


def _unique_route_urls(candidates: list[_RouteCandidate[ProviderT, ModelT]]) -> list[str]:
    urls: list[str] = []
    for candidate in candidates:
        url = candidate.route.base.url
        if url not in urls:
            urls.append(url)
    return urls


def _earliest_next_active_start(
    now_dt: datetime,
    candidates: list[_RouteCandidate[ProviderT, ModelT]],
    windows: dict[str, list[tuple[time_of_day, time_of_day]]],
    timezones: dict[str, tzinfo],
) -> datetime | None:
    starts = [
        next_active_start(now_dt, windows[candidate.route.route_id], timezones[candidate.route.route_id])
        for candidate in candidates
    ]
    valid = [start for start in starts if start is not None]
    return min(valid) if valid else None


def _build_route_candidates(
    provider: ProviderT,
    model: ModelT,
    *,
    weight: int,
) -> list[_RouteCandidate[ProviderT, ModelT]]:
    candidates: list[_RouteCandidate[ProviderT, ModelT]] = []
    routeable_provider = cast(_RouteableProvider, provider)
    routeable_model = cast(_RouteableModel, model)
    for base in routeable_provider.base:
        resolved_keys = resolve_api_key_configs(base.key)
        for key in resolved_keys:
            routed_base = BaseConfig(
                url=base.url,
                weight=base.weight,
                key=[key],
                active_windows=base.active_windows,
                active_timezone=base.active_timezone,
            )
            routed_provider = cast(ProviderT, replace(cast(Any, provider), base=[routed_base], models=[model]))
            route_id = f"{routeable_provider.name}|{routeable_model.model_name}|{base.url}|{key.value}"
            route = RuntimeRoute(
                provider=routed_provider,
                base=routed_base,
                key=key,
                model=model,
                structured_mode=structured_mode_for_model(routeable_model),
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
        now_provider: Callable[[], float] | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("RoutePool requires at least one candidate route")
        self._candidates = candidates
        self._rng = rng or Random()
        self._cooldown_seconds = max(cooldown_seconds, 0.0)
        self._verbose = verbose
        self._now = now_provider or time.time
        self._lock = asyncio.Lock()
        self._cooldowns: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._quota_until: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._error_counts: dict[str, int] = {candidate.route.route_id: 0 for candidate in candidates}
        self._last_pause_until: float = 0.0
        self._last_key_quota_until: dict[str, float] = {candidate.route.route_id: 0.0 for candidate in candidates}
        self._key_meta: dict[str, KeyConfig] = {candidate.route.route_id: candidate.route.key for candidate in candidates}
        self._windows, self._tz = _parse_windows_for_candidates(candidates)

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
        now_provider: Callable[[], float] | None = None,
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
        return cast(
            "RoutePool[ProviderConfig, ModelCapability]",
            cls(
                cast(Any, candidates),
                cooldown_seconds=cooldown_seconds,
                verbose=verbose,
                rng=rng,
                now_provider=now_provider,
            ),
        )

    @classmethod
    def _from_active_route_config(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
        now_provider: Callable[[], float] | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        provider, model = config.resolve_active()
        candidates = _build_route_candidates(provider, model, weight=1)
        return cls(
            candidates,
            cooldown_seconds=cooldown_seconds,
            verbose=verbose,
            rng=rng,
            now_provider=now_provider,
        )

    @classmethod
    def from_embedding_provider(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
        now_provider: Callable[[], float] | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        return cls._from_active_route_config(
            config,
            cooldown_seconds=cooldown_seconds,
            verbose=verbose,
            rng=rng,
            now_provider=now_provider,
        )

    @classmethod
    def from_rerank_provider(
        cls,
        config: _SupportsActiveRouteConfig[ProviderT, ModelT],
        *,
        cooldown_seconds: float = 1.0,
        verbose: bool = False,
        rng: Random | None = None,
        now_provider: Callable[[], float] | None = None,
    ) -> "RoutePool[ProviderT, ModelT]":
        return cls._from_active_route_config(
            config,
            cooldown_seconds=cooldown_seconds,
            verbose=verbose,
            rng=rng,
            now_provider=now_provider,
        )

    async def get(self) -> RuntimeRoute[ProviderT, ModelT]:
        while True:
            wait_for: float | None = None
            wait_until_epoch: float | None = None
            pause_reason: str | None = None
            should_log_pause = False
            async with self._lock:
                now = time.monotonic()
                now_epoch = self._now()
                now_dt = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
                available: list[_RouteCandidate[ProviderT, ModelT]] = []
                waits: list[float] = []
                has_cooldown_wait = False
                has_quota_wait = False
                any_window_ok = False
                for candidate in self._candidates:
                    route_id = candidate.route.route_id
                    window_ok = is_active(now_dt, self._windows[route_id], self._tz[route_id])
                    cooldown_wait = max(self._cooldowns.get(route_id, 0.0) - now, 0.0)
                    quota_wait = max(self._quota_until.get(route_id, 0.0) - now_epoch, 0.0)
                    timer_ok = cooldown_wait <= 0 and quota_wait <= 0
                    if window_ok and timer_ok:
                        available.append(candidate)
                    if not window_ok:
                        continue
                    any_window_ok = True
                    if cooldown_wait > 0:
                        has_cooldown_wait = True
                    if quota_wait > 0:
                        has_quota_wait = True
                    waits.append(max(cooldown_wait, quota_wait))
                if available:
                    selected = choose_weighted(
                        available,
                        [candidate.weight for candidate in available],
                        rng=self._rng,
                    )
                    return selected.route

                if not any_window_ok:
                    urls = _unique_route_urls(self._candidates)
                    earliest = _earliest_next_active_start(now_dt, self._candidates, self._windows, self._tz)
                    logger.warning(
                        "All provider URLs are outside their active window: [%s]; next available at %s",
                        ", ".join(urls),
                        str(earliest) if earliest is not None else "unknown",
                    )
                    raise ProviderOutOfActiveWindow(urls, earliest)

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
    now_dt = datetime.now(timezone.utc)
    windows, timezones = _parse_windows_for_candidates(pool._candidates)
    active_candidates = [
        candidate
        for candidate in pool._candidates
        if is_active(now_dt, windows[candidate.route.route_id], timezones[candidate.route.route_id])
    ]
    if not active_candidates:
        urls = _unique_route_urls(pool._candidates)
        earliest = _earliest_next_active_start(now_dt, pool._candidates, windows, timezones)
        raise ProviderOutOfActiveWindow(urls, earliest)
    return choose_weighted(
        [candidate.route for candidate in active_candidates],
        [candidate.weight for candidate in active_candidates],
        rng=rng,
    )
