from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _full_config_v2(tmp_path: Path, extra: str = "") -> Path:
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
        default_model = "Qwen3-Embedding-4B"
        default_provider = "ollama"
        dimensions = 1024
        normalized = true
        batch_size = 32
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64

        [[embedding.providers]]
        name = "ollama"
        type = "openai_compatible"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
        models = [
          { model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 32768 },
          { model_name = "bge-m3", dimensions = 1024, max_context = 8192 }
        ]

        [[embedding.providers]]
        name = "siliconflow"
        type = "openai_compatible"
        base_url = "https://api.siliconflow.cn/v1"
        api_key = "env:SF_KEY"
        models = [
          { model_name = "Qwen/Qwen3-Embedding-4B", dimensions = 2560, max_context = 32768 }
        ]

        [rerank]
        enabled = true
        default_model = "BAAI/bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 10

        [[rerank.providers]]
        name = "siliconflow"
        type = "openai_compatible"
        base_url = "https://api.siliconflow.cn/v1"
        api_key = "env:SF_KEY"
        models = [
          { model_name = "BAAI/bge-reranker-v2-m3", max_context = 8192, max_chunks_per_doc = 1024 },
          { model_name = "Qwen/Qwen3-Reranker-8B", max_context = 32768, instruction = "Rerank by relevance" }
        ]

        """
        + extra,
        encoding="utf-8",
    )
    return config_path


def test_loads_embedding_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    assert config.embedding is not None
    assert config.embedding.default_model == "Qwen3-Embedding-4B"
    assert config.embedding.default_provider == "ollama"
    assert len(config.embedding.providers) == 2
    ollama = config.embedding.providers[0]
    assert ollama.name == "ollama"
    assert ollama.base_url == "http://localhost:11434/v1"
    assert len(ollama.models) == 2
    assert ollama.models[0].model_name == "Qwen3-Embedding-4B"
    assert ollama.models[0].dimensions == 1024
    assert ollama.models[0].max_context == 32768


def test_loads_rerank_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    assert config.rerank is not None
    assert config.rerank.default_model == "BAAI/bge-reranker-v2-m3"
    assert config.rerank.default_provider == "siliconflow"
    assert len(config.rerank.providers) == 1
    sf = config.rerank.providers[0]
    assert sf.models[0].max_context == 8192
    assert sf.models[0].max_chunks_per_doc == 1024
    assert sf.models[1].instruction == "Rerank by relevance"


def test_resolves_embedding_provider_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    provider, model = config.embedding.resolve_active()
    assert provider.name == "ollama"
    assert model.model_name == "Qwen3-Embedding-4B"
    assert model.dimensions == 1024


def test_embedding_dimensions_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    config_sf = replace(
        config.embedding,
        default_provider="siliconflow",
        default_model="Qwen/Qwen3-Embedding-4B",
    )
    with pytest.raises(ValueError, match="dimensions"):
        config_sf.resolve_active()


def test_chunk_max_tokens_exceeds_max_context_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [embedding]
        default_model = "tiny"
        default_provider = "local"
        dimensions = 128
        normalized = true
        chunk_max_tokens = 512

        [[embedding.providers]]
        name = "local"
        type = "openai_compatible"
        base_url = "http://localhost/v1"
        api_key = "k"
        models = [{ model_name = "tiny", dimensions = 128, max_context = 100 }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    with pytest.raises(ValueError, match="max_context"):
        config.embedding.resolve_active()


def test_model_capability_has_no_embedding_rerank_flags(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    model = config.providers[0].models[0]
    assert not hasattr(model, "is_support_embedding")
    assert not hasattr(model, "is_support_rerank")


def test_rerank_disabled_without_providers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [embedding]
        default_model = "tiny"
        default_provider = "local"
        dimensions = 128
        normalized = true

        [[embedding.providers]]
        name = "local"
        type = "openai_compatible"
        base_url = "http://localhost/v1"
        api_key = "k"
        models = [{ model_name = "tiny", dimensions = 128, max_context = 2048 }]

        [rerank]
        enabled = false
        top_n = 5
        """,
        encoding="utf-8",
    )

    config = load_config(str(config_path))
    assert config.rerank is not None
    assert config.rerank.enabled is False
    assert config.rerank.providers == []


def test_search_access_token_env_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_ACCESS_TOKEN", "secret-search-token")
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config_path = _full_config_v2(
        tmp_path,
        extra="""
        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        access_token = "env:SEARCH_ACCESS_TOKEN"
        """,
    )
    config = load_config(str(config_path))
    assert config.search is not None
    assert config.search.access_token == "secret-search-token"
