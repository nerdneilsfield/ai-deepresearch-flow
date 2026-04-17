from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from deepresearch_flow.cli import cli


def test_api_serve_fails_fast_on_index_mismatch(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        """
main_model = [ { model = "ollama/m", weight = 1 } ]

[extract]
output = "o.json"
errors = "e.json"

[render]

[[providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "m" } ]

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
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "bge-m3", dimensions = 1024, max_context = 8192 } ]

[search]
vector_dir = "./nope"
vector_top_k = 50
keyword_top_k = 30
hybrid = true
advanced_enabled = true
""",
        encoding="utf-8",
    )

    with patch(
        "deepresearch_flow.paper.vector_store.validate_index_meta",
        side_effect=ValueError("dimensions mismatch"),
    ):
        result = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
                "--embed-db", str(tmp_path / "lance"),
                "--search-access-token", "t",
            ],
        )

    assert result.exit_code != 0
    assert "INDEX_MISMATCH" in result.output or "dimensions mismatch" in result.output


def test_api_serve_accepts_cli_embed_db_without_search_vector_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        """
main_model = [ { model = "ollama/m", weight = 1 } ]

[extract]
output = "o.json"
errors = "e.json"

[render]

[[providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "m" } ]

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
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "bge-m3", dimensions = 1024, max_context = 8192 } ]

[search]
vector_top_k = 50
keyword_top_k = 30
hybrid = true
advanced_enabled = true
""",
        encoding="utf-8",
    )

    with (
        patch("lancedb.connect", return_value=object()),
        patch("uvicorn.run"),
        patch("deepresearch_flow.paper.vector_store.validate_index_meta"),
        patch("deepresearch_flow.paper.db.RoutePool.from_embedding_provider", return_value=object()),
        patch("deepresearch_flow.paper.snapshot.api.create_app") as create_app,
    ):
        result = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
                "--embed-db", str(tmp_path / "lance"),
                "--search-access-token", "t",
            ],
        )

    assert result.exit_code == 0
    advanced_ctx = create_app.call_args.kwargs["advanced_config"]
    assert advanced_ctx is not None
    assert advanced_ctx.embed_db_path == tmp_path / "lance"


def test_api_serve_requires_lance_path_for_advanced_search(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        """
main_model = [ { model = "ollama/m", weight = 1 } ]

[extract]
output = "o.json"
errors = "e.json"

[render]

[[providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "m" } ]

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
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "bge-m3", dimensions = 1024, max_context = 8192 } ]

[search]
vector_top_k = 50
keyword_top_k = 30
hybrid = true
advanced_enabled = true
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli,
        [
            "paper", "db", "api", "serve",
            "--snapshot-db", str(db),
            "--config", str(config),
            "--search-access-token", "t",
        ],
    )

    assert result.exit_code != 0
    assert "--embed-db" in result.output or "config.search.vector_dir" in result.output
