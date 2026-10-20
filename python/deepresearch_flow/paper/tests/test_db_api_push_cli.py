from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.paper.snapshot.push import PushStats, RemoteConfig, RemoteSemanticConfig
from deepresearch_flow.paper.snapshot.push_semantic import PushSemanticStats, SemanticPushError
from deepresearch_flow.paper.snapshot.push_static import PushStaticStats
from deepresearch_flow.paper.vector_store import compute_group_fingerprint
from deepresearch_flow.storage.config import StorageConfig


def _write_config(path: Path) -> None:
    path.write_text(
        '[remote]\napi_base_url = "https://api.example.com"\nadmin_token = "token"\n',
        encoding="utf-8",
    )


def _local_chunks(chunks: list[dict]):
    """Serve ``chunks`` as the local embed DB content."""

    def chunks_for_groups(_db: object, groups: list[tuple[str, str]]) -> list[dict]:
        wanted = set(groups)
        return [
            chunk
            for chunk in chunks
            if (chunk["doc_id"], chunk.get("template_tag") or "_shared") in wanted
        ]

    return patch.multiple(
        "deepresearch_flow.paper.vector_store",
        read_chunk_metadata=lambda _db: list(chunks),
        read_chunks_for_groups=chunks_for_groups,
    )


def _remote_manifests(
    papers: dict[str, str] | None = None,
    groups: dict[str, str] | None = None,
    status: int = 200,
    aliases: dict[str, str] | None = None,
    group_entries: list[dict[str, str]] | None = None,
):
    """Fake the remote manifest endpoints at the HTTP layer.

    ``groups`` maps ``"doc_id\ttemplate_key"`` to a group fingerprint, as a shorthand for ``group_entries``.
    """
    if group_entries is None:
        group_entries = [
            {"doc_id": key.split("\t")[0], "template_key": key.split("\t")[1], "fingerprint": value}
            for key, value in (groups or {}).items()
        ]

    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        body = (
            {"papers": papers or {}, "aliases": aliases or {}}
            if url.endswith("/papers/manifest")
            else {"groups": group_entries}
        )
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return patch("httpx.get", fake_get)


@pytest.fixture(autouse=True)
def mock_remote_manifests() -> None:
    with _remote_manifests():
        yield


@pytest.fixture(autouse=True)
def mock_local_paper_keys() -> None:
    with patch("deepresearch_flow.paper.snapshot.push.read_local_paper_keys", return_value={}):
        yield


@pytest.fixture(autouse=True)
def mock_optimize() -> MagicMock:
    with patch("deepresearch_flow.paper.snapshot.push_semantic.optimize_remote_semantic") as mocked:
        yield mocked


