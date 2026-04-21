from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _write_config(
    tmp_path: Path,
    *,
    main_base_extra: str = "",
    embedding_base_extra: str = "",
    rerank_base_extra: str = "",
) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
        main_model = [{{ model = "openai/gpt-4.1", weight = 1 }}]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{{ url = "https://api.example.com/v1", weight = 1, {main_base_extra} key = [{{ value = "main-key", weight = 1 }}] }}]
        models = [{{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }}]

        [embedding]
        default_model = "bge-m3"
        default_provider = "embedder"
        dimensions = 1024
        normalized = true
        batch_size = 32
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64

        [[embedding.providers]]
        name = "embedder"
        type = "openai_compatible"
        base = [{{ url = "http://localhost:11434/v1", weight = 1, {embedding_base_extra} key = [{{ value = "embed-key", weight = 1 }}] }}]
        models = [{{ model_name = "bge-m3", dimensions = 1024, max_context = 8192 }}]

        [rerank]
        enabled = true
        default_model = "bge-reranker-v2-m3"
        default_provider = "reranker"
        top_n = 10

        [[rerank.providers]]
        name = "reranker"
        type = "openai_compatible"
        base = [{{ url = "https://rerank.example.com/v1", weight = 1, {rerank_base_extra} key = [{{ value = "rerank-key", weight = 1 }}] }}]
        models = [{{ model_name = "bge-reranker-v2-m3", max_context = 8192 }}]
        """,
        encoding="utf-8",
    )
    return config_path


def test_load_config_round_trips_active_window_fields(tmp_path: Path) -> None:
    config = load_config(
        str(
            _write_config(
                tmp_path,
                main_base_extra='active_windows = ["09:00-12:00"], active_timezone = "Asia/Shanghai",',
                embedding_base_extra='active_windows = ["13:00-18:00"], active_timezone = "UTC",',
                rerank_base_extra='active_windows = ["22:00-06:00"], active_timezone = "America/Los_Angeles",',
            )
        )
    )

    assert config.providers[0].base[0].active_windows == ["09:00-12:00"]
    assert config.providers[0].base[0].active_timezone == "Asia/Shanghai"
    assert config.embedding is not None
    assert config.embedding.providers[0].base[0].active_windows == ["13:00-18:00"]
    assert config.embedding.providers[0].base[0].active_timezone == "UTC"
    assert config.rerank is not None
    assert config.rerank.providers[0].base[0].active_windows == ["22:00-06:00"]
    assert config.rerank.providers[0].base[0].active_timezone == "America/Los_Angeles"


def test_load_config_rejects_invalid_active_window_with_path(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, main_base_extra='active_windows = ["13:00"],')

    with pytest.raises(ValueError, match=r"providers\[openai\]\.base\[0\]\.active_windows\[0\]"):
        load_config(str(config_path))


def test_load_config_rejects_invalid_active_timezone(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, embedding_base_extra='active_timezone = "Not/A_Zone",')

    with pytest.raises(ValueError, match=r"embedding\.providers\[embedder\]\.base\[0\]\.active_timezone"):
        load_config(str(config_path))


def test_load_config_defaults_active_window_fields_when_omitted(tmp_path: Path) -> None:
    config = load_config(str(_write_config(tmp_path)))

    assert config.providers[0].base[0].active_windows == []
    assert config.providers[0].base[0].active_timezone is None
