from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _write_config(tmp_path: Path, *, embedding_section: str, rerank_section: str = "") -> Path:
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
        batch_size = 32
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64
        __EMBEDDING__

        __RERANK__

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        """.replace("__EMBEDDING__", embedding_section).replace("__RERANK__", rerank_section),
        encoding="utf-8",
    )
    return config_path


def _load_config(tmp_path: Path, *, embedding_section: str, rerank_section: str = ""):
    return load_config(str(_write_config(tmp_path, embedding_section=embedding_section, rerank_section=rerank_section)))


def test_loads_embedding_providers_with_bases_and_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = _load_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [
          { url = "http://localhost:11434/v1", weight = 2, key = [{ value = "env:SF_KEY", weight = 3, quota_duration = 30, quota_error_tokens = ["rate_limit", "quota"] }, { value = "ollama-fallback", weight = 1 }] },
          { url = "http://localhost:11435/v1", weight = 1, key = [{ value = "ollama-secondary", weight = 2 }] }
        ]
        models = [
          { model_name = "bge-m3", dimensions = 1024, max_context = 8192 },
          { model_name = "bge-large", dimensions = 1024, max_context = 32768 }
        ]

        [[embedding.providers]]
        name = "siliconflow"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "env:SF_KEY", weight = 1 }] }]
        models = [{ model_name = "Qwen/Qwen3-Embedding-4B", dimensions = 2560, max_context = 32768 }]
        """,
    )

    assert config.embedding is not None
    assert config.embedding.default_model == "bge-m3"
    assert config.embedding.default_provider == "ollama"
    assert config.embedding.providers[0].type == "openai_compatible"

    ollama = config.embedding.providers[0]
    assert len(ollama.base) == 2
    assert ollama.base[0].url == "http://localhost:11434/v1"
    assert ollama.base[0].weight == 2
    assert ollama.base[0].key[0].value == "env:SF_KEY"
    assert ollama.base[0].key[0].weight == 3
    assert ollama.base[0].key[0].quota_duration == 30
    assert ollama.base[0].key[0].quota_error_tokens == ["rate_limit", "quota"]
    assert ollama.base[0].key[1].value == "ollama-fallback"
    assert ollama.base[1].url == "http://localhost:11435/v1"
    assert ollama.base[1].key[0].value == "ollama-secondary"
    assert ollama.models[0].model_name == "bge-m3"
    assert ollama.models[0].dimensions == 1024
    assert ollama.models[0].max_context == 8192

    siliconflow = config.embedding.providers[1]
    assert siliconflow.type == "openai_compatible"
    assert siliconflow.base[0].key[0].value == "env:SF_KEY"


