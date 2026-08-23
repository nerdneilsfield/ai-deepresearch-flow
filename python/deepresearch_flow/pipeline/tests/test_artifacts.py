from pathlib import Path
from datetime import datetime, timezone

import pytest

from deepresearch_flow.pipeline.artifacts import ArtifactStore


def test_artifact_is_invisible_until_atomic_promotion(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    pending = store.begin("job-1", "ocr")
    assert store.resolve("job-1", "ocr") is None
    pending.write(b"result")
    artifact = pending.promote()
    assert artifact.size == 6
    resolved = store.resolve("job-1", "ocr")
    assert resolved is not None
    assert resolved.digest == artifact.digest


def test_artifact_resolution_rejects_wrong_kind_and_escape(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    pending = store.begin("job-1", "ocr")
    pending.write(b"x")
    artifact = pending.promote()
    with pytest.raises(FileNotFoundError):
        store.resolve("job-1", "extract")
    with pytest.raises(ValueError):
        store.resolve_path(artifact.path.parent / ".." / "other")


def test_cleanup_removes_only_expired_terminal_work(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal", retention_days=1)
    for job_id in ("published", "failed"):
        pending = store.begin(job_id, "ocr")
        pending.write(job_id.encode())
        pending.promote()
    (tmp_path / "formal" / "keep.txt").write_text("formal", encoding="utf-8")
    removed = store.cleanup({"published": "published", "failed": "failed"}, force=True)
    assert "published" in removed
    assert store.resolve("failed", "ocr") is not None
    assert (tmp_path / "formal" / "keep.txt").exists()


def test_cleanup_honors_retention_cutoff(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal", retention_days=7)
    pending = store.begin("published", "ocr")
    pending.write(b"old")
    artifact = pending.promote()
    assert store.cleanup({"published": "published"}, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == ["published"]
