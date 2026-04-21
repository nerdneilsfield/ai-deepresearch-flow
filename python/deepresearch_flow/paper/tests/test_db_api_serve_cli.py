from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from starlette.testclient import TestClient

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.vector_store import compute_group_hash, encode_vector_b64


def _write_semantic_config(
    path: Path,
    *,
    embedding_dimensions: int = 4,
    model_dimensions: int = 4,
    chunk_max_tokens: int = 512,
    advanced_enabled: bool = True,
) -> None:
    path.write_text(
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
dimensions = __EMBED_DIMS__
normalized = true
batch_size = 16
chunk_max_tokens = __CHUNK_MAX_TOKENS__
chunk_overlap_tokens = 64

[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "bge-m3", dimensions = __MODEL_DIMS__, max_context = 8192 } ]

[search]
vector_top_k = 50
keyword_top_k = 30
hybrid = true
advanced_enabled = __ADVANCED_ENABLED__
""",
        encoding="utf-8",
    )
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("__EMBED_DIMS__", str(embedding_dimensions))
        .replace("__MODEL_DIMS__", str(model_dimensions))
        .replace("__CHUNK_MAX_TOKENS__", str(chunk_max_tokens))
        .replace("__ADVANCED_ENABLED__", "true" if advanced_enabled else "false"),
        encoding="utf-8",
    )


def _semantic_body() -> dict:
    chunk = {
        "id": "paper-1__shared_content_0",
        "doc_id": "paper-1",
        "source_path": "summary.md",
        "template_tag": "",
        "chunk_type": "content",
        "chunk_index": 0,
        "field_name": "summary",
        "lang": "",
        "text": "hello",
        "content_hash": "hash-0",
        "vector_b64": encode_vector_b64([0.1, 0.2, 0.3, 0.4]),
        "vector_dim": 4,
        "title": "T",
        "year": 2024,
        "authors": "A",
        "venue": "V",
        "tags": "tag",
    }
    return {
        "index_meta": {
            "model": "bge-m3",
            "dimensions": 4,
            "normalized": True,
            "provider": "ollama",
            "index_version": 1,
        },
        "group": {
            "doc_id": "paper-1",
            "template_tag": "",
            "group_hash": compute_group_hash([chunk["content_hash"]]),
            "part_index": 0,
            "part_count": 1,
            "is_final_part": True,
        },
        "chunks": [chunk],
    }


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


def test_api_serve_mounts_admin_semantic_push_with_cli_embed_db(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    embed_dir = tmp_path / "lance"
    embed_dir.mkdir()
    _write_semantic_config(config)

    with (
        patch("lancedb.connect", return_value=object()),
        patch("uvicorn.run") as mock_run,
        patch("deepresearch_flow.paper.vector_store.validate_index_meta"),
        patch("deepresearch_flow.paper.db.RoutePool.from_embedding_provider", return_value=object()),
    ):
        result = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
                "--embed-db", str(embed_dir),
                "--search-access-token", "search-token",
                "--admin-token", "admin-token",
            ],
        )

    assert result.exit_code == 0

    app = mock_run.call_args.args[0]
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/admin/semantic/chunks/batch",
        content=json.dumps(_semantic_body()),
        headers={
            "Authorization": "Bearer admin-token",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 1


def test_api_serve_admin_semantic_push_accepts_reduced_dimensions(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    embed_dir = tmp_path / "lance"
    embed_dir.mkdir()
    _write_semantic_config(
        config,
        embedding_dimensions=4,
        model_dimensions=8,
        advanced_enabled=True,
    )

    with (
        patch("lancedb.connect", return_value=object()),
        patch("uvicorn.run") as mock_run,
        patch("deepresearch_flow.paper.vector_store.validate_index_meta"),
        patch("deepresearch_flow.paper.db.RoutePool.from_embedding_provider", return_value=object()),
    ):
        result = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
                "--embed-db", str(embed_dir),
                "--search-access-token", "search-token",
                "--admin-token", "admin-token",
            ],
        )

    assert result.exit_code == 0

    app = mock_run.call_args.args[0]
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/admin/semantic/chunks/batch",
        content=json.dumps(_semantic_body()),
        headers={
            "Authorization": "Bearer admin-token",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 1


def test_api_serve_skips_embedding_resolution_when_admin_sync_disabled(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    _write_semantic_config(
        config,
        embedding_dimensions=4,
        model_dimensions=4,
        chunk_max_tokens=99999,
        advanced_enabled=False,
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "[search]\n",
            "[search]\nvector_dir = \"./unused\"\n",
        ),
        encoding="utf-8",
    )

    with patch("uvicorn.run"):
        result = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
            ],
        )

    assert result.exit_code == 0
