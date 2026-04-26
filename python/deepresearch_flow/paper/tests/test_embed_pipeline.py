from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import lancedb
import pytest

from deepresearch_flow.paper.embedding import EmbeddingResult
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
from deepresearch_flow.paper.vector_store import (
    _reset_ensured_scalar_index_cache,
    load_index_meta,
    open_store,
    scan_rows,
)


@pytest.fixture(autouse=True)
def _clear_scalar_index_cache() -> None:
    _reset_ensured_scalar_index_cache()
    yield
    _reset_ensured_scalar_index_cache()


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


def _write_two_doc_json(tmp_path: Path) -> Path:
    data = [
        {
            "paper_title": "Test Paper A",
            "summary": "A summary of the first test paper.",
            "source_path": "papers/a.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        },
        {
            "paper_title": "Test Paper B",
            "summary": "A summary of the second test paper.",
            "source_path": "papers/b.md",
            "prompt_template": "simple",
            "paper_authors": ["Author B"],
        },
    ]
    path = tmp_path / "papers-two.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _test_config(
    *,
    api_key: str = "ollama",
    batch_size: int = 2,
    max_concurrency: int = 1,
    chunk_max_tokens: int = 512,
) -> PaperConfig:
    # These pipeline tests exercise embedding-only plumbing and intentionally bypass
    # chat provider/main_model validation by constructing PaperConfig directly.
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
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=64,
            providers=[
                EmbeddingProviderConfig(
                    name="ollama",
                    type="openai_compatible",
                    base=[BaseConfig(url="http://localhost:11434/v1", weight=1, key=[KeyConfig(value=api_key, weight=1)])],
                    models=[EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)],
                )
            ],
        ),
        rerank=None,
        search=None,
    )


def test_pipeline_creates_index_meta(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def fake_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)

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

    async def counting_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ANN001
        nonlocal call_count
        call_count += len(texts)
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", counting_embed)

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


def test_pipeline_upgrades_existing_table_indices_even_when_no_reembed_needed(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", ok_embed)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    existing_rows = scan_rows(open_store(vector_dir))
    raw_db = lancedb.connect(str(vector_dir))
    raw_db.create_table("paper_chunks", data=existing_rows, mode="overwrite")
    assert list(raw_db.open_table("paper_chunks").list_indices()) == []

    async def should_not_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        raise AssertionError("unchanged embed run should not request embeddings")

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", should_not_embed)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    indexed_columns = {
        column
        for index in lancedb.connect(str(vector_dir)).open_table("paper_chunks").list_indices()
        for column in getattr(index, "columns", [])
    }
    assert {"doc_id", "template_tag"} <= indexed_columns


