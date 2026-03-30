from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.paper.snapshot.push import PushStats, RemoteConfig
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
