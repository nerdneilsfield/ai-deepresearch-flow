from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.paper.snapshot.push import PushStats, RemoteConfig, RemoteSemanticConfig
from deepresearch_flow.paper.snapshot.push_semantic import PushSemanticStats, SemanticPushError
from deepresearch_flow.paper.snapshot.push_static import PushStaticStats
from deepresearch_flow.storage.config import StorageConfig


def _write_config(path: Path) -> None:
    path.write_text(
        '[remote]\n'
        'api_base_url = "https://api.example.com"\n'
        'admin_token = "token"\n',
        encoding="utf-8",
    )


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
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]) as mock_extract,
            patch("deepresearch_flow.paper.snapshot.push.push_papers", return_value=PushStats(total=1, added=1, batches_sent=1)) as mock_push,
            patch("deepresearch_flow.paper.snapshot.push_static.push_static_files") as mock_push_static,
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
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_called_once()
        mock_push.assert_called_once()
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
                ],
            )

        assert result.exit_code == 0
        mock_extract.assert_not_called()
        mock_push.assert_not_called()
        mock_push_static.assert_called_once()

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

    def test_retry_failed_semantic_report_retries_semantic_requests(self, tmp_path: Path) -> None:
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

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db") as mock_extract,
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch("deepresearch_flow.paper.snapshot.push_static.push_static_files") as mock_push_static,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1},
            ),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", return_value=PushSemanticStats(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0)) as mock_push_semantic,
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
                "db", "api", "push",
                "--snapshot-db", str(snapshot_db),
                "--static-export-dir", str(static_dir),
                "--config", str(config_path),
                "--only-storage",
                "--embed-db", str(embed_dir),
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

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]),
            patch("deepresearch_flow.paper.snapshot.push.push_papers") as mock_push,
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks") as mock_push_semantic,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db", "api", "push",
                    "--snapshot-db", str(snapshot_db),
                    "--config", str(config_path),
                    "--dry-run",
                    "--embed-db", str(embed_dir),
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_not_called()
        mock_push_semantic.assert_not_called()

    def test_embed_db_pushes_semantic_chunks(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]),
            patch("deepresearch_flow.paper.snapshot.push.push_papers", return_value=PushStats(total=1, added=1, batches_sent=1)) as mock_push,
            patch("deepresearch_flow.paper.vector_store.load_index_meta", return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=[{"doc_id": "paper-1", "template_tag": "", "content_hash": "h", "vector": [0.1, 0.2, 0.3, 0.4]}]),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", return_value=MagicMock(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0)) as mock_push_semantic,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db", "api", "push",
                    "--snapshot-db", str(snapshot_db),
                    "--config", str(config_path),
                    "--embed-db", str(embed_dir),
                    "--only-api",
                ],
            )

        assert result.exit_code == 0
        mock_push.assert_called_once()
        mock_push_semantic.assert_called_once()


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
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]),
            patch("deepresearch_flow.paper.snapshot.push.push_papers", side_effect=lambda *a, **k: calls.append("api") or PushStats(total=1, added=1, batches_sent=1)),
            patch("deepresearch_flow.storage.factory.create_storage", return_value=fake_storage),
            patch("deepresearch_flow.paper.snapshot.push_static.discover_static_files", return_value=["images/a.png"]),
            patch("deepresearch_flow.paper.snapshot.push_static.push_static_files", side_effect=lambda *a, **k: calls.append("static") or PushStaticStats(uploaded=1)),
            patch("deepresearch_flow.paper.vector_store.load_index_meta", return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=[{"doc_id": "paper-1", "template_tag": "", "content_hash": "h", "vector": [0.1, 0.2, 0.3, 0.4]}]),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", side_effect=lambda *a, **k: calls.append("semantic") or MagicMock(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0)),
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db", "api", "push",
                    "--snapshot-db", str(snapshot_db),
                    "--static-export-dir", str(static_dir),
                    "--config", str(config_path),
                    "--embed-db", str(embed_dir),
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
            semantic=RemoteSemanticConfig(max_rows=2, max_payload_bytes=1024, timeout=30.0, retries=1, retry_backoff_seconds=0.0),
        )
        progress = MagicMock()

        def _fake_push_semantic(*args, **kwargs):
            kwargs["on_batch"](0, 2, {"inserted": 2, "updated": 0, "skipped": 0, "deleted": 0})
            kwargs["on_batch"](1, 1, {"inserted": 1, "updated": 0, "skipped": 0, "deleted": 0})
            return PushSemanticStats(batches_sent=2, inserted=3, updated=0, skipped=0, deleted=0)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]),
            patch("deepresearch_flow.paper.snapshot.push.push_papers", return_value=PushStats(total=1, added=1, batches_sent=1)),
            patch("deepresearch_flow.paper.vector_store.load_index_meta", return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch(
                "deepresearch_flow.paper.vector_store.read_all_chunks",
                return_value=[
                    {"doc_id": "paper-1", "template_tag": "", "content_hash": "h1", "vector": [0.1, 0.2, 0.3, 0.4]},
                    {"doc_id": "paper-1", "template_tag": "", "content_hash": "h2", "vector": [0.1, 0.2, 0.3, 0.4]},
                    {"doc_id": "paper-1", "template_tag": "", "content_hash": "h3", "vector": [0.1, 0.2, 0.3, 0.4]},
                ],
            ),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.group_chunks_for_push",
                return_value=[
                    {"group": {"doc_id": "paper-1", "template_tag": "", "group_hash": "g", "part_index": 0, "part_count": 2, "is_final_part": False}, "chunks": [{}, {}]},
                    {"group": {"doc_id": "paper-1", "template_tag": "", "group_hash": "g", "part_index": 1, "part_count": 2, "is_final_part": True}, "chunks": [{}]},
                ],
            ) as mock_group,
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", side_effect=_fake_push_semantic),
            patch("deepresearch_flow.paper.db.tqdm", return_value=progress) as mock_tqdm,
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db", "api", "push",
                    "--snapshot-db", str(snapshot_db),
                    "--config", str(config_path),
                    "--embed-db", str(embed_dir),
                    "--only-api",
                ],
            )

        assert result.exit_code == 0
        mock_group.assert_called_once_with(
            [
                {"doc_id": "paper-1", "template_tag": "", "content_hash": "h1", "vector": [0.1, 0.2, 0.3, 0.4]},
                {"doc_id": "paper-1", "template_tag": "", "content_hash": "h2", "vector": [0.1, 0.2, 0.3, 0.4]},
                {"doc_id": "paper-1", "template_tag": "", "content_hash": "h3", "vector": [0.1, 0.2, 0.3, 0.4]},
            ],
            max_rows=2,
            max_payload_bytes=1024,
        )
        mock_tqdm.assert_called_once()
        assert mock_tqdm.call_args.kwargs["total"] == 3
        assert mock_tqdm.call_args.kwargs["desc"] == "Semantic push"
        assert progress.update.call_count == 2
        assert progress.set_postfix.call_count >= 2

    def test_embed_db_failure_writes_semantic_error_report(self, tmp_path: Path, monkeypatch) -> None:
        config_path = tmp_path / "remote.toml"
        snapshot_db = tmp_path / "paper_snapshot.db"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        snapshot_db.write_text("")
        embed_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)
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
            "request": {"index_meta": {"dimensions": 4}, "group": {"doc_id": "paper-1"}, "chunks": [{"id": "chunk-1"}]},
        }
        error = SemanticPushError(failure, PushSemanticStats(errors=[failure]))

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=[{"paper_id": "paper-1", "paper_title": "Paper"}]),
            patch("deepresearch_flow.paper.snapshot.push.push_papers", return_value=PushStats(total=1, added=1, batches_sent=1)),
            patch("deepresearch_flow.paper.vector_store.load_index_meta", return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=[{"doc_id": "paper-1", "template_tag": "", "content_hash": "h1", "vector": [0.1, 0.2, 0.3, 0.4]}]),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.group_chunks_for_push",
                return_value=[
                    {"group": {"doc_id": "paper-1", "template_tag": "", "group_hash": "g", "part_index": 0, "part_count": 1, "is_final_part": True}, "chunks": [{}]},
                ],
            ),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", side_effect=error),
            patch("deepresearch_flow.paper.db.tqdm"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                paper,
                [
                    "db", "api", "push",
                    "--snapshot-db", str(snapshot_db),
                    "--config", str(config_path),
                    "--embed-db", str(embed_dir),
                    "--only-api",
                ],
            )

        assert result.exit_code != 0
        report_path = tmp_path / "push-semantic-errors.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report[0]["doc_id"] == "paper-1"