def test_pipeline_resumes_after_partial_failure(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_two_doc_json(tmp_path)
    vector_dir = tmp_path / "vectors"
    seen_texts: list[str] = []
    should_fail = {"value": True}

    async def flaky_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ANN001
        seen_texts.extend(list(texts))
        if should_fail["value"] and any("second test paper" in text.lower() for text in texts):
            raise RuntimeError("embedding backend unavailable")
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", flaky_embed)

    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        asyncio.run(
            run_embed_pipeline(
                config=_test_config(),
                input_paths=[json_path],
                vector_dir=vector_dir,
            )
        )

    rows_after_failure = scan_rows(open_store(vector_dir))
    assert rows_after_failure
    assert "papers/a.md" in {row["source_path"] for row in rows_after_failure}

    should_fail["value"] = False
    first_run_count = len(seen_texts)
    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    rows_after_resume = scan_rows(open_store(vector_dir))
    assert {row["source_path"] for row in rows_after_resume} == {"papers/a.md", "papers/b.md"}
    resumed_texts = seen_texts[first_run_count:]
    assert resumed_texts
    assert all("first test paper" not in text.lower() for text in resumed_texts)


def test_pipeline_embeds_batches_concurrently_with_configured_limit(tmp_path: Path, monkeypatch) -> None:
    json_path = tmp_path / "papers.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "paper_title": "Concurrent Paper",
                    "summary": "One. Two. Three. Four. Five. Six.",
                    "finding_a": "Alpha. Beta. Gamma. Delta.",
                    "finding_b": "Epsilon. Zeta. Eta. Theta.",
                    "source_path": "papers/concurrent.md",
                    "prompt_template": "simple",
                    "paper_authors": ["Author A"],
                }
            ]
        ),
        encoding="utf-8",
    )
    vector_dir = tmp_path / "vectors"
    active_requests = 0
    peak_requests = 0
    started_requests = 0
    release = asyncio.Event()

    async def delayed_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        nonlocal active_requests, peak_requests, started_requests
        if texts == ["Concurrent Paper"]:
            return EmbeddingResult(
                vectors=[[0.1] * 1024 for _ in texts],
                model=model,
                usage_tokens=len(texts),
            )
        active_requests += 1
        started_requests += 1
        peak_requests = max(peak_requests, active_requests)
        if started_requests >= 2:
            release.set()
        await release.wait()
        await asyncio.sleep(0)
        active_requests -= 1
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", delayed_embed)

    asyncio.run(
        asyncio.wait_for(
            run_embed_pipeline(
                config=_test_config(batch_size=1, max_concurrency=2, chunk_max_tokens=2),
                input_paths=[json_path],
                vector_dir=vector_dir,
            ),
            timeout=1.0,
        )
    )

    assert peak_requests == 2
    assert scan_rows(open_store(vector_dir))


def test_pipeline_keeps_existing_rows_when_reembed_fails(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", ok_embed)
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

    async def failing_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", failing_embed)

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

    async def bad_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", bad_embed)

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

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", ok_embed)
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

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", ok_embed)
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


def test_pipeline_embeds_using_active_weighted_route(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    seen: dict[str, object] = {}

    async def fake_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ANN001
        seen.update(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "dimensions": dimensions,
                "provider_type": provider_type,
                "texts": list(texts),
            }
        )
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(api_key="resolved-embed-key"),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    assert seen["base_url"] == "http://localhost:11434/v1"
    assert seen["api_key"] == "resolved-embed-key"
    assert seen["model"] == "bge-m3"
    assert seen["dimensions"] == 1024
    assert seen["provider_type"] == "openai_compatible"


def test_pipeline_shows_tqdm_progress_for_embedding_batches(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"
    tqdm_calls: list[dict[str, object]] = []
    progress_events: list[tuple[str, str, int | None]] = []

    class FakeProgress:
        def __init__(self, desc: str) -> None:
            self.desc = desc
            self.total = 0

        def __enter__(self):
            progress_events.append((self.desc, "enter", None))
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            progress_events.append((self.desc, "exit", None))
            return False

        def update(self, value: int) -> None:
            progress_events.append((self.desc, "update", value))

        def refresh(self) -> None:
            progress_events.append((self.desc, "refresh", self.total))

        def close(self) -> None:
            progress_events.append((self.desc, "close", None))

    def fake_tqdm(*args, **kwargs):  # noqa: ANN002, ANN003
        tqdm_calls.append(kwargs)
        return FakeProgress(str(kwargs.get("desc", "")))

    async def ok_embed(base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None):  # noqa: ARG001, ANN001
        return EmbeddingResult(
            vectors=[[0.1] * 1024 for _ in texts],
            model=model,
            usage_tokens=len(texts),
        )

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.tqdm", fake_tqdm)
    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", ok_embed)

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    assert tqdm_calls
    assert any(call.get("desc") == "prepare chunks" and int(call.get("total", 0)) > 0 for call in tqdm_calls)
    assert any(call.get("desc") == "embed chunks" for call in tqdm_calls)
    assert ("prepare chunks", "enter", None) in progress_events
    assert ("prepare chunks", "update", 1) in progress_events
    assert ("prepare chunks", "exit", None) in progress_events
    assert any(event[0] == "embed chunks" and event[1] == "refresh" and int(event[2] or 0) > 0 for event in progress_events)
    assert ("embed chunks", "update", 2) in progress_events
    assert ("embed chunks", "close", None) in progress_events
