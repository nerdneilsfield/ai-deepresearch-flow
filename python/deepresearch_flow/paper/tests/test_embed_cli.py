from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.config import (
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
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
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
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


def test_paper_search_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["paper", "search", "--help"])
    assert result.exit_code == 0
    assert "--embed-db" in result.output
    assert "--no-rerank" in result.output
    assert "--no-hybrid" in result.output


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


def _search_config(*, embedding_api_key: str = "ollama", rerank_api_key: str = "rerank") -> PaperConfig:
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
                    base_url="http://localhost:11434/v1",
                    api_key=embedding_api_key,
                    models=[EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)],
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
                    base_url="https://api.siliconflow.cn/v1",
                    api_key=rerank_api_key,
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
        search=SearchConfig(vector_dir="paper_vectors", vector_top_k=50, keyword_top_k=30, hybrid=True),
    )


def test_run_search_uses_embedding_and_rerank_resolve_active(monkeypatch, tmp_path: Path) -> None:
    from deepresearch_flow.paper.cli import _run_search
    from deepresearch_flow.paper.embedding import EmbeddingResult

    def boom_select_runtime_route(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("select_runtime_route should not be used in paper search")

    monkeypatch.setattr("deepresearch_flow.paper.routing.select_runtime_route", boom_select_runtime_route)
    monkeypatch.setattr("deepresearch_flow.paper.vector_store.open_store", lambda _: object())
    monkeypatch.setattr("deepresearch_flow.paper.vector_store.scan_rows", lambda _: [{"doc_id": "doc-1", "title": "Attention Paper", "text": "body text", "authors": "Author A", "venue": "NeurIPS", "tags": "transformer"}])
    monkeypatch.setattr("rich.console.Console.print", lambda self, table: None)

    seen: dict[str, object] = {}

    async def fake_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):  # noqa: ANN001
        seen["embed"] = {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "dimensions": dimensions,
        }
        return EmbeddingResult(vectors=[[0.1] * 1024], model=model, usage_tokens=1)

    class FakeReranker:
        def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            model: str,
            max_context: int,
            max_chunks_per_doc: int | None,
            instruction: str | None,
        ) -> None:
            seen["reranker"] = {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "max_context": max_context,
                "max_chunks_per_doc": max_chunks_per_doc,
                "instruction": instruction,
            }

    async def fake_hybrid_search(**kwargs):  # noqa: ANN003
        assert kwargs["reranker"] is not None
        assert kwargs["rerank_top_n"] == 5
        assert kwargs["document_text_resolver"]("doc-1").startswith("Attention Paper")
        return []

    monkeypatch.setattr("deepresearch_flow.paper.embedding.call_embedding", fake_embed)
    monkeypatch.setattr("deepresearch_flow.paper.reranker.OpenAICompatibleReranker", FakeReranker)
    monkeypatch.setattr("deepresearch_flow.paper.search.hybrid_search", fake_hybrid_search)

    asyncio.run(
        _run_search(
            config=_search_config(embedding_api_key="resolved-embed-key", rerank_api_key="resolved-rerank-key"),
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
    }
    assert seen["reranker"] == {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "resolved-rerank-key",
        "model": "bge-reranker-v2-m3",
        "max_context": 2048,
        "max_chunks_per_doc": 128,
        "instruction": "Rank by relevance",
    }