class TestApiPushCli:
    def test_only_api_skips_storage(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        _write_config(config_path)
        snapshot_db.write_text("")

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ) as mock_extract,
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, added=1, batches_sent=1),
            ) as mock_push,
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files"
            ) as mock_push_static,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--only-api",
                    "--overwrite",
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_called_once()
        mock_push.assert_called_once()
        assert mock_push.call_args.kwargs["overwrite"] is True
        mock_push_static.assert_not_called()

    def test_only_storage_skips_api(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db") as mock_extract,
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                return_value=PushStaticStats(uploaded=1),
            ) as mock_push_static,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--only-storage",
                    "--overwrite",
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_not_called()
        mock_push.assert_not_called()
        mock_push_static.assert_called_once()
        assert mock_push_static.call_args.kwargs["overwrite"] is True

    def test_overwrite_is_forwarded_to_api_and_static(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, updated=1, batches_sent=1),
            ) as mock_push,
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=["summary/paper-1/deep_read.json"],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                return_value=PushStaticStats(overwritten=1),
            ) as mock_push_static,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--overwrite",
                ],
            )

        assert result.exit_code == 0
        assert mock_push.call_args.kwargs["overwrite"] is True
        assert mock_push_static.call_args.kwargs["overwrite"] is True

    def test_overwrite_is_forwarded_for_static_retry(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        retry_report = tmp_path / "push-static-errors.json"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()
        retry_report.write_text(
            json.dumps(
                [{"path": "summary/paper-1/deep_read.json", "error": "timeout"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=["summary/paper-1/deep_read.json"],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                return_value=PushStaticStats(overwritten=1),
            ) as mock_push_static,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--retry-failed",
                    str(retry_report),
                    "--overwrite",
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_not_called()
        assert mock_push_static.call_args.kwargs["only_files"] == ["summary/paper-1/deep_read.json"]
        assert mock_push_static.call_args.kwargs["overwrite"] is True

    def test_only_storage_shows_tqdm_progress(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False
        progress = MagicMock()

        def _fake_push_static(*args, **kwargs):
            callback = kwargs["on_file_result"]
            callback("images/a.png", "uploaded", "")
            callback("images/b.png", "skipped", "")
            callback("images/c.png", "failed", "boom")
            return PushStaticStats(uploaded=1, skipped=1, failed=1)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db") as mock_extract,
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=["images/a.png", "images/b.png", "images/c.png"],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                side_effect=_fake_push_static,
            ) as mock_push_static,
            patch("deepresearch_flow.paper.db.tqdm", return_value=progress) as mock_tqdm,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--only-storage",
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_not_called()
        mock_push.assert_not_called()
        mock_push_static.assert_called_once()
        mock_tqdm.assert_called_once()
        assert mock_tqdm.call_args.kwargs["total"] == 3
        assert progress.update.call_count == 3
        assert progress.set_postfix.call_count == 3

    def test_storage_concurrency_is_forwarded(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=["images/a.png"],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                return_value=PushStaticStats(uploaded=1),
            ) as mock_push_static,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--only-storage",
                    "--storage-concurrency",
                    "6",
                ],
            )

        assert result.exit_code == 0
        assert mock_push_static.call_args.kwargs["concurrency"] == 6

    def test_only_api_and_only_storage_are_mutually_exclusive(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        _write_config(config_path)
        snapshot_db.write_text("")

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push",
                "--snapshot-db",
                str(snapshot_db),
                "--config",
                str(config_path),
                "--only-api",
                "--only-storage",
            ],
        )

        assert result.exit_code != 0
        assert "--only-api and --only-storage are mutually exclusive" in result.output

    def test_only_storage_rejects_dry_run(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push",
                "--snapshot-db",
                str(snapshot_db),
                "--static-export-dir",
                str(static_dir),
                "--config",
                str(config_path),
                "--only-storage",
                "--dry-run",
            ],
        )

        assert result.exit_code != 0
        assert "--dry-run cannot be used with --only-storage" in result.output

    def test_only_api_rejects_retry_failed(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        retry_report = tmp_path / "push-static-errors.json"
        _write_config(config_path)
        snapshot_db.write_text("")
        retry_report.write_text("[]", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push",
                "--snapshot-db",
                str(snapshot_db),
                "--config",
                str(config_path),
                "--only-api",
                "--retry-failed",
                str(retry_report),
            ],
        )

        assert result.exit_code != 0
        assert "--retry-failed cannot be used with --only-api" in result.output

    def test_only_storage_rejects_start_end_idx(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push",
                "--snapshot-db",
                str(snapshot_db),
                "--static-export-dir",
                str(static_dir),
                "--config",
                str(config_path),
                "--only-storage",
                "--start-idx",
                "1",
            ],
        )

        assert result.exit_code != 0
        assert "--start-idx/--end-idx cannot be used with --only-storage" in result.output

    def test_retry_failed_semantic_report_retries_semantic_requests(
        self, tmp_path: Path, mock_optimize: MagicMock
    ) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        embed_dir = tmp_path / "embed_vectors"
        retry_report = tmp_path / "push-semantic-errors.json"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()
        embed_dir.mkdir()
        retry_request = {
            "group": {
                "doc_id": "paper-2",
                "template_tag": "",
                "group_hash": "group-2",
                "part_index": 0,
                "part_count": 1,
                "is_final_part": True,
            },
            "chunks": [{"id": "chunk-2", "vector_b64": "abc", "vector_dim": 4}],
        }
        retry_report.write_text(
            json.dumps(
                [
                    {
                        "doc_id": "paper-2",
                        "template_tag": "",
                        "chunk_count": 1,
                        "request": retry_request,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db") as mock_extract,
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files"
            ) as mock_push_static,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(
                    batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0
                ),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                    "--retry-failed",
                    str(retry_report),
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_not_called()
        mock_push.assert_not_called()
        mock_push_static.assert_not_called()
        mock_push_semantic.assert_called_once()
        assert mock_push_semantic.call_args.args[0] == []
        assert mock_push_semantic.call_args.kwargs["requests"] == [retry_request]
        mock_optimize.assert_called_once_with(config)

    def test_embed_db_rejects_only_storage(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()
        embed_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push",
                "--snapshot-db",
                str(snapshot_db),
                "--static-export-dir",
                str(static_dir),
                "--config",
                str(config_path),
                "--only-storage",
                "--embed-db",
                str(embed_dir),
            ],
        )

        assert result.exit_code != 0
        assert "--embed-db cannot be combined with --only-storage" in result.output

    def test_embed_db_is_skipped_in_dry_run(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks"
            ) as mock_push_semantic,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--dry-run",
                    "--embed-db",
                    str(embed_dir),
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_not_called()
        mock_push_semantic.assert_not_called()

    def test_only_api_with_embed_db_skips_semantic_chunks(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, added=1, batches_sent=1),
            ) as mock_push,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(
                [
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    }
                ]
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=MagicMock(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0),
            ) as mock_push_semantic,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                    "--only-api",
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_called_once()
        mock_push_semantic.assert_not_called()

    def test_embed_db_pushes_semantic_chunks(
        self, tmp_path: Path, mock_optimize: MagicMock
    ) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, added=1, batches_sent=1),
            ) as mock_push,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(
                [
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    }
                ]
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=MagicMock(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0),
            ) as mock_push_semantic,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_called_once()
        mock_push_semantic.assert_called_once()
        mock_optimize.assert_called_once_with(config)

    def test_embed_db_runs_after_static_push(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        static_dir = tmp_path / "static"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        static_dir.mkdir()
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            storage=StorageConfig(
                type="webdav",
                url="https://cdn.example.com/static",
                username="deploy",
                password="secret",
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False
        calls: list[str] = []

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                side_effect=lambda *a, **k: (
                    calls.append("api") or PushStats(total=1, added=1, batches_sent=1)
                ),
            ),
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=["images/a.png"],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                side_effect=lambda *a, **k: calls.append("static") or PushStaticStats(uploaded=1),
            ),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(
                [
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    }
                ]
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                side_effect=lambda *a, **k: (
                    calls.append("semantic")
                    or MagicMock(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0)
                ),
            ),
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                ],
            )

        assert result.exit_code == 0
        assert calls == ["api", "static", "semantic"]

    def test_embed_db_shows_semantic_tqdm_progress(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            batch_size=10,
            semantic=RemoteSemanticConfig(
                max_rows=2,
                max_payload_bytes=1024,
                timeout=30.0,
                retries=1,
                retry_backoff_seconds=0.0,
            ),
        )
        progress = MagicMock()

        def _fake_push_semantic(*args, **kwargs):
            kwargs["on_batch"](0, 2, {"inserted": 2, "updated": 0, "skipped": 0, "deleted": 0})
            kwargs["on_batch"](1, 1, {"inserted": 1, "updated": 0, "skipped": 0, "deleted": 0})
            return PushSemanticStats(batches_sent=2, inserted=3, updated=0, skipped=0, deleted=0)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, added=1, batches_sent=1),
            ),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(
                [
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h1",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    },
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h2",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    },
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h3",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    },
                ]
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.group_chunks_for_push",
                return_value=[
                    {
                        "group": {
                            "doc_id": "paper-1",
                            "template_tag": "",
                            "group_hash": "g",
                            "part_index": 0,
                            "part_count": 2,
                            "is_final_part": False,
                        },
                        "chunks": [{}, {}],
                    },
                    {
                        "group": {
                            "doc_id": "paper-1",
                            "template_tag": "",
                            "group_hash": "g",
                            "part_index": 1,
                            "part_count": 2,
                            "is_final_part": True,
                        },
                        "chunks": [{}],
                    },
                ],
            ) as mock_group,
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                side_effect=_fake_push_semantic,
            ),
            patch("deepresearch_flow.paper.db.tqdm", return_value=progress) as mock_tqdm,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                ],
            )

        assert result.exit_code == 0
        mock_group.assert_called_once_with(
            [
                {
                    "doc_id": "paper-1",
                    "template_tag": "",
                    "content_hash": "h1",
                    "vector": [0.1, 0.2, 0.3, 0.4],
                },
                {
                    "doc_id": "paper-1",
                    "template_tag": "",
                    "content_hash": "h2",
                    "vector": [0.1, 0.2, 0.3, 0.4],
                },
                {
                    "doc_id": "paper-1",
                    "template_tag": "",
                    "content_hash": "h3",
                    "vector": [0.1, 0.2, 0.3, 0.4],
                },
            ],
            max_rows=2,
            max_payload_bytes=1024,
        )
        mock_tqdm.assert_called_once()
        assert mock_tqdm.call_args.kwargs["total"] == 3
        assert mock_tqdm.call_args.kwargs["desc"] == "Semantic push"
        assert progress.update.call_count == 2
        assert progress.set_postfix.call_count >= 2

    def test_embed_db_failure_writes_semantic_error_report(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )
        failure = {
            "batch_index": 0,
            "total_batches": 1,
            "doc_id": "paper-1",
            "template_tag": "",
            "part_index": 0,
            "part_count": 1,
            "chunk_count": 1,
            "payload_bytes": 123,
            "attempts": 2,
            "error": "Server disconnected",
            "request": {
                "index_meta": {"dimensions": 4},
                "group": {"doc_id": "paper-1"},
                "chunks": [{"id": "chunk-1"}],
            },
        }
        error = SemanticPushError(failure, PushSemanticStats(errors=[failure]))

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=1, added=1, batches_sent=1),
            ),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(
                [
                    {
                        "doc_id": "paper-1",
                        "template_tag": "",
                        "content_hash": "h1",
                        "vector": [0.1, 0.2, 0.3, 0.4],
                    }
                ]
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.group_chunks_for_push",
                return_value=[
                    {
                        "group": {
                            "doc_id": "paper-1",
                            "template_tag": "",
                            "group_hash": "g",
                            "part_index": 0,
                            "part_count": 1,
                            "is_final_part": True,
                        },
                        "chunks": [{}],
                    },
                ],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                side_effect=error,
            ),
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                ],
            )

        assert result.exit_code != 0
        report_path = tmp_path / "push-semantic-errors.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report[0]["doc_id"] == "paper-1"


