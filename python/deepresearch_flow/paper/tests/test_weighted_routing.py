from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from random import Random

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


def _build_provider(name: str, *, models: list[str], base_urls: list[str]) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        type="openai_compatible",
        base=[
            BaseConfig(
                url=url,
                weight=idx + 1,
                key=[
                    KeyConfig(value=f"{name}-key-{idx}-a", weight=idx + 1),
                    KeyConfig(value=f"{name}-key-{idx}-b", weight=idx + 2),
                ],
            )
            for idx, url in enumerate(base_urls)
        ],
        models=[
            ModelCapability(
                model_name=model_name,
                is_stream=True,
                is_support_json_schema=True,
                is_support_json_object=True,
            )
            for model_name in models
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


def _build_config() -> PaperConfig:
    provider_a = _build_provider(
        "openai",
        models=["gpt-4.1", "gpt-4.1-mini"],
        base_urls=["https://a.example.com/v1", "https://b.example.com/v1"],
    )
    provider_b = _build_provider(
        "anthropic",
        models=["claude-sonnet"],
        base_urls=["https://c.example.com/v1"],
    )
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider_a, provider_b],
        main_model=[
            MainModelConfig(model="openai/gpt-4.1", weight=4),
            MainModelConfig(model="openai/gpt-4.1-mini", weight=1),
            MainModelConfig(model="anthropic/claude-sonnet", weight=3),
        ],
    )


def test_parse_single_model_ref_resolves_declared_model() -> None:
    from deepresearch_flow.paper.routing import parse_model_selector

    config = _build_config()

    selector = parse_model_selector("openai/gpt-4.1", config)

    assert selector.kind == "single"
    assert selector.fixed_model == "openai/gpt-4.1"


def test_parse_inline_json_model_pool() -> None:
    from deepresearch_flow.paper.routing import parse_model_selector

    config = _build_config()

    selector = parse_model_selector(
        '[{"model":"openai/gpt-4.1","weight":2}]',
        config,
    )

    assert selector.kind == "pool"
    assert selector.pool[0].model == "openai/gpt-4.1"
    assert selector.pool[0].weight == 2


def test_parse_at_file_model_pool(tmp_path) -> None:
    from deepresearch_flow.paper.routing import parse_model_selector

    config = _build_config()
    payload = tmp_path / "main_model.json"
    payload.write_text(
        '[{"model":"openai/gpt-4.1-mini","weight":5}]',
        encoding="utf-8",
    )

    selector = parse_model_selector(f"@{payload}", config)

    assert selector.kind == "pool"
    assert selector.pool[0].model == "openai/gpt-4.1-mini"
    assert selector.pool[0].weight == 5


def test_rejects_unknown_single_model() -> None:
    from deepresearch_flow.paper.routing import parse_model_selector

    config = _build_config()

    with pytest.raises(ValueError, match="does not resolve"):
        parse_model_selector("openai/gpt-unknown", config)


def test_rejects_unknown_model_in_json_pool() -> None:
    from deepresearch_flow.paper.routing import parse_model_selector

    config = _build_config()

    with pytest.raises(ValueError, match="does not resolve"):
        parse_model_selector(
            '[{"model":"openai/gpt-unknown","weight":2}]',
            config,
        )


def test_single_item_main_model_is_equivalent_to_fixed_route() -> None:
    from deepresearch_flow.paper.routing import ParsedModelSelector, parse_model_selector, select_runtime_route

    config = _build_config()
    single_pool_config = PaperConfig(
        extract=config.extract,
        render=config.render,
        providers=config.providers,
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )

    single_selector = ParsedModelSelector(
        kind="pool",
        fixed_model=None,
        pool=single_pool_config.main_model,
    )
    fixed_selector = parse_model_selector("openai/gpt-4.1", config)

    single_route = select_runtime_route(single_pool_config, single_selector, rng=Random(1))
    fixed_route = select_runtime_route(config, fixed_selector, rng=Random(1))

    assert single_route.provider.name == fixed_route.provider.name
    assert single_route.model.model_name == fixed_route.model.model_name


