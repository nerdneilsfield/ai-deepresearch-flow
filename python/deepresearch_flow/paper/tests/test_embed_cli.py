from __future__ import annotations

from pathlib import Path
from click.testing import CliRunner

from deepresearch_flow.cli import cli


def _write_embed_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [embedding]
        model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 2
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64
        provider = "ollama"

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_embedding = true, is_support_rerank = false }]
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
