from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from random import Random
import time

import pytest

from deepresearch_flow.paper.config import (
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
)
from deepresearch_flow.paper.routing import (
    ProviderOutOfActiveWindow,
    RoutePool,
    parse_model_selector,
)


def _build_config(
    *,
    base_configs: list[BaseConfig],
) -> PaperConfig:
    provider = ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=base_configs,
        models=[
            ModelCapability(
                model_name="gpt-4.1",
                is_stream=True,
                is_support_json_schema=True,
                is_support_json_object=True,
            )
        ],
        api_version=None,
        deployment=None,
        project_id=None,
        location=None,
        credentials_path=None,
        anthropic_version=None,
        max_tokens=None,
        extra_headers={},
        system_prompt=None,
        user_prompt=None,
    )
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )


def _build_pool(
    *,
    base_configs: list[BaseConfig],
    now_dt: datetime,
    cooldown_seconds: float = 0.01,
    rng_seed: int = 1,
) -> RoutePool:
    config = _build_config(base_configs=base_configs)
    selector = parse_model_selector("openai/gpt-4.1", config)
    return RoutePool.from_selector(
        config,
        selector,
        cooldown_seconds=cooldown_seconds,
        rng=Random(rng_seed),
        now_provider=lambda: now_dt.timestamp(),
    )


def test_route_pool_get_returns_route_when_all_candidates_are_in_window() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://a.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="UTC",
            ),
            BaseConfig(
                url="https://b.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-b", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="UTC",
            ),
        ],
        now_dt=datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc),
    )

    route = asyncio.run(pool.get())

    assert route.base.url in {"https://a.example.com/v1", "https://b.example.com/v1"}


def test_route_pool_get_filters_out_window_inactive_candidates() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://always.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="Asia/Shanghai",
            ),
            BaseConfig(
                url="https://night.example.com/v1",
                weight=50,
                key=[KeyConfig(value="key-b", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="Asia/Shanghai",
            ),
        ],
        now_dt=datetime(2026, 4, 21, 1, 30, tzinfo=timezone.utc),
    )

    route = asyncio.run(pool.get())

    assert route.base.url == "https://always.example.com/v1"


def test_route_pool_get_raises_when_every_candidate_is_out_of_window() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://night.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="Asia/Shanghai",
            )
        ],
        now_dt=datetime(2026, 4, 21, 1, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(ProviderOutOfActiveWindow) as exc_info:
        asyncio.run(pool.get())

    assert "https://night.example.com/v1" in str(exc_info.value)
    assert "2026-04-21 22:00:00+08:00" in str(exc_info.value)


def test_route_pool_get_ignores_timer_when_every_candidate_is_out_of_window() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://night-a.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            ),
            BaseConfig(
                url="https://night-b.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-b", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            ),
        ],
        now_dt=datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc),
        cooldown_seconds=60.0,
    )
    blocked_route = pool._candidates[0].route

    async def _run() -> None:
        await pool.mark_error(blocked_route)
        await pool.get()

    with pytest.raises(ProviderOutOfActiveWindow):
        asyncio.run(_run())


def test_route_pool_get_waits_for_in_window_candidate_to_recover() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://always.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="UTC",
            ),
            BaseConfig(
                url="https://night.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-b", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            ),
        ],
        now_dt=datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc),
        cooldown_seconds=0.02,
    )

    async def _run() -> tuple[str, float]:
        first = await pool.get()
        await pool.mark_error(first)
        start = time.monotonic()
        second = await pool.get()
        return second.base.url, time.monotonic() - start

    chosen_url, elapsed = asyncio.run(_run())

    assert chosen_url == "https://always.example.com/v1"
    assert elapsed >= 0.01


def test_route_pool_wait_time_ignores_window_inactive_candidates() -> None:
    pool = _build_pool(
        base_configs=[
            BaseConfig(
                url="https://always.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="UTC",
            ),
            BaseConfig(
                url="https://night.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-b", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            ),
        ],
        now_dt=datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc),
        cooldown_seconds=0.02,
    )

    async def _run() -> tuple[str, float]:
        first = await pool.get()
        await pool.mark_error(first)
        blocked_route = next(
            candidate.route
            for candidate in pool._candidates
            if candidate.route.base.url.endswith("night.example.com/v1")
        )
        pool._cooldowns[blocked_route.route_id] = time.monotonic() + 10.0
        start = time.monotonic()
        second = await pool.get()
        return second.base.url, time.monotonic() - start

    chosen_url, elapsed = asyncio.run(_run())

    assert chosen_url == "https://always.example.com/v1"
    assert elapsed < 1.0