class TestApiPushSemanticCli:
    def test_push_semantic_expands_chunk_window_to_full_groups(
        self, tmp_path: Path, mock_optimize: MagicMock
    ) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )
        chunks = [
            {
                "id": "paper-1___title_0",
                "doc_id": "paper-1",
                "template_tag": "",
                "chunk_type": "title",
                "chunk_index": 0,
                "content_hash": "h1",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "paper-1___content_0",
                "doc_id": "paper-1",
                "template_tag": "",
                "chunk_type": "content",
                "chunk_index": 0,
                "content_hash": "h2",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "paper-2___title_0",
                "doc_id": "paper-2",
                "template_tag": "",
                "chunk_type": "title",
                "chunk_index": 0,
                "content_hash": "h3",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "paper-2___content_0",
                "doc_id": "paper-2",
                "template_tag": "",
                "chunk_type": "content",
                "chunk_index": 0,
                "content_hash": "h4",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
        ]

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(chunks),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(
                    batches_sent=1, inserted=2, updated=0, skipped=0, deleted=0
                ),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                    "--start-chunk-idx",
                    "2",
                    "--end-chunk-idx",
                    "3",
                ],
            )

        assert result.exit_code == 0
        pushed_chunks = mock_push_semantic.call_args.args[0]
        assert [chunk["doc_id"] for chunk in pushed_chunks] == ["paper-2", "paper-2"]
        assert len(pushed_chunks) == 2
        mock_optimize.assert_called_once_with(config)

    def test_push_semantic_retries_semantic_error_report(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        retry_report = tmp_path / "push-semantic-errors.json"
        _write_config(config_path)
        embed_dir.mkdir()

        retry_request = {
            "group": {
                "doc_id": "paper-2",
                "template_tag": "",
                "group_hash": "group-2",
                "part_index": 0,
                "part_count": 1,
                "is_final_part": True,
            },
            "chunks": [{"id": "chunk-2", "vector_b64": "abc", "vector_dim": 4}],
        }
        retry_report.write_text(
            json.dumps(
                [
                    {
                        "doc_id": "paper-2",
                        "template_tag": "",
                        "chunk_count": 1,
                        "request": retry_request,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(
                    batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0
                ),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                    "--retry-failed",
                    str(retry_report),
                ],
            )

        assert result.exit_code == 0
        assert mock_push_semantic.call_args.args[0] == []
        assert mock_push_semantic.call_args.kwargs["requests"] == [retry_request]

    def test_push_semantic_empty_retry_report_is_noop_before_embed_validation(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "remote.toml"
        retry_report = tmp_path / "push-semantic-errors.json"
        _write_config(config_path)
        retry_report.write_text("[]", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push-semantic",
                "--embed-db",
                str(tmp_path / "missing-embed-db"),
                "--config",
                str(config_path),
                "--retry-failed",
                str(retry_report),
            ],
        )

        assert result.exit_code == 0
        assert "Retry report is empty; nothing to push." in result.output

    def test_push_semantic_rejects_static_retry_report(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        retry_report = tmp_path / "push-static-errors.json"
        _write_config(config_path)
        embed_dir.mkdir()
        retry_report.write_text(
            json.dumps(
                [{"path": "summary/paper-1/default.json", "error": "boom"}], ensure_ascii=False
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push-semantic",
                "--embed-db",
                str(embed_dir),
                "--config",
                str(config_path),
                "--retry-failed",
                str(retry_report),
            ],
        )

        assert result.exit_code != 0
        assert "push-semantic only accepts semantic retry reports" in result.output

    def test_push_semantic_rejects_range_with_retry_report(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        retry_report = tmp_path / "push-semantic-errors.json"
        _write_config(config_path)
        embed_dir.mkdir()
        retry_report.write_text(
            json.dumps(
                [{"request": {"group": {"doc_id": "paper-1"}, "chunks": []}}], ensure_ascii=False
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            paper,
            [
                "db",
                "api",
                "push-semantic",
                "--embed-db",
                str(embed_dir),
                "--config",
                str(config_path),
                "--retry-failed",
                str(retry_report),
                "--start-chunk-idx",
                "1",
            ],
        )

        assert result.exit_code != 0
        assert (
            "--retry-failed cannot be combined with --start-chunk-idx/--end-chunk-idx"
            in result.output
        )

    def test_push_semantic_skips_when_selected_window_is_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks([]),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks"
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                    "--start-chunk-idx",
                    "10",
                    "--end-chunk-idx",
                    "20",
                ],
            )

        assert result.exit_code == 0
        assert "Nothing to push" in result.output
        mock_push_semantic.assert_not_called()

    def test_start_end_idx_filters_api_and_semantic_docs(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(
            api_base_url="https://api.example.com", admin_token="token", batch_size=10
        )
        papers = [
            {"paper_id": "paper-0", "paper_title": "Paper 0"},
            {"paper_id": "paper-1", "paper_title": "Paper 1"},
            {"paper_id": "paper-2", "paper_title": "Paper 2"},
            {"paper_id": "paper-3", "paper_title": "Paper 3"},
        ]
        chunks = [
            {
                "doc_id": "paper-0",
                "template_tag": "",
                "content_hash": "h0",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "doc_id": "paper-1",
                "template_tag": "",
                "content_hash": "h1",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "doc_id": "paper-2",
                "template_tag": "",
                "content_hash": "h2",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "doc_id": "paper-3",
                "template_tag": "",
                "content_hash": "h3",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
        ]

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=papers
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push.push_papers",
                return_value=PushStats(total=2, added=2, batches_sent=1),
            ) as mock_push,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={
                    "model": "m",
                    "dimensions": 4,
                    "normalized": True,
                    "provider": "p",
                    "index_version": 1,
                },
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(chunks),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(
                    batches_sent=1, inserted=2, updated=0, skipped=0, deleted=0
                ),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--config",
                    str(config_path),
                    "--embed-db",
                    str(embed_dir),
                    "--start-idx",
                    "1",
                    "--end-idx",
                    "3",
                ],
            )

        assert result.exit_code == 0
        assert [paper["paper_id"] for paper in mock_push.call_args.args[0]] == [
            "paper-1",
            "paper-2",
        ]
        assert [chunk["doc_id"] for chunk in mock_push_semantic.call_args.args[0]] == [
            "paper-1",
            "paper-2",
        ]


def _push_papers_args(args: list[str], tmp_path: Path, remote: object, papers: list[dict]):
    config_path = tmp_path / "remote.toml"
    snapshot_db = tmp_path / "paper_snapshot.db"
    _write_config(config_path)
    snapshot_db.write_text("")
    config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token")
    with (
        patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
        patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=papers),
        patch(
            "deepresearch_flow.paper.snapshot.push.push_papers",
            return_value=PushStats(total=1, added=1, batches_sent=1),
        ) as mock_push,
        remote,
    ):
        result = CliRunner().invoke(
            paper,
            ["db", "api", "push", "--snapshot-db", str(snapshot_db), "--config", str(config_path)]
            + args,
        )
    return result, mock_push


_THREE_PAPERS = [
    {"paper_id": "paper-0", "paper_title": "P0", "source_hash": "a"},
    {"paper_id": "paper-1", "paper_title": "P1", "source_hash": "b"},
    {"paper_id": "paper-2", "paper_title": "P2", "source_hash": "c"},
]


class TestIncrementalPush:
    def test_only_missing_papers_are_posted(self, tmp_path: Path) -> None:
        result, mock_push = _push_papers_args(
            [],
            tmp_path,
            _remote_manifests(papers={"paper-1": "stale", "other": "x"}),
            _THREE_PAPERS,
        )

        assert result.exit_code == 0
        assert [p["paper_id"] for p in mock_push.call_args.args[0]] == ["paper-0", "paper-2"]
        assert "Remote has 2, pushing 2 (skipped 1 already present)" in result.output

    def test_overwrite_also_posts_papers_with_different_source_hash(self, tmp_path: Path) -> None:
        result, mock_push = _push_papers_args(
            ["--overwrite"],
            tmp_path,
            _remote_manifests(papers={"paper-0": "a", "paper-1": "stale"}),
            _THREE_PAPERS,
        )

        assert result.exit_code == 0
        assert [p["paper_id"] for p in mock_push.call_args.args[0]] == ["paper-1", "paper-2"]

    def test_paper_known_to_remote_under_another_id_by_alias_is_not_posted(
        self, tmp_path: Path
    ) -> None:
        remote = _remote_manifests(
            papers={"remote-id": "a"}, aliases={"doi:10.1/zero": "remote-id"}
        )
        with patch(
            "deepresearch_flow.paper.snapshot.push.read_local_paper_keys",
            return_value={"paper-0": ["doi:10.1/zero"]},
        ):
            result, mock_push = _push_papers_args([], tmp_path, remote, _THREE_PAPERS)

        assert result.exit_code == 0
        assert [p["paper_id"] for p in mock_push.call_args.args[0]] == ["paper-1", "paper-2"]

    def test_overwrite_compares_source_hash_of_the_alias_resolved_remote_id(
        self, tmp_path: Path
    ) -> None:
        remote = _remote_manifests(
            papers={"remote-0": "a", "remote-1": "stale"},
            aliases={"k0": "remote-0", "k1": "remote-1"},
        )
        with patch(
            "deepresearch_flow.paper.snapshot.push.read_local_paper_keys",
            return_value={"paper-0": ["k0"], "paper-1": ["k1"], "paper-2": ["k2"]},
        ):
            result, mock_push = _push_papers_args(["--overwrite"], tmp_path, remote, _THREE_PAPERS)

        assert result.exit_code == 0
        assert [p["paper_id"] for p in mock_push.call_args.args[0]] == ["paper-1", "paper-2"]

    def test_aliases_hitting_different_remote_ids_warn_and_are_not_pushed(
        self, tmp_path: Path
    ) -> None:
        remote = _remote_manifests(
            papers={"remote-a": "x", "remote-b": "y"},
            aliases={"k1": "remote-a", "k2": "remote-b"},
        )
        with patch(
            "deepresearch_flow.paper.snapshot.push.read_local_paper_keys",
            return_value={"paper-0": ["k1", "k2"]},
        ):
            result, mock_push = _push_papers_args([], tmp_path, remote, _THREE_PAPERS[:1])

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "paper-0" in result.output
        assert "remote-a" in result.output and "remote-b" in result.output
        mock_push.assert_not_called()

    def test_overwrite_pushes_when_any_alias_matched_remote_id_has_different_hash(
        self, tmp_path: Path
    ) -> None:
        remote = _remote_manifests(
            papers={"remote-a": "a", "remote-b": "stale"},
            aliases={"k1": "remote-a", "k2": "remote-b"},
        )
        with patch(
            "deepresearch_flow.paper.snapshot.push.read_local_paper_keys",
            return_value={"paper-0": ["k1", "k2"]},
        ):
            result, mock_push = _push_papers_args(
                ["--overwrite"], tmp_path, remote, _THREE_PAPERS[:1]
            )

        assert result.exit_code == 0
        assert [p["paper_id"] for p in mock_push.call_args.args[0]] == ["paper-0"]

    def test_nothing_to_push_when_remote_has_everything(self, tmp_path: Path) -> None:
        remote = _remote_manifests(papers={"paper-0": "a", "paper-1": "b", "paper-2": "c"})
        result, mock_push = _push_papers_args([], tmp_path, remote, _THREE_PAPERS)

        assert result.exit_code == 0
        mock_push.assert_not_called()
        assert "Nothing to push" in result.output

    def test_dry_run_lists_only_missing_papers(self, tmp_path: Path) -> None:
        result, mock_push = _push_papers_args(
            ["--dry-run"], tmp_path, _remote_manifests(papers={"paper-0": "a"}), _THREE_PAPERS
        )

        assert result.exit_code == 0
        mock_push.assert_not_called()
        assert "Remote has 1, pushing 2" in result.output

    def test_nothing_to_push_still_runs_storage_phase(self, tmp_path: Path) -> None:
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        config = RemoteConfig(
            api_base_url="https://api.example.com",
            admin_token="token",
            storage=StorageConfig(
                type="webdav", url="https://cdn.example.com", username="u", password="p"
            ),
        )
        fake_storage = MagicMock()
        fake_storage.__enter__.return_value = fake_storage
        fake_storage.__exit__.return_value = False
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        _write_config(config_path)
        snapshot_db.write_text("")

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.snapshot.push.extract_papers_from_db",
                return_value=_THREE_PAPERS,
            ),
            _remote_manifests(papers={"paper-0": "a", "paper-1": "b", "paper-2": "c"}),
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.discover_static_files",
                return_value=[],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_static.push_static_files",
                return_value=PushStaticStats(uploaded=1),
            ) as mock_push_static,
        ):
            result = CliRunner().invoke(
                paper,
                [
                    "db",
                    "api",
                    "push",
                    "--snapshot-db",
                    str(snapshot_db),
                    "--static-export-dir",
                    str(static_dir),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code == 0
        mock_push_static.assert_called_once()

    def test_old_server_without_manifest_fails_with_upgrade_hint(self, tmp_path: Path) -> None:
        result, mock_push = _push_papers_args(
            [], tmp_path, _remote_manifests(status=404), _THREE_PAPERS
        )

        assert result.exit_code != 0
        mock_push.assert_not_called()
        assert "upgrade the server" in result.output

    def test_semantic_pushes_only_missing_or_changed_groups(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()
        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token")
        vector = [0.1, 0.2, 0.3, 0.4]
        chunks = [
            {
                "id": "s",
                "doc_id": "same",
                "template_tag": "",
                "content_hash": "h1",
                "vector": vector,
            },
            {
                "id": "c",
                "doc_id": "changed",
                "template_tag": "simple",
                "content_hash": "h2",
                "vector": vector,
            },
            {
                "id": "n",
                "doc_id": "new",
                "template_tag": "",
                "content_hash": "h3",
                "vector": vector,
            },
        ]
        groups = {
            "same\t_shared": compute_group_fingerprint([("s", "h1")]),
            "changed\tsimple": "old-hash",
            "remote-only\t_shared": "zzz",
        }

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(chunks),
            _remote_manifests(groups=groups),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(batches_sent=1, inserted=2),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            result = CliRunner().invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code == 0
        assert sorted(c["doc_id"] for c in mock_push_semantic.call_args.args[0]) == [
            "changed",
            "new",
        ]
        assert "Semantic: remote has 3 groups, pushing 2" in result.output

    def _push_semantic(
        self, tmp_path: Path, chunks: list[dict], remote: object, extra_args: list[str]
    ) -> tuple[object, MagicMock]:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir(exist_ok=True)
        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token")
        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(chunks),
            remote,
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(batches_sent=1, inserted=1),
            ) as mock_push_semantic,
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            result = CliRunner().invoke(
                paper,
                ["db", "api", "push-semantic", "--embed-db", str(embed_dir)]
                + ["--config", str(config_path)]
                + extra_args,
            )
        return result, mock_push_semantic

    def test_semantic_chunk_window_selects_groups_before_remote_filtering(
        self, tmp_path: Path
    ) -> None:
        vector = [0.1, 0.2, 0.3, 0.4]
        chunks = [
            {"id": "a0", "doc_id": "a", "template_tag": "", "content_hash": "ha", "vector": vector},
            {"id": "b0", "doc_id": "b", "template_tag": "", "content_hash": "hb", "vector": vector},
        ]
        remote = _remote_manifests(groups={"a\t_shared": compute_group_fingerprint([("a0", "ha")])})

        result, mock_push = self._push_semantic(
            tmp_path, chunks, remote, ["--start-chunk-idx", "1", "--end-chunk-idx", "2"]
        )
        assert result.exit_code == 0
        assert [c["doc_id"] for c in mock_push.call_args.args[0]] == ["b"]

        result, mock_push = self._push_semantic(
            tmp_path, chunks, remote, ["--start-chunk-idx", "0", "--end-chunk-idx", "1"]
        )
        assert result.exit_code == 0
        mock_push.assert_not_called()
        assert "Nothing to push" in result.output

    def test_semantic_group_with_swapped_chunk_ids_but_same_text_is_pushed(
        self, tmp_path: Path
    ) -> None:
        vector = [0.1, 0.2, 0.3, 0.4]
        remote_chunks = [("c0", "h0"), ("c1", "h1")]
        local = [
            {"id": "c1", "doc_id": "a", "template_tag": "", "content_hash": "h0", "vector": vector},
            {"id": "c0", "doc_id": "a", "template_tag": "", "content_hash": "h1", "vector": vector},
        ]
        remote = _remote_manifests(groups={"a\t_shared": compute_group_fingerprint(remote_chunks)})

        result, mock_push = self._push_semantic(tmp_path, local, remote, [])

        assert result.exit_code == 0
        assert sorted(c["id"] for c in mock_push.call_args.args[0]) == ["c0", "c1"]

    def test_semantic_groups_whose_ids_contain_tabs_stay_distinct(self, tmp_path: Path) -> None:
        vector = [0.1, 0.2, 0.3, 0.4]
        chunks = [
            {
                "id": "x",
                "doc_id": "a\tb",
                "template_tag": "c",
                "content_hash": "h",
                "vector": vector,
            },
            {
                "id": "y",
                "doc_id": "a",
                "template_tag": "b\tc",
                "content_hash": "h",
                "vector": vector,
            },
        ]
        entries = [
            {
                "doc_id": "a\tb",
                "template_key": "c",
                "fingerprint": compute_group_fingerprint([("x", "h")]),
            },
            {"doc_id": "a", "template_key": "b\tc", "fingerprint": "old"},
        ]

        result, mock_push = self._push_semantic(
            tmp_path, chunks, _remote_manifests(group_entries=entries), []
        )

        assert result.exit_code == 0
        assert [c["id"] for c in mock_push.call_args.args[0]] == ["y"]
        assert "remote has 2 groups" in result.output

    def test_semantic_with_nothing_pending_skips_push_and_optimize(
        self, tmp_path: Path, mock_optimize: MagicMock
    ) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()
        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token")
        chunks = [
            {
                "id": "s",
                "doc_id": "same",
                "template_tag": "",
                "content_hash": "h1",
                "vector": [0.1] * 4,
            }
        ]

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks(chunks),
            _remote_manifests(groups={"same\t_shared": compute_group_fingerprint([("s", "h1")])}),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks"
            ) as mock_push_semantic,
        ):
            result = CliRunner().invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code == 0
        mock_push_semantic.assert_not_called()
        mock_optimize.assert_not_called()
        assert "Nothing to push" in result.output

    def test_semantic_old_server_without_manifest_fails_with_upgrade_hint(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()
        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token")

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.vector_store.load_index_meta", return_value={}),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            _local_chunks([]),
            _remote_manifests(status=404),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks"
            ) as mock_push_semantic,
        ):
            result = CliRunner().invoke(
                paper,
                [
                    "db",
                    "api",
                    "push-semantic",
                    "--embed-db",
                    str(embed_dir),
                    "--config",
                    str(config_path),
                ],
            )

        assert result.exit_code != 0
        mock_push_semantic.assert_not_called()
        assert "upgrade the server" in result.output
