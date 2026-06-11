from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.config import (
    BaseConfig,
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    KeyConfig,
    PaperConfig,
    RerankConfig,
    RerankModelConfig,
    RerankProviderConfig,
    SearchConfig,
)


def _write_embed_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [embedding]
        default_model = "bge-m3"
        default_provider = "ollama"
        dimensions = 1024
        normalized = true
        batch_size = 2
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64

        [[embedding.providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        """,
        encoding="utf-8",
    )
    return config_path


def test_paper_embed_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["paper", "embed", "--help"])
    assert result.exit_code == 0
    assert "--output-embed-db" in result.output
    assert "--snapshot-db" in result.output
    assert "--force" in result.output
    assert "--template-tag" in result.output
    assert "--embedding" in result.output
    assert "--max-concurrency" in result.output
    assert "--document-window" in result.output


def test_paper_search_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["paper", "search", "--help"])
    assert result.exit_code == 0
    assert "--embed-db" in result.output
    assert "--no-rerank" in result.output
    assert "--no-hybrid" in result.output
    assert "--embedding" in result.output
    assert "--rerank" in result.output


def test_paper_embed_rejects_no_input(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    result = runner.invoke(cli, ["paper", "embed", "-c", str(config_path)])
    assert result.exit_code != 0
    assert "input" in result.output.lower() or "snapshot" in result.output.lower()


def test_paper_embed_rejects_mixed_sources(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")
    result = runner.invoke(
        cli,
        [
            "paper",
            "embed",
            "-c",
            str(config_path),
            "-i",
            str(json_path),
            "--snapshot-db",
            "fake.db",
            "--static-export-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0


def test_paper_embed_fails_fast_when_vector_store_preflight_fails(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")

    def boom_preflight(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Vector store preflight failed for /mnt/d/vectors")

    def should_not_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("run_embed_pipeline should not run when preflight fails")

    monkeypatch.setattr(
        "deepresearch_flow.paper.vector_store.preflight_vector_store", boom_preflight
    )
    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.run_embed_pipeline", should_not_run)

    result = runner.invoke(
        cli,
        [
            "paper",
            "embed",
            "-c",
            str(config_path),
            "-i",
            str(json_path),
            "--output-embed-db",
            str(tmp_path / "vectors"),
        ],
    )

    assert result.exit_code != 0
    assert "preflight failed" in result.output.lower()


def test_paper_embed_passes_max_concurrency_override(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")
    seen: dict[str, object] = {}

    def ok_preflight(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def fake_run_embed_pipeline(**kwargs):  # noqa: ANN003
        seen.update(kwargs)

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.preflight_vector_store", ok_preflight)
    monkeypatch.setattr(
        "deepresearch_flow.paper.embed_pipeline.run_embed_pipeline", fake_run_embed_pipeline
    )

    result = runner.invoke(
        cli,
        [
            "paper",
            "embed",
            "-c",
            str(config_path),
            "-i",
            str(json_path),
            "--output-embed-db",
            str(tmp_path / "vectors"),
            "--max-concurrency",
            "3",
            "--document-window",
            "5",
        ],
    )

    assert result.exit_code == 0
    assert seen["max_concurrency_override"] == 3
    assert seen["document_window_override"] == 5


def test_paper_embed_rejects_nonpositive_max_concurrency(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "paper",
            "embed",
            "-c",
            str(config_path),
            "-i",
            str(json_path),
            "--max-concurrency",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--max-concurrency must be positive" in result.output


def test_paper_embed_rejects_nonpositive_document_window(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "paper",
            "embed",
            "-c",
            str(config_path),
            "-i",
            str(json_path),
            "--document-window",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--document-window must be positive" in result.output


def test_paper_search_rejects_invalid_venue_filter(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    embed_dir = tmp_path / "paper_vectors"
    embed_dir.mkdir()
    result = runner.invoke(
        cli,
        [
            "paper",
            "search",
            "-c",
            str(config_path),
            "--embed-db",
            str(embed_dir),
            "--query",
            "attention",
            "--venue",
            "NeurIPS' OR 1=1",
        ],
    )
    assert result.exit_code != 0
    assert "venue" in result.output.lower()


def _search_config(
    *, embedding_api_key: str = "ollama", rerank_api_key: str = "rerank"
) -> PaperConfig:
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
                            key=[KeyConfig(value=embedding_api_key, weight=1)],
                        )
                    ],
                    models=[
                        EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)
                    ],
                )
            ],
        ),
        rerank=RerankConfig(
            enabled=True,
            default_model="bge-reranker-v2-m3",
            default_provider="siliconflow",
            top_n=5,
            providers=[
                RerankProviderConfig(
                    name="siliconflow",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="https://api.siliconflow.cn/v1",
                            weight=1,
                            key=[KeyConfig(value=rerank_api_key, weight=1)],
                        )
                    ],
                    models=[
                        RerankModelConfig(
                            model_name="bge-reranker-v2-m3",
                            max_context=2048,
                            max_chunks_per_doc=128,
                            instruction="Rank by relevance",
                        )
                    ],
                )
            ],
        ),
        search=SearchConfig(
            vector_dir="paper_vectors", vector_top_k=50, keyword_top_k=30, hybrid=True
        ),
    )


def test_run_search_uses_embedding_and_rerank_resolve_active(monkeypatch, tmp_path: Path) -> None:
    from deepresearch_flow.paper.cli import _run_search
    from deepresearch_flow.paper.embedding import EmbeddingResult

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda _: object())
    monkeypatch.setattr(
        "deepresearch_flow.paper.vector_store.scan_rows",
        lambda _: [
            {
                "doc_id": "doc-1",
                "title": "Attention Paper",
                "text": "body text",
                "authors": "Author A",
                "venue": "NeurIPS",
                "tags": "transformer",
            }
        ],
    )
    monkeypatch.setattr("rich.console.Console.print", lambda self, table: None)

    seen: dict[str, object] = {}

    async def fake_embed(
        base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None
    ):  # noqa: ANN001
        seen["embed"] = {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "dimensions": dimensions,
            "provider_type": provider_type,
        }
        return EmbeddingResult(vectors=[[0.1] * 1024], model=model, usage_tokens=1)

    class FakeReranker:
        def __init__(
            self,
            *,
            route_pool,
        ) -> None:
            seen["reranker_route_pool"] = route_pool

        async def rerank(self, query, documents, *, top_n, client):  # noqa: ANN001
            route = await seen["reranker_route_pool"].get()
            seen["reranker"] = {
                "base_url": route.base.url,
                "api_key": route.key.value,
                "model": route.model.model_name,
                "max_context": route.model.max_context,
                "max_chunks_per_doc": route.model.max_chunks_per_doc,
                "instruction": route.model.instruction,
            }
            return type("RerankResult", (), {"indices": [0], "scores": [1.0]})()

    async def fake_hybrid_search(**kwargs):  # noqa: ANN003
        assert kwargs["reranker"] is not None
        assert kwargs["rerank_top_n"] == 5
        assert kwargs["document_text_resolver"]("doc-1").startswith("Attention Paper")
        await kwargs["reranker"].rerank("attention", ["doc text"], top_n=1, client=None)
        return []

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)
    monkeypatch.setattr("deepresearch_flow.paper.reranker.RoutedReranker", FakeReranker)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    asyncio.run(
        _run_search(
            config=_search_config(
                embedding_api_key="resolved-embed-key", rerank_api_key="resolved-rerank-key"
            ),
            vector_dir=tmp_path / "vectors",
            query_text="attention",
            top_n=5,
            year=None,
            venue=None,
            no_rerank=False,
            no_hybrid=False,
        )
    )

    assert seen["embed"] == {
        "base_url": "http://localhost:11434/v1",
        "api_key": "resolved-embed-key",
        "model": "bge-m3",
        "dimensions": 1024,
        "provider_type": "openai_compatible",
    }
    assert seen["reranker"] == {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "resolved-rerank-key",
        "model": "bge-reranker-v2-m3",
        "max_context": 2048,
        "max_chunks_per_doc": 128,
        "instruction": "Rank by relevance",
    }


def test_run_search_applies_embedding_and_rerank_overrides(monkeypatch, tmp_path: Path) -> None:
    from deepresearch_flow.paper.cli import _run_search
    from deepresearch_flow.paper.embedding import EmbeddingResult

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda _: object())
    monkeypatch.setattr("deepresearch_flow.paper.vector_store.scan_rows", lambda _: [])
    monkeypatch.setattr("rich.console.Console.print", lambda self, table: None)

    config = _search_config()
    config = replace(
        config,
        embedding=replace(
            config.embedding,
            providers=config.embedding.providers
            + [
                EmbeddingProviderConfig(
                    name="backup",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="http://localhost:2242/v1",
                            weight=1,
                            key=[KeyConfig(value="backup-key", weight=1)],
                        )
                    ],
                    models=[
                        EmbeddingModelConfig(
                            model_name="embed-alt", dimensions=1024, max_context=8192
                        )
                    ],
                )
            ],
        ),
        rerank=replace(
            config.rerank,
            providers=config.rerank.providers
            + [
                RerankProviderConfig(
                    name="rerank-alt",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="https://rerank-alt.example/v1",
                            weight=1,
                            key=[KeyConfig(value="rerank-alt-key", weight=1)],
                        )
                    ],
                    models=[RerankModelConfig(model_name="rerank-alt-model", max_context=4096)],
                )
            ],
        ),
    )

    seen: dict[str, object] = {}

    async def fake_embed(
        base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None
    ):  # noqa: ANN001
        seen["embed"] = {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "provider_type": provider_type,
        }
        return EmbeddingResult(vectors=[[0.1] * 1024], model=model, usage_tokens=1)

    class FakeRoutedReranker:
        def __init__(self, *, route_pool) -> None:  # noqa: ANN001
            self._route_pool = route_pool

        async def rerank(self, query, documents, *, top_n, client):  # noqa: ANN001
            route = await self._route_pool.get()
            seen["rerank"] = {
                "base_url": route.base.url,
                "api_key": route.key.value,
                "model": route.model.model_name,
            }
            return type("RerankResult", (), {"indices": [0], "scores": [1.0]})()

    async def fake_hybrid_search(**kwargs):  # noqa: ANN003
        await kwargs["reranker"].rerank("attention", ["doc text"], top_n=1, client=None)
        return []

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)
    monkeypatch.setattr("deepresearch_flow.paper.reranker.RoutedReranker", FakeRoutedReranker)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    asyncio.run(
        _run_search(
            config=config,
            vector_dir=tmp_path / "vectors",
            query_text="attention",
            top_n=5,
            year=None,
            venue=None,
            no_rerank=False,
            no_hybrid=False,
            embedding_override="backup/embed-alt",
            rerank_override="rerank-alt/rerank-alt-model",
        )
    )

    assert seen["embed"] == {
        "base_url": "http://localhost:2242/v1",
        "api_key": "backup-key",
        "model": "embed-alt",
        "provider_type": "openai_compatible",
    }
    assert seen["rerank"] == {
        "base_url": "https://rerank-alt.example/v1",
        "api_key": "rerank-alt-key",
        "model": "rerank-alt-model",
    }


def test_run_search_verbose_emits_formal_stage_messages(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from deepresearch_flow.paper.cli import _run_search
    from deepresearch_flow.paper.embedding import EmbeddingResult

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda _: object())
    monkeypatch.setattr(
        "deepresearch_flow.paper.vector_store.scan_rows",
        lambda _: [
            {
                "doc_id": "doc-1",
                "title": "Attention Paper",
                "text": "body text",
                "authors": "Author A",
                "venue": "NeurIPS",
                "tags": "transformer",
            }
        ],
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.vector_store.query_vector",
        lambda db, query_vector, top_k=50, where=None: [  # noqa: ARG005
            {
                "doc_id": "doc-1",
                "text": "Attention body",
                "_distance": 0.2,
                "field_name": "summary",
                "template_tag": "simple",
                "chunk_type": "abstract",
                "lang": "",
            }
        ],
    )
    monkeypatch.setattr("rich.console.Console.print", lambda self, table: None)

    async def fake_embed(
        base_url, api_key, model, texts, *, dimensions=None, client=None, provider_type=None
    ):  # noqa: ANN001
        return EmbeddingResult(vectors=[[0.1] * 1024], model=model, usage_tokens=1)

    class FakeRoutedReranker:
        def __init__(self, *, route_pool) -> None:  # noqa: ANN001
            self._route_pool = route_pool

        async def rerank(self, query, documents, *, top_n, client):  # noqa: ANN001
            return type("RerankResult", (), {"indices": [0], "scores": [0.99]})()

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)
    monkeypatch.setattr("deepresearch_flow.paper.reranker.RoutedReranker", FakeRoutedReranker)

    asyncio.run(
        _run_search(
            config=_search_config(
                embedding_api_key="resolved-embed-key", rerank_api_key="resolved-rerank-key"
            ),
            vector_dir=tmp_path / "vectors",
            query_text="attention",
            top_n=5,
            year=None,
            venue=None,
            no_rerank=False,
            no_hybrid=False,
            verbose=True,
        )
    )

    output = capsys.readouterr().out
    assert "Embedding query: starting." in output
    assert "Embedding query: completed." in output
    assert "Rerank: enabled with model" in output
    assert "Vector retrieval: completed with" in output
    assert "Keyword retrieval: completed with" in output
    assert "Search completed: returned" in output
