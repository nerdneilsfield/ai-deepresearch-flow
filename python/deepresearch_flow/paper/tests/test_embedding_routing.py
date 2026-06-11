from __future__ import annotations

import asyncio
from dataclasses import dataclass
from random import Random

from deepresearch_flow.paper.config import (
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
)
from deepresearch_flow.paper.routing import RoutePool, parse_model_selector


@dataclass(frozen=True)
class _ResolvedBundle:
    provider: ProviderConfig
    model: ModelCapability

    def resolve_active(self) -> tuple[ProviderConfig, ModelCapability]:
        return self.provider, self.model


def _provider(name: str) -> tuple[ProviderConfig, ModelCapability]:
    return (
        ProviderConfig(
            name=name,
            type="openai_compatible",
            base=[
                BaseConfig(
                    url=f"https://{name}-a.example.com/v1",
                    weight=1,
                    key=[
                        KeyConfig(
                            value=f"{name}-a-key-1",
                            weight=1,
                            quota_duration=3600,
                            reset_time="2000-01-01 00:00:00 +00:00",
                            quota_error_tokens=["rate limit"],
                        ),
                        KeyConfig(
                            value=f"{name}-a-key-2",
                            weight=2,
                            quota_duration=3600,
                            reset_time="2000-01-01 00:00:00 +00:00",
                            quota_error_tokens=["rate limit"],
                        ),
                    ],
                ),
                BaseConfig(
                    url=f"https://{name}-b.example.com/v1",
                    weight=3,
                    key=[
                        KeyConfig(
                            value=f"{name}-b-key-1",
                            weight=1,
                            quota_duration=3600,
                            reset_time="2000-01-01 00:00:00 +00:00",
                            quota_error_tokens=["rate limit"],
                        ),
                    ],
                ),
            ],
            models=[
                ModelCapability(
                    model_name=f"{name}-model",
                    is_stream=False,
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
        ),
        ModelCapability(
            model_name=f"{name}-model",
            is_stream=False,
            is_support_json_schema=True,
            is_support_json_object=True,
        ),
    )


def _main_config(provider: ProviderConfig, model_name: str) -> PaperConfig:
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model=f"{provider.name}/{model_name}", weight=1)],
    )


def test_embedding_route_pool_matches_main_pool_for_weighted_selection_and_cooldown() -> None:
    provider, model = _provider("embed")
    main_config = _main_config(provider, model.model_name)
    selector = parse_model_selector(f"{provider.name}/{model.model_name}", main_config)
    embed_bundle = _ResolvedBundle(provider=provider, model=model)

    main_pool = RoutePool.from_selector(main_config, selector, cooldown_seconds=0.05, rng=Random(7))
    embed_pool = RoutePool.from_embedding_provider(
        embed_bundle,
        cooldown_seconds=0.05,
        rng=Random(7),
    )

    async def _run() -> tuple[str, str, str, str]:
        main_first = await main_pool.get()
        embed_first = await embed_pool.get()

        await main_pool.mark_error(main_first)
        await embed_pool.mark_error(embed_first)

        main_second = await main_pool.get()
        embed_second = await embed_pool.get()
        return (
            main_first.route_id,
            embed_first.route_id,
            main_second.route_id,
            embed_second.route_id,
        )

    main_first, embed_first, main_second, embed_second = asyncio.run(_run())

    assert main_first == embed_first
    assert main_second == embed_second
    assert main_first != main_second


def test_rerank_route_pool_matches_main_pool_for_quota_backoff() -> None:
    provider, model = _provider("rerank")
    main_config = _main_config(provider, model.model_name)
    selector = parse_model_selector(f"{provider.name}/{model.model_name}", main_config)
    rerank_bundle = _ResolvedBundle(provider=provider, model=model)

    main_pool = RoutePool.from_selector(
        main_config, selector, cooldown_seconds=0.05, rng=Random(11)
    )
    rerank_pool = RoutePool.from_rerank_provider(
        rerank_bundle,
        cooldown_seconds=0.05,
        rng=Random(11),
    )

    async def _run() -> tuple[str, str, str]:
        main_initial = await main_pool.get()
        rerank_initial = await rerank_pool.get()

        flagged_main = await main_pool.mark_quota_exceeded(
            main_initial,
            "rate limit exceeded",
            429,
        )
        flagged_rerank = await rerank_pool.mark_quota_exceeded(
            rerank_initial,
            "rate limit exceeded",
            429,
        )

        main_second = await main_pool.get()
        rerank_second = await rerank_pool.get()

        assert flagged_main is True
        assert flagged_rerank is True
        return main_initial.route_id, main_second.route_id, rerank_second.route_id

    main_initial, main_second, rerank_second = asyncio.run(_run())

    assert main_second == rerank_second
    assert main_second != main_initial
