from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from typing import override

import pytest

from deepresearch_flow.pipeline.publication import (
    LocalFormalStore,
    PublicationConflict,
    PublicationError,
    PublicationWorker,
    WebDavFormalStore,
    build_publication_bundle,
    publish_bundle,
    queue_publication,
)


def _paper(title: str = "Tiny paper") -> dict[str, object]:
    return {
        "paper_title": title,
        "paper_authors": ["Ada Lovelace"],
        "publication_date": "2026",
        "publication_venue": "Test Journal",
        "templates": {"simple": {"summary": "A short summary."}},
        "source_hash": "source-1",
    }


def _bundle(tmp_path: Path, *, job_id: str = "job-1", title: str = "Tiny paper"):
    return build_publication_bundle(
        job_id,
        _paper(title),
        bibtex={"status": "not_provided"},
        resources={
            "pdf": b"%PDF-1.7 tiny",
            "source_markdown": b"# Tiny paper\n",
            "summary_json": b'{"summary":"A short summary."}\n',
            "translated_markdown": b"# Tiny paper\n",
        },
        work_dir=tmp_path,
    )


def test_bundle_is_normalized_content_addressed_and_deterministic(tmp_path: Path) -> None:
    first = _bundle(tmp_path)
    second = _bundle(tmp_path, title="  Tiny paper  ")

    assert first.bundle_digest == second.bundle_digest
    assert first.paper["paper_title"] == "Tiny paper"
    assert len(first.resource_map) == 4
    assert any(path.startswith("pdf/") and path.endswith(".pdf") for path in first.resource_map)
    assert any(path.startswith("md/") and path.endswith(".md") for path in first.resource_map)
    assert "summary/" + first.paper_id + ".json" in first.resource_map
    assert any(path.startswith("md_translate/en/") for path in first.resource_map)
    assert first.references["pdf"].startswith("pdf/")


def test_local_formal_store_is_idempotent(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    store.put("pdf/abc.pdf", b"%PDF-1.7")
    store.put("pdf/abc.pdf", b"%PDF-1.7")

    assert (tmp_path / "formal/pdf/abc.pdf").read_bytes() == b"%PDF-1.7"


def test_webdav_formal_store_upload_does_not_require_head() -> None:
    class FakeStorage:
        def __init__(self) -> None:
            self.uploads: list[tuple[str, bytes]] = []

        def upload(self, path: str, data: bytes) -> None:
            self.uploads.append((path, data))

        def exists(self, path: str) -> bool:
            raise AssertionError("publication must not require WebDAV HEAD")

    storage = FakeStorage()
    WebDavFormalStore(storage).put("pdf/abc.pdf", b"pdf")

    assert storage.uploads == [("pdf/abc.pdf", b"pdf")]


def test_formal_store_rejects_absolute_and_parent_paths(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")

    with pytest.raises(ValueError):
        store.put("/outside.pdf", b"x")
    with pytest.raises(ValueError):
        store.put("pdf/../outside.pdf", b"x")


def test_static_failure_prevents_snapshot_commit(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    class BrokenStore(LocalFormalStore):
        @override
        def put(self, relative_path: str, data: bytes) -> None:
            raise OSError("storage unavailable")

    with pytest.raises(PublicationError, match="formal resource write failed"):
        publish_bundle(bundle, tmp_path / "snapshot.sqlite3", BrokenStore(tmp_path / "formal"))

    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper'"
        ).fetchone() is None
    finally:
        conn.close()


def test_receipt_makes_retry_idempotent_and_conflicting_digest_visible(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")

    first = publish_bundle(bundle, db, store)
    second = publish_bundle(bundle, db, store)
    assert first.paper_id == second.paper_id
    assert second.already_published is True

    conflicting = _bundle(tmp_path, job_id=bundle.job_id, title="Different paper")
    with pytest.raises(PublicationConflict):
        publish_bundle(conflicting, db, store)


def test_concurrent_publication_commits_are_serialized(tmp_path: Path) -> None:
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")
    bundles = [_bundle(tmp_path, job_id=f"job-{index}", title=f"Paper {index}") for index in range(2)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: publish_bundle(item, db, store), bundles))

    assert {item.paper_id for item in results}.__len__() == 2
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM pipeline_publication_receipt").fetchone()[0] == 2
    finally:
        conn.close()


def test_indexing_failure_returns_warning_and_retry_only_indexes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")

    calls: list[str] = []

    def fail_index(_: object) -> None:
        calls.append("fail")
        raise RuntimeError("embedding unavailable")

    warning = publish_bundle(bundle, db, store, indexer=fail_index)
    assert warning.index_warning == "embedding unavailable"

    def index(_: object) -> None:
        calls.append("ok")

    recovered = publish_bundle(bundle, db, store, indexer=index)
    assert recovered.already_published is True
    assert recovered.index_warning is None
    assert calls == ["fail", "ok"]


def test_publish_queue_uses_expected_revision_cas(tmp_path: Path) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    revision = int(state.get_job(job_id)["revision"])

    queued = queue_publication(state, job_id, revision)
    assert queued["status"] == "publish_queued"
    with pytest.raises(ValueError, match="revision"):
        queue_publication(state, job_id, revision)


def test_publication_worker_commits_receipt_before_index_and_reports_warning(tmp_path: Path) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    revision = int(state.get_job(job_id)["revision"])
    queue_publication(state, job_id, revision)
    bundle = _bundle(tmp_path, job_id=job_id)

    first = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=lambda _: (_ for _ in ()).throw(RuntimeError("embedding unavailable")),
    ).run_once()
    assert first[0].status == "published_with_warning"

    retry_revision = int(state.get_job(job_id)["revision"])
    state.retry_indexing(job_id, retry_revision)
    second = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=lambda _: None,
    ).run_once()
    assert second[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"

    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM pipeline_publication_receipt").fetchone()[0] == 1
    finally:
        conn.close()


def test_expired_publication_lease_requeues_and_receipt_retry_indexes_only(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(
        tmp_path / "queue.sqlite3", artifact_store=artifacts, lease_seconds=1
    )
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    lease = state.acquire_lease(
        job_id, "crashed-worker", now=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    assert lease is not None
    state.transition(job_id, "publishing", lease.token)
    publish_bundle(bundle, tmp_path / "snapshot.sqlite3", LocalFormalStore(tmp_path / "formal"))

    recovered = state.recover_expired(
        now=datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=2)
    )
    assert job_id in recovered
    assert state.get_job(job_id)["status"] == "publish_queued"

    result = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=lambda _: None,
    ).run_once()
    assert result[0].status == "published"