def test_loads_rerank_providers_with_bases_and_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = _load_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
        """,
        rerank_section="""
        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 10

        [[rerank.providers]]
        name = "siliconflow"
        type = "openai_compatible"
        base = [
          { url = "https://api.siliconflow.cn/v1", weight = 4, key = [{ value = "env:SF_KEY", weight = 2, quota_error_tokens = ["quota"] }, { value = "rerank-fallback", weight = 1 }] }
        ]
        models = [
          { model_name = "bge-reranker-v2-m3", max_context = 8192, max_chunks_per_doc = 1024 },
          { model_name = "Qwen/Qwen3-Reranker-8B", max_context = 32768, instruction = "Rerank by relevance" }
        ]
        """,
    )

    assert config.rerank is not None
    assert config.rerank.default_model == "bge-reranker-v2-m3"
    assert config.rerank.default_provider == "siliconflow"
    assert config.rerank.providers[0].type == "openai_compatible"

    provider = config.rerank.providers[0]
    assert len(provider.base) == 1
    assert provider.base[0].url == "https://api.siliconflow.cn/v1"
    assert provider.base[0].weight == 4
    assert provider.base[0].key[0].value == "env:SF_KEY"
    assert provider.base[0].key[0].weight == 2
    assert provider.base[0].key[0].quota_error_tokens == ["quota"]
    assert provider.base[0].key[1].value == "rerank-fallback"
    assert provider.models[0].model_name == "bge-reranker-v2-m3"
    assert provider.models[0].max_context == 8192
    assert provider.models[0].max_chunks_per_doc == 1024
    assert provider.models[1].instruction == "Rerank by relevance"


@pytest.mark.parametrize(
    ("legacy_field", "provider_kind"),
    [
        ("base_url", "embedding"),
        ("api_key", "embedding"),
    ],
)
def test_embedding_rejects_legacy_provider_fields(
    tmp_path: Path, legacy_field: str, provider_kind: str
) -> None:
    field_value = (
        '"http://localhost:11434/v1"' if legacy_field == "base_url" else '"ollama"'
    )
    with pytest.raises(ValueError, match=f"legacy provider field '{legacy_field}'"):
        _load_config(
            tmp_path,
            embedding_section=f"""
            [[embedding.providers]]
            name = "ollama"
            {legacy_field} = {field_value}
            models = [{{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }}]
            """,
        )


@pytest.mark.parametrize(
    ("legacy_field", "provider_kind"),
    [
        ("base_url", "rerank"),
        ("api_key", "rerank"),
    ],
)
def test_rerank_rejects_legacy_provider_fields(
    tmp_path: Path, legacy_field: str, provider_kind: str
) -> None:
    field_value = (
        '"https://api.siliconflow.cn/v1"' if legacy_field == "base_url" else '"rerank"'
    )
    with pytest.raises(ValueError, match=f"legacy provider field '{legacy_field}'"):
        _load_config(
            tmp_path,
            embedding_section="""
            [[embedding.providers]]
            name = "ollama"
            base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
            models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
            """,
            rerank_section=f"""
            [rerank]
            enabled = true
            default_model = "bge-reranker-v2-m3"
            default_provider = "siliconflow"
            top_n = 10

            [[rerank.providers]]
            name = "siliconflow"
            {legacy_field} = {field_value}
            models = [{{ model_name = "bge-reranker-v2-m3", max_context = 8192 }}]
            """,
        )


def test_resolves_embedding_provider_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = _load_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
        """,
        rerank_section="""
        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 10

        [[rerank.providers]]
        name = "siliconflow"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "rerank", weight = 1 }] }]
        models = [{ model_name = "bge-reranker-v2-m3", max_context = 8192 }]
        """,
    )
    provider = config.embedding.providers[0]
    model = provider.models[0]
    assert config.embedding.default_provider == provider.name
    assert config.embedding.default_model == model.model_name
    assert model.dimensions == 1024


def test_embedding_dimensions_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = _load_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 2560, max_context = 8192 }]
        """,
        rerank_section="""
        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 10

        [[rerank.providers]]
        name = "siliconflow"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "rerank", weight = 1 }] }]
        models = [{ model_name = "bge-reranker-v2-m3", max_context = 8192 }]
        """,
    )
    with pytest.raises(ValueError, match="dimensions"):
        config.embedding.resolve_active()


def test_chunk_max_tokens_exceeds_max_context_raises(tmp_path: Path) -> None:
    config = _load_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 100 }]
        """,
        rerank_section="""
        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 5

        [[rerank.providers]]
        name = "siliconflow"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "rerank", weight = 1 }] }]
        models = [{ model_name = "bge-reranker-v2-m3", max_context = 8192 }]
        """,
    )
    with pytest.raises(ValueError, match="max_context"):
        config.embedding.resolve_active()


def test_model_capability_has_no_embedding_rerank_flags(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
        """,
    )
    loaded = load_config(str(config))
    model = loaded.providers[0].models[0]
    assert not hasattr(model, "is_support_embedding")
    assert not hasattr(model, "is_support_rerank")


def test_rerank_disabled_without_providers(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
        """,
        rerank_section="""
        [rerank]
        enabled = false
        top_n = 5
        """,
    )

    loaded = load_config(str(config))
    assert loaded.rerank is not None
    assert loaded.rerank.enabled is False
    assert loaded.rerank.providers == []


def test_search_access_token_env_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_ACCESS_TOKEN", "secret-search-token")
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = _write_config(
        tmp_path,
        embedding_section="""
        [[embedding.providers]]
        name = "ollama"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }]
        """,
        rerank_section="""
        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "siliconflow"
        top_n = 10

        [[rerank.providers]]
        name = "siliconflow"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "rerank", weight = 1 }] }]
        models = [{ model_name = "bge-reranker-v2-m3", max_context = 8192 }]
        """,
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            """[search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        """,
            """[search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        access_token = "env:SEARCH_ACCESS_TOKEN"
        """,
        ),
        encoding="utf-8",
    )
    loaded = load_config(str(config))
    assert loaded.search is not None
    assert loaded.search.access_token == "secret-search-token"
