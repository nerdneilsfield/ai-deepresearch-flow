"""Tests for static file push — backend-agnostic using fake storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepresearch_flow.storage.base import StorageAuthError
from deepresearch_flow.paper.snapshot.push_static import (
    PushStaticStats,
    load_retry_files,
    push_static_files,
    write_error_report,
)


class FakeStorage:
    """In-memory RemoteStorage for testing."""

    def __init__(
        self,
        *,
        existing: set[str] | None = None,
        fail_uploads: bool = False,
        auth_fail: bool = False,
    ) -> None:
        self._existing = existing or set()
        self._fail_uploads = fail_uploads
        self._auth_fail = auth_fail
        self.uploaded: dict[str, bytes] = {}
        self.mkdirs: list[str] = []

    def exists(self, remote_path: str) -> bool:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        return remote_path in self._existing

    def mkdir(self, remote_path: str) -> None:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        self.mkdirs.append(remote_path)

    def upload(self, remote_path: str, data: bytes) -> None:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        if self._fail_uploads:
            raise RuntimeError("Upload failed: HTTP 500")
        self.uploaded[remote_path] = data

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeStorage":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _setup_static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "static_export"
    (root / "pdf").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "md").mkdir()
    (root / "summary" / "paper1").mkdir(parents=True)

    (root / "pdf" / "abc123.pdf").write_bytes(b"%PDF-fake")
    (root / "images" / "def456.png").write_bytes(b"\x89PNG-fake")
    (root / "md" / "ghi789.md").write_text("# Hello")
    (root / "summary" / "paper1" / "default.json").write_text('{"k":"v"}')
    return root


class TestPushStaticFiles:
    def test_upload_new_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert stats.uploaded == 4
        assert stats.skipped == 0
        assert stats.failed == 0
        assert len(storage.uploaded) == 4

    def test_skip_existing_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(existing={
            "pdf/abc123.pdf", "images/def456.png",
            "md/ghi789.md", "summary/paper1/default.json",
        })
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.skipped == 4
        assert stats.failed == 0

    def test_record_failed_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(fail_uploads=True)
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.failed == 4
        assert len(stats.failed_files) == 4

    def test_auth_failure_aborts(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(auth_fail=True)
        with pytest.raises(StorageAuthError):
            push_static_files(root, storage)

    def test_only_files_filter(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage, only_files=["pdf/abc123.pdf"])

        assert stats.uploaded == 1
        assert len(storage.uploaded) == 1
        assert "pdf/abc123.pdf" in storage.uploaded

    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.skipped == 0
        assert stats.failed == 0

    def test_per_directory_stats(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert "pdf/" in stats.per_directory
        assert stats.per_directory["pdf/"]["uploaded"] == 1
        assert "summary/" in stats.per_directory
        assert stats.per_directory["summary/"]["uploaded"] == 1

    def test_mkdir_called_for_parents(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        push_static_files(root, storage)

        assert "summary" in storage.mkdirs
        assert "summary/paper1" in storage.mkdirs


class TestErrorReport:
    def test_write_and_load(self, tmp_path: Path) -> None:
        report_path = tmp_path / "errors.json"
        failed = [
            {"path": "pdf/abc.pdf", "error": "timeout"},
            {"path": "images/def.png", "error": "500"},
        ]
        write_error_report(failed, report_path)
        loaded = load_retry_files(report_path)
        assert loaded == ["pdf/abc.pdf", "images/def.png"]

    def test_load_empty_report(self, tmp_path: Path) -> None:
        report_path = tmp_path / "errors.json"
        report_path.write_text("[]")
        loaded = load_retry_files(report_path)
        assert loaded == []
