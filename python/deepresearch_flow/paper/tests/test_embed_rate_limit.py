from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deepresearch_flow.paper.config import (
    BaseConfig,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    KeyConfig,
    PaperConfig,
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
)
from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline
from deepresearch_flow.paper.embedding import EmbeddingRateLimiter

# estimate_tokens() maps 4 characters to 1 token, so 4 chars == 1 token.
_ONE_TOKEN_TEXT = "abcd"


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_no_limits_admits_every_call_immediately() -> None:
    limiter = EmbeddingRateLimiter()

    async def scenario() -> None:
        for _ in range(50):
            await asyncio.wait_for(limiter.acquire([_ONE_TOKEN_TEXT]), timeout=0.5)

    _run(scenario())


def test_rpm_limit_delays_calls_beyond_the_limit() -> None:
    limiter = EmbeddingRateLimiter(rpm=2)

    async def scenario() -> None:
        await limiter.acquire([_ONE_TOKEN_TEXT])
        await limiter.acquire([_ONE_TOKEN_TEXT])
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(limiter.acquire([_ONE_TOKEN_TEXT]), timeout=0.5)

    _run(scenario())


def test_tpm_limit_delays_calls_beyond_the_token_budget() -> None:
    limiter = EmbeddingRateLimiter(tpm=2)

    async def scenario() -> None:
        await limiter.acquire([_ONE_TOKEN_TEXT])
        await limiter.acquire([_ONE_TOKEN_TEXT])
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(limiter.acquire([_ONE_TOKEN_TEXT]), timeout=0.5)

    _run(scenario())


def test_batch_larger_than_tpm_budget_is_rejected() -> None:
    limiter = EmbeddingRateLimiter(tpm=2)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="tokens/minute"):
            await limiter.acquire([_ONE_TOKEN_TEXT] * 5)

    _run(scenario())


def _config() -> PaperConfig:
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[],
        main_model=[],
        embedding=EmbeddingConfig(
            default_model="bge-m3",
            default_provider="ollama",
            dimensions=1024,
            normalized=True,
            batch_size=2,
            max_concurrency=1,
            chunk_max_tokens=512,
            chunk_overlap_tokens=64,
            providers=[
                EmbeddingProviderConfig(
                    name="ollama",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="http://localhost:11434/v1",
                            weight=1,
                            key=[KeyConfig(value="ollama", weight=1)],
                        )
                    ],
                    models=[
                        EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)
                    ],
                )
            ],
        ),
        rerank=None,
        search=None,
    )


def _write_json(tmp_path: Path) -> Path:
    payload = [
        {
            "paper_title": "Rate Limited Paper",
            "summary": "A summary long enough to need more than a handful of tokens.",
            "source_path": "papers/rate.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        }
    ]
    path = tmp_path / "papers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pipeline_rejects_batch_that_exceeds_tpm(tmp_path: Path) -> None:
    json_path = _write_json(tmp_path)

    with pytest.raises(ValueError, match="tokens/minute"):
        _run(
            run_embed_pipeline(
                config=_config(),
                input_paths=[json_path],
                vector_dir=tmp_path / "vectors",
                tpm_limit=1,
            )
        )