class TestApiPushSemanticCli:
    def test_push_semantic_expands_chunk_window_to_full_groups(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)
        chunks = [
            {"id": "paper-1___title_0", "doc_id": "paper-1", "template_tag": "", "chunk_type": "title", "chunk_index": 0, "content_hash": "h1", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"id": "paper-1___content_0", "doc_id": "paper-1", "template_tag": "", "chunk_type": "content", "chunk_index": 0, "content_hash": "h2", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"id": "paper-2___title_0", "doc_id": "paper-2", "template_tag": "", "chunk_type": "title", "chunk_index": 0, "content_hash": "h3", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"id": "paper-2___content_0", "doc_id": "paper-2", "template_tag": "", "chunk_type": "content", "chunk_index": 0, "content_hash": "h4", "vector": [0.1, 0.2, 0.3, 0.4]},
        ]

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=chunks),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(batches_sent=1, inserted=2, updated=0, skipped=0, deleted=0),
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

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1},
            ),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks", return_value=PushSemanticStats(batches_sent=1, inserted=1, updated=0, skipped=0, deleted=0)) as mock_push_semantic,
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

    def test_push_semantic_empty_retry_report_is_noop_before_embed_validation(self, tmp_path: Path) -> None:
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
            json.dumps([{"path": "summary/paper-1/default.json", "error": "boom"}], ensure_ascii=False),
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
            json.dumps([{"request": {"group": {"doc_id": "paper-1"}, "chunks": []}}], ensure_ascii=False),
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
        assert "--retry-failed cannot be combined with --start-chunk-idx/--end-chunk-idx" in result.output

    def test_push_semantic_skips_when_selected_window_is_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / "remote.toml"
        embed_dir = tmp_path / "embed_vectors"
        _write_config(config_path)
        embed_dir.mkdir()

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=[]),
            patch("deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks") as mock_push_semantic,
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

        config = RemoteConfig(api_base_url="https://api.example.com", admin_token="token", batch_size=10)
        papers = [
            {"paper_id": "paper-0", "paper_title": "Paper 0"},
            {"paper_id": "paper-1", "paper_title": "Paper 1"},
            {"paper_id": "paper-2", "paper_title": "Paper 2"},
            {"paper_id": "paper-3", "paper_title": "Paper 3"},
        ]
        chunks = [
            {"doc_id": "paper-0", "template_tag": "", "content_hash": "h0", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"doc_id": "paper-1", "template_tag": "", "content_hash": "h1", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"doc_id": "paper-2", "template_tag": "", "content_hash": "h2", "vector": [0.1, 0.2, 0.3, 0.4]},
            {"doc_id": "paper-3", "template_tag": "", "content_hash": "h3", "vector": [0.1, 0.2, 0.3, 0.4]},
        ]

        with (
            patch("deepresearch_flow.paper.snapshot.push.load_remote_config", return_value=config),
            patch("deepresearch_flow.paper.snapshot.push.extract_papers_from_db", return_value=papers),
            patch("deepresearch_flow.paper.snapshot.push.push_papers", return_value=PushStats(total=2, added=2, batches_sent=1)) as mock_push,
            patch(
                "deepresearch_flow.paper.vector_store.load_index_meta",
                return_value={"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1},
            ),
            patch("deepresearch_flow.paper.vector_store.open_store", return_value=object()),
            patch("deepresearch_flow.paper.vector_store.read_all_chunks", return_value=chunks),
            patch(
                "deepresearch_flow.paper.snapshot.push_semantic.push_semantic_chunks",
                return_value=PushSemanticStats(batches_sent=1, inserted=2, updated=0, skipped=0, deleted=0),
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
                    "--only-api",
                    "--start-idx",
                    "1",
                    "--end-idx",
                    "3",
                ],
            )

        assert result.exit_code == 0
        assert [paper["paper_id"] for paper in mock_push.call_args.args[0]] == ["paper-1", "paper-2"]
        assert [chunk["doc_id"] for chunk in mock_push_semantic.call_args.args[0]] == ["paper-1", "paper-2"]
