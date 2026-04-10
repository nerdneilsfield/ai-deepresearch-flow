from __future__ import annotations

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
