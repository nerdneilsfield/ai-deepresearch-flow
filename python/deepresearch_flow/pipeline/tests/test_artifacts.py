from pathlib import Path
from datetime import datetime, timezone
import hashlib
import os

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


def test_roots_must_be_physically_separate_and_job_symlink_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path / "same", tmp_path / "same")
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path / "root", tmp_path / "root" / "formal")
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    job_dir = store.work_dir / store._job_key("job-1")
    os.symlink(store.formal_root, job_dir)
    with pytest.raises(ValueError):
        store.begin("job-1", "ocr")


def test_unsafe_artifact_kind_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    with pytest.raises(ValueError):
        store.begin("job-1", "ocr*")


def test_kind_lookup_does_not_prefix_collide(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    for kind in ("ocr", "ocr-extra"):
        pending = store.begin("job-1", kind)
        pending.write(kind.encode())
        pending.promote()
    resolved_ocr = store.resolve("job-1", "ocr")
    resolved_extra = store.resolve("job-1", "ocr-extra")
    assert resolved_ocr is not None and resolved_ocr.digest == hashlib.sha256(b"ocr").hexdigest()
    assert resolved_extra is not None and resolved_extra.digest == hashlib.sha256(b"ocr-extra").hexdigest()


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
    assert store.cleanup({"published": {"status": "published", "terminal_at": "2020-01-01T00:00:00+00:00"}}, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == ["published"]


def test_cleanup_uses_terminal_timestamp_not_directory_mtime(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "work", tmp_path / "formal", retention_days=7)
    pending = store.begin("published", "ocr")
    pending.write(b"old")
    artifact = pending.promote()
    os.utime(artifact.path.parent, (1, 1))
    jobs = {"published": {"status": "published", "terminal_at": "2030-01-01T00:00:00+00:00"}}
    assert store.cleanup(jobs, now=datetime(2030, 1, 2, tzinfo=timezone.utc)) == []


def test_protected_previews_use_private_root_and_expire_with_work_artifacts(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(
        tmp_path / "work",
        tmp_path / "previews",
        retention_days=1,
    )
    static_root = (tmp_path / "static").resolve()
    static_root.mkdir()
    job_id = "published"
    preview = store.protect(job_id, "preview_pdf", b"preview")
    pending = store.begin(job_id, "ocr")
    pending.write(b"work")
    pending.promote()
    (static_root / "published.pdf").write_bytes(b"formal")

    assert preview.path.is_relative_to(store.preview_root)
    assert not preview.path.is_relative_to(static_root)
    assert store.cleanup(
        {
            job_id: {
                "status": "published",
                "terminal_at": "2020-01-01T00:00:00+00:00",
            }
        },
        now=datetime(2030, 1, 1, tzinfo=timezone.utc),
    ) == [job_id]
    assert not preview.path.exists()
    assert not (store.work_dir / store._job_key(job_id)).exists()
    assert (static_root / "published.pdf").exists()
