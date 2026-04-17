from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


def _base_config() -> str:
    return """
        main_model = [
          { model = "ollama/m", weight = 1 },
        ]

        [extract]
        output = "out.json"
        errors = "err.json"

        [render]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [
          { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "x", weight = 1 }] },
        ]
        models = [
          { model_name = "m" },
        ]

        [embedding]
        default_provider = "ollama"
        default_model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 16
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64

        [[embedding.providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [
          { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] },
        ]
        models = [
          { model_name = "bge-m3", dimensions = 1024, max_context = 8192 },
        ]
    """


def test_advanced_defaults_present_when_search_section_exists(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_dir = "./embed_db"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search is not None
    assert cfg.search.advanced_enabled is False
    assert cfg.search.advanced_rrf_k == 60
    assert cfg.search.advanced_dense_top_k == 50
    assert cfg.search.advanced_sparse_top_k == 30
    assert cfg.search.advanced_post_fusion_top_k == 50
    assert cfg.search.advanced_dedup_cosine_threshold == pytest.approx(0.95)
    assert cfg.search.advanced_rerank_top_n == 20
    assert cfg.search.advanced_mmr_lambda_default == pytest.approx(0.6)
    assert cfg.search.advanced_rerank_timeout_ms == 1500
    assert cfg.search.advanced_top_n_max == 50
    assert cfg.search.advanced_max_query_length == 500


def test_advanced_fields_overridable(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_dir = "./embed_db"
        vector_top_k = 40
        keyword_top_k = 20
        hybrid = true
        advanced_enabled = true
        advanced_rrf_k = 30
        advanced_rerank_timeout_ms = 2500
        advanced_top_n_max = 25
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search is not None
    assert cfg.search.advanced_enabled is True
    assert cfg.search.advanced_rrf_k == 30
    assert cfg.search.advanced_rerank_timeout_ms == 2500
    assert cfg.search.advanced_top_n_max == 25


def test_advanced_config_allows_cli_only_vector_dir(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_top_k = 40
        keyword_top_k = 20
        hybrid = true
        advanced_enabled = true
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search is not None
    assert cfg.search.advanced_enabled is True
    assert cfg.search.vector_dir == ""


def test_existing_search_fields_still_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_ACCESS_TOKEN", "token")
    body = _base_config() + """
        [search]
        vector_dir = "./v"
        vector_top_k = 10
        keyword_top_k = 5
        hybrid = false
        access_token = "env:SEARCH_ACCESS_TOKEN"
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search is not None
    assert cfg.search.vector_dir == "./v"
    assert cfg.search.hybrid is False
    assert cfg.search.access_token == "token"