def test_weighted_base_selection_uses_provider_scope() -> None:
    from deepresearch_flow.paper.routing import ParsedModelSelector, select_runtime_route

    config = _build_config()
    selector = ParsedModelSelector(
        kind="pool",
        fixed_model=None,
        pool=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )

    route = select_runtime_route(config, selector, rng=Random(7))

    assert route.provider.name == "openai"
    assert route.base.url in {"https://a.example.com/v1", "https://b.example.com/v1"}
    assert route.base.url != "https://c.example.com/v1"


def test_weighted_key_selection_uses_base_scope() -> None:
    from deepresearch_flow.paper.routing import ParsedModelSelector, select_runtime_route

    config = _build_config()
    selector = ParsedModelSelector(
        kind="pool",
        fixed_model=None,
        pool=[MainModelConfig(model="anthropic/claude-sonnet", weight=1)],
    )

    route = select_runtime_route(config, selector, rng=Random(3))

    assert route.provider.name == "anthropic"
    assert route.base.url == "https://c.example.com/v1"
    assert route.key.value in {"anthropic-key-0-a", "anthropic-key-0-b"}


def test_route_pool_expands_all_model_base_key_candidates() -> None:
    from deepresearch_flow.paper.routing import RoutePool, parse_model_selector

    config = _build_config()
    selector = parse_model_selector("openai/gpt-4.1", config)

    pool = RoutePool.from_selector(config, selector, cooldown_seconds=0.01, rng=Random(1))

    expanded = sorted(
        (
            candidate.route.base.url,
            candidate.route.key.value,
            candidate.weight,
        )
        for candidate in pool._candidates
    )
    assert expanded == [
        ("https://a.example.com/v1", "openai-key-0-a", 1),
        ("https://a.example.com/v1", "openai-key-0-b", 2),
        ("https://b.example.com/v1", "openai-key-1-a", 4),
        ("https://b.example.com/v1", "openai-key-1-b", 6),
    ]


def test_build_route_candidates_preserves_active_window_fields() -> None:
    from deepresearch_flow.paper.routing import _build_route_candidates

    provider = ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://a.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
                active_windows=["09:00-12:00"],
                active_timezone="Asia/Shanghai",
            )
        ],
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

    candidates = _build_route_candidates(provider, provider.models[0], weight=1)

    assert all(candidate.route.base.active_windows == ["09:00-12:00"] for candidate in candidates)
    assert all(candidate.route.base.active_timezone == "Asia/Shanghai" for candidate in candidates)


def test_route_pool_get_matches_weighted_selection_when_available() -> None:
    from deepresearch_flow.paper.routing import RoutePool, choose_weighted, parse_model_selector

    config = _build_config()
    selector = parse_model_selector("openai/gpt-4.1", config)

    pool = RoutePool.from_selector(config, selector, cooldown_seconds=0.01, rng=Random(7))
    expected = choose_weighted(
        [candidate.route for candidate in pool._candidates],
        [candidate.weight for candidate in pool._candidates],
        rng=Random(7),
    )

    route = asyncio.run(pool.get())

    assert route.route_id == expected.route_id


def test_route_pool_skips_route_in_cooldown() -> None:
    from deepresearch_flow.paper.routing import RoutePool, parse_model_selector

    config = _build_config()
    selector = parse_model_selector("openai/gpt-4.1", config)
    pool = RoutePool.from_selector(config, selector, cooldown_seconds=60.0, rng=Random(1))
    blocked = pool._candidates[0].route

    async def _run() -> str:
        await pool.mark_error(blocked)
        route = await pool.get()
        return route.route_id

    chosen = asyncio.run(_run())

    assert chosen != blocked.route_id


