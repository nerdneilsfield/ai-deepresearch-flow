from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper.config import load_config


def _write_config(tmp_path: Path, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [embedding]
        model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 32
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64
        provider = "ollama"

        [rerank]
        enabled = true
        model = "BAAI/bge-reranker-v2-m3"
        top_n = 10
        provider = "siliconflow"

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        access_token = "env:SEARCH_ACCESS_TOKEN"

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_embedding = true }]

        [[providers]]
        name = "siliconflow"
        type = "openai_compatible"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "test-sf-key", weight = 1 }] }]
        models = [{ model_name = "BAAI/bge-reranker-v2-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_rerank = true }]
        """
        + extra,
        encoding="utf-8",
    )
    return config_path


def test_loads_embedding_rerank_search_sections(tmp_path: Path) -> None:
    config = load_config(str(_write_config(tmp_path)))

    assert config.embedding is not None
    assert config.embedding.model == "bge-m3"
    assert config.embedding.dimensions == 1024
    assert config.embedding.normalized is True
    assert config.embedding.batch_size == 32
    assert config.embedding.chunk_max_tokens == 512
    assert config.embedding.chunk_overlap_tokens == 64
    assert config.embedding.provider == "ollama"

    assert config.rerank is not None
    assert config.rerank.enabled is True
    assert config.rerank.model == "BAAI/bge-reranker-v2-m3"
    assert config.rerank.top_n == 10
    assert config.rerank.provider == "siliconflow"

    assert config.search is not None
    assert config.search.vector_dir == "paper_vectors"
    assert config.search.vector_top_k == 50
    assert config.search.keyword_top_k == 30
    assert config.search.hybrid is True
    assert config.search.access_token == "env:SEARCH_ACCESS_TOKEN"


def test_model_capability_defaults_embedding_and_rerank_false(tmp_path: Path) -> None:
    config = load_config(str(_write_config(tmp_path)))

    openai_model = config.providers[0].models[0]
    assert openai_model.is_support_embedding is False
    assert openai_model.is_support_rerank is False


def test_model_capability_reads_embedding_and_rerank_flags(tmp_path: Path) -> None:
    config = load_config(str(_write_config(tmp_path)))

    ollama_model = config.providers[1].models[0]
    rerank_model = config.providers[2].models[0]

    assert ollama_model.is_support_embedding is True
    assert ollama_model.is_support_rerank is False
    assert rerank_model.is_support_embedding is False
    assert rerank_model.is_support_rerank is True


def test_embedding_rerank_search_sections_are_optional(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.embedding is None
    assert config.rerank is None
    assert config.search is None
