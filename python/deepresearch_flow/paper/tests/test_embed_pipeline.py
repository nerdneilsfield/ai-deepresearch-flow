from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deepresearch_flow.paper.embedding import EmbeddingResult
from deepresearch_flow.paper.config import (
    BaseConfig,
    EmbeddingConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
)
from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline
from deepresearch_flow.paper.vector_store import open_store, scan_rows
from deepresearch_flow.paper.vector_store import load_index_meta


def _write_json(tmp_path: Path) -> Path:
    data = [
        {
            "paper_title": "Test Paper",
            "summary": "A summary of the test paper.",
            "source_path": "papers/test.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        }
    ]
    path = tmp_path / "papers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _test_config() -> PaperConfig:
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[
            ProviderConfig(
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
                    ModelCapability(
                        model_name="bge-m3",
                        is_stream=False,
                        is_support_json_schema=False,
                        is_support_json_object=False,
                        is_support_embedding=True,
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
        ],
        main_model=[MainModelConfig(model="ollama/bge-m3", weight=1)],
        embedding=EmbeddingConfig(
            model="bge-m3",
            dimensions=1024,
            normalized=True,
            batch_size=2,
            chunk_max_tokens=512,
            chunk_overlap_tokens=64,
            provider="ollama",
        ),
        rerank=None,
        search=None,
    )


def test_pipeline_creates_index_meta(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def fake_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):
        from deepresearch_flow.paper.embedding import EmbeddingResult

        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", fake_embed)

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    assert (vector_dir / "index_meta.json").exists()


def test_pipeline_incremental_skips_unchanged(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"
    call_count = 0

    async def counting_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):
        nonlocal call_count
        call_count += len(texts)
        from deepresearch_flow.paper.embedding import EmbeddingResult

        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", counting_embed)

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )
    first_count = call_count

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    assert call_count == first_count


def test_pipeline_keeps_existing_rows_when_reembed_fails(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ARG001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", ok_embed)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )
    original_rows = scan_rows(open_store(vector_dir))
    assert original_rows

    updated = [
        {
            "paper_title": "Test Paper",
            "summary": "Updated summary text to force re-embedding.",
            "source_path": "papers/test.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        }
    ]
    json_path.write_text(json.dumps(updated), encoding="utf-8")

    async def failing_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ARG001
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", failing_embed)

    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        asyncio.run(
            run_embed_pipeline(
                config=_test_config(),
                input_paths=[json_path],
                vector_dir=vector_dir,
            )
        )

    rows_after_failure = scan_rows(open_store(vector_dir))
    assert rows_after_failure == original_rows


def test_pipeline_reports_batch_size_mismatch(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def bad_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ARG001
        return EmbeddingResult(
            vectors=[[0.1] * 1024],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", bad_embed)

    with pytest.raises(ValueError, match="returned 1 vectors for batch of 2 texts"):
        asyncio.run(
            run_embed_pipeline(
                config=_test_config(),
                input_paths=[json_path],
                vector_dir=vector_dir,
            )
        )


def test_pipeline_assigns_monotonic_chunk_index_per_template_and_type(tmp_path: Path, monkeypatch) -> None:
    json_path = tmp_path / "papers.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "paper_title": "Test Paper",
                    "summary": "A summary of the test paper.",
                    "finding_a": "First content block.",
                    "finding_b": "Second content block.",
                    "source_path": "papers/test.md",
                    "prompt_template": "simple",
                    "paper_authors": ["Author A"],
                }
            ]
        ),
        encoding="utf-8",
    )
    vector_dir = tmp_path / "vectors"

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ARG001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", ok_embed)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    rows = scan_rows(open_store(vector_dir))
    content_rows = [row for row in rows if row["template_tag"] == "simple" and row["chunk_type"] == "content"]
    assert [row["chunk_index"] for row in content_rows] == list(range(len(content_rows)))
    assert len({row["id"] for row in content_rows}) == len(content_rows)


def test_pipeline_updates_index_meta_stats(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ARG001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", ok_embed)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    meta = load_index_meta(vector_dir)
    assert meta["doc_count"] == 1
    assert meta["template_count"] == 1
    assert meta["chunk_count"] > 0
    assert isinstance(meta["last_updated"], str) and meta["last_updated"]