def test_route_pool_marks_quota_and_skips_exhausted_route() -> None:
    from deepresearch_flow.paper.routing import RoutePool, parse_model_selector

    provider = ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://a.example.com/v1",
                weight=1,
                key=[
                    KeyConfig(
                        value="key-a",
                        weight=1,
                        quota_duration=3600,
                        reset_time="2000-01-01 00:00:00 +00:00",
                        quota_error_tokens=["rate limit"],
                    ),
                    KeyConfig(value="key-b", weight=1),
                ],
            )
        ],
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
    config = PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )
    selector = parse_model_selector("openai/gpt-4.1", config)
    pool = RoutePool.from_selector(config, selector, cooldown_seconds=0.01, rng=Random(1))
    quota_route = next(candidate.route for candidate in pool._candidates if candidate.route.key.value == "key-a")

    async def _run() -> tuple[bool, str]:
        flagged = await pool.mark_quota_exceeded(
            quota_route,
            "upstream says RATE LIMIT exceeded",
            429,
        )
        route = await pool.get()
        return flagged, route.key.value

    flagged, chosen_key = asyncio.run(_run())

    assert flagged is True
    assert chosen_key == "key-b"


def test_route_pool_waits_until_route_recovers() -> None:
    from deepresearch_flow.paper.routing import RoutePool, parse_model_selector
    import time

    provider = ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://a.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
            )
        ],
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
    config = PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )
    selector = parse_model_selector("openai/gpt-4.1", config)
    pool = RoutePool.from_selector(config, selector, cooldown_seconds=0.05, rng=Random(1))
    only_route = pool._candidates[0].route

    async def _run() -> tuple[str, float]:
        await pool.mark_error(only_route)
        start = time.monotonic()
        route = await pool.get()
        return route.route_id, time.monotonic() - start

    route_id, elapsed = asyncio.run(_run())

    assert route_id == only_route.route_id
    assert elapsed >= 0.04


def test_select_runtime_route_raises_when_all_candidates_are_out_of_window(monkeypatch: pytest.MonkeyPatch) -> None:
    import deepresearch_flow.paper.routing as routing
    from deepresearch_flow.paper.routing import ParsedModelSelector, ProviderOutOfActiveWindow, select_runtime_route

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc)
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(routing, "datetime", _FrozenDateTime)

    provider = _build_provider(
        "openai",
        models=["gpt-4.1"],
        base_urls=["https://night-only.example.com/v1"],
    )
    provider = replace(
        provider,
        base=[
            replace(
                provider.base[0],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            )
        ],
    )
    config = PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )

    with pytest.raises(ProviderOutOfActiveWindow):
        select_runtime_route(
            config,
            ParsedModelSelector(kind="pool", fixed_model=None, pool=config.main_model),
            rng=Random(1),
        )


def test_select_runtime_route_filters_out_window_inactive_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    import deepresearch_flow.paper.routing as routing
    from deepresearch_flow.paper.routing import ParsedModelSelector, select_runtime_route

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc)
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(routing, "datetime", _FrozenDateTime)

    provider = ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://always-on.example.com/v1",
                weight=1,
                key=[KeyConfig(value="always-key", weight=1)],
                active_windows=["00:00-24:00"],
                active_timezone="UTC",
            ),
            BaseConfig(
                url="https://night-only.example.com/v1",
                weight=10,
                key=[KeyConfig(value="night-key", weight=1)],
                active_windows=["22:00-23:00"],
                active_timezone="UTC",
            ),
        ],
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
    config = PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
    )
    selector = ParsedModelSelector(kind="pool", fixed_model=None, pool=config.main_model)

    for seed in range(20):
        route = select_runtime_route(config, selector, rng=Random(seed))
        assert route.base.url == "https://always-on.example.com/v1"


def test_select_runtime_route_keeps_default_no_window_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    import deepresearch_flow.paper.routing as routing
    from deepresearch_flow.paper.routing import ParsedModelSelector, select_runtime_route

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc)
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(routing, "datetime", _FrozenDateTime)

    config = _build_config()
    route = select_runtime_route(
        config,
        ParsedModelSelector(kind="pool", fixed_model=None, pool=[MainModelConfig(model="openai/gpt-4.1", weight=1)]),
        rng=Random(7),
    )

    assert route.provider.name == "openai"
