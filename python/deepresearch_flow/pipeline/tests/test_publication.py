from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
from typing import override

import pytest

from deepresearch_flow.pipeline.publication import (
    LocalFormalStore,
    MirroredFormalStore,
    PublicationBundle,
    PublicationConflict,
    PublicationError,
    PublicationResource,
    PublicationWorker,
    WebDavFormalStore,
    build_publication_bundle_from_manifest,
    build_publication_bundle,
    publish_bundle,
    queue_publication,
)


def _cached_bundle_from_manifest(
    manifest: dict[str, object], formal_root: Path, work_root: Path
) -> PublicationBundle:
    records = manifest["resources"]
    assert isinstance(records, list)
    resources: dict[str, bytes] = {}
    for record in records:
        assert isinstance(record, dict)
        path = str(record["path"])
        resources[path] = (formal_root / path).read_bytes()
    return build_publication_bundle_from_manifest(
        manifest, resources, work_dir=work_root
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
    assert any(
        path.startswith("summary/" + first.paper_id + "/simple/")
        and path.endswith(".json")
        for path in first.resource_map
    )
    assert any(path.startswith("md_translate/en/") for path in first.resource_map)
    assert first.references["pdf"].startswith("pdf/")


def test_local_formal_store_is_idempotent(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    store.put("pdf/abc.pdf", b"%PDF-1.7")
    store.put("pdf/abc.pdf", b"%PDF-1.7")

    assert (tmp_path / "formal/pdf/abc.pdf").read_bytes() == b"%PDF-1.7"


def test_local_formal_store_rejects_immutable_path_overwrite(tmp_path: Path) -> None:
    store = LocalFormalStore(tmp_path / "formal")
    store.put("summary/paper/simple.json", b"first")

    with pytest.raises(PublicationConflict):
        store.put("summary/paper/simple.json", b"second")

    assert (tmp_path / "formal/summary/paper/simple.json").read_bytes() == b"first"


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
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "paper" in tables:
            assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 0
        if "pipeline_publication_receipt" in tables:
            assert conn.execute(
                "SELECT COUNT(*) FROM pipeline_publication_receipt"
            ).fetchone()[0] == 0
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


def test_matching_receipt_retry_skips_formal_store_writes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")
    publish_bundle(bundle, db, store)

    writes: list[str] = []

    class UnavailableStore:
        def put(self, relative_path: str, data: bytes) -> None:
            writes.append(relative_path)
            raise OSError("formal store unavailable")

    result = publish_bundle(
        bundle,
        db,
        UnavailableStore(),
        indexer=lambda _: None,
    )

    assert result.already_published is True
    assert writes == []


def test_conflicting_receipt_fails_before_formal_store_writes(tmp_path: Path) -> None:
    first = _bundle(tmp_path, job_id="receipt-owner")
    conflicting = _bundle(tmp_path, job_id="receipt-owner", title="Different paper")
    db = tmp_path / "snapshot.sqlite3"
    publish_bundle(first, db, LocalFormalStore(tmp_path / "formal"))
    writes: list[str] = []

    class RecordingStore:
        def put(self, relative_path: str, data: bytes) -> None:
            writes.append(relative_path)

    with pytest.raises(PublicationConflict):
        publish_bundle(conflicting, db, RecordingStore())

    assert writes == []


def test_non_content_addressed_bundle_is_rejected_before_webdav_write(
    tmp_path: Path,
) -> None:
    source = _bundle(tmp_path, job_id="source-bundle")
    resource = PublicationResource(
        relative_path="custom/stable.json",
        content=b"immutable content",
        digest=hashlib.sha256(b"immutable content").hexdigest(),
        size=len(b"immutable content"),
        media_type="application/json",
    )
    bundle = PublicationBundle(
        job_id="custom-bundle",
        paper_id=source.paper_id,
        paper=source.paper,
        bibtex=source.bibtex,
        resource_map={resource.relative_path: resource},
        references={},
        bundle_digest="f" * 64,
    )

    class Storage:
        def __init__(self) -> None:
            self.uploads: list[tuple[str, bytes]] = []

        def upload(self, path: str, data: bytes) -> None:
            self.uploads.append((path, data))

    storage = Storage()
    with pytest.raises(ValueError, match="content-addressed"):
        publish_bundle(
            bundle,
            tmp_path / "snapshot.sqlite3",
            WebDavFormalStore(storage),
        )

    assert storage.uploads == []


def test_snapshot_lock_is_released_before_indexing(tmp_path: Path) -> None:
    first = _bundle(tmp_path, job_id="slow-index")
    second = _bundle(tmp_path, job_id="fast-index", title="Second paper")
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")
    index_started = Event()
    release_index = Event()
    second_snapshot_committed = Event()
    results: list[object] = []

    def slow_index(_: object) -> None:
        index_started.set()
        assert release_index.wait(timeout=3)

    def publish_first() -> None:
        results.append(publish_bundle(first, db, store, indexer=slow_index))

    first_thread = Thread(target=publish_first, daemon=True)
    first_thread.start()
    assert index_started.wait(timeout=3)
    try:
        publish_bundle(
            second,
            db,
            store,
            indexer=lambda _: second_snapshot_committed.set(),
        )
        assert second_snapshot_committed.is_set()
    finally:
        release_index.set()
        first_thread.join(timeout=3)

    assert len(results) == 1


def test_receipt_retry_releases_snapshot_lock_before_indexing(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, job_id="receipt-slow-index")
    second = _bundle(tmp_path, job_id="receipt-fast-index", title="Second paper")
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")
    publish_bundle(bundle, db, store)
    index_started = Event()
    release_index = Event()

    def slow_index(_: object) -> None:
        index_started.set()
        assert release_index.wait(timeout=3)

    first_thread = Thread(
        target=lambda: publish_bundle(bundle, db, store, indexer=slow_index),
        daemon=True,
    )
    first_thread.start()
    assert index_started.wait(timeout=3)
    try:
        publish_bundle(second, db, store)
    finally:
        release_index.set()
        first_thread.join(timeout=3)

    assert not first_thread.is_alive()


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


@pytest.mark.parametrize("backend", ["local", "webdav"])
def test_warning_retry_reconstructs_from_published_cache_after_private_retention(
    tmp_path: Path, backend: str
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    work = tmp_path / "work"
    previews = tmp_path / "previews"
    formal = tmp_path / "formal"
    artifacts = ArtifactStore(work, previews)
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(work, job_id=job_id)

    class FakeWebDav:
        def __init__(self) -> None:
            self.uploads: list[str] = []

        def upload(self, path: str, data: bytes) -> None:
            del data
            self.uploads.append(path)

    remote = FakeWebDav()
    cache = LocalFormalStore(formal)
    primary = (
        LocalFormalStore(formal)
        if backend == "local"
        else WebDavFormalStore(remote)
    )
    first = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        MirroredFormalStore(primary, cache),
        bundle_builder=lambda _: bundle,
        indexer=lambda _: (_ for _ in ()).throw(RuntimeError("index unavailable")),
    ).run_once()
    assert first[0].status == "published_with_warning"
    manifest = state.get_publication_manifest(job_id)
    assert manifest is not None
    assert all("content" not in record for record in manifest["resources"])

    private_work = artifacts.begin(job_id, "ocr")
    private_work.write(b"private")
    private_work.promote()
    artifacts.protect(job_id, "preview_pdf", b"preview")
    assert state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), limit=1
    ) == [job_id]
    assert artifacts.resolve(job_id, "ocr") is None

    state.retry_indexing(job_id, int(state.get_job(job_id)["revision"]))

    class BrokenPrimary:
        def put(self, relative_path: str, data: bytes) -> None:
            del relative_path, data
            raise AssertionError("index-only retry must not rewrite formal resources")

    rebuilt = _cached_bundle_from_manifest(manifest, formal, work)
    index_marker = tmp_path / "index-marker"

    def recover_indexer(value: object) -> None:
        assert isinstance(value, PublicationBundle)
        assert value.resource_map
        index_marker.write_text(value.paper_id, encoding="utf-8")

    second = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        MirroredFormalStore(BrokenPrimary(), LocalFormalStore(formal)),
        bundle_builder=lambda _: rebuilt,
        indexer=recover_indexer,
    ).run_once()
    assert second[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"
    if backend == "webdav":
        assert len(remote.uploads) == len(bundle.resources)
    assert index_marker.read_text(encoding="utf-8") == bundle.paper_id


def test_runtime_public_worker_recovers_warning_from_manifest_cache(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineConfig, PipelineState
    from deepresearch_flow.pipeline.runtime import (
        build_publication_bundle_from_state,
        run_worker_until_stopped,
    )

    work = tmp_path / "work"
    previews = tmp_path / "previews"
    formal = tmp_path / "formal"
    snapshot = tmp_path / "snapshot.sqlite3"
    config = PipelineConfig(
        enabled=True,
        work_dir=str(work),
        preview_root=str(previews),
        static_root=str(formal),
        queue_db=str(tmp_path / "queue.sqlite3"),
        snapshot_db=str(snapshot),
    )
    artifacts = ArtifactStore(work, previews)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(work, job_id=job_id)
    first = PublicationWorker(
        state,
        snapshot,
        LocalFormalStore(formal),
        bundle_builder=lambda _: bundle,
        indexer=lambda _: (_ for _ in ()).throw(RuntimeError("index unavailable")),
    ).run_once()
    assert first[0].status == "published_with_warning"

    private_work = artifacts.begin(job_id, "ocr")
    private_work.write(b"private")
    private_work.promote()
    artifacts.protect(job_id, "preview_pdf", b"preview")
    assert state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), limit=1
    ) == [job_id]
    state.retry_indexing(job_id, int(state.get_job(job_id)["revision"]))

    class ProcessingIdle:
        def run_once(self, job_ids: list[str] | None = None) -> list[object]:
            del job_ids
            return []

    marker = tmp_path / "index-marker"

    def fake_indexer(value: object) -> None:
        assert isinstance(value, PublicationBundle)
        assert value.resource_map
        marker.write_text(value.paper_id, encoding="utf-8")

    class BrokenPrimary:
        def put(self, relative_path: str, data: bytes) -> None:
            del relative_path, data
            raise AssertionError("index-only retry must not rewrite formal resources")

    recovery_worker = PublicationWorker(
        state,
        snapshot,
        MirroredFormalStore(BrokenPrimary(), LocalFormalStore(formal)),
        bundle_builder=lambda current_job_id: build_publication_bundle_from_state(
            current_job_id, state, artifacts, config
        ),
        indexer=fake_indexer,
    )

    result = run_worker_until_stopped(
        config,
        state,
        artifacts,
        snapshot_db=snapshot,
        processing_worker=ProcessingIdle(),
        publication_worker=recovery_worker,
        stop_event=Event(),
        poll_interval_seconds=0,
        cleanup_interval_seconds=3600,
        max_cycles=1,
        worker_id="runtime-recovery",
    )
    assert result.published_jobs == 1
    assert state.get_job(job_id)["status"] == "published"
    assert marker.read_text(encoding="utf-8") == bundle.paper_id


def test_publication_worker_stops_after_current_job_without_claiming_next(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "previews")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    jobs: list[str] = []
    bundles: dict[str, PublicationBundle] = {}
    for title in ("First paper", "Second paper"):
        job_id = state.create_job()
        state.admin_transition(job_id, "running")
        state.admin_transition(job_id, "review_ready")
        queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
        jobs.append(job_id)
        bundles[job_id] = _bundle(tmp_path, job_id=job_id, title=title)
    stop = Event()

    class StopAfterCurrent(LocalFormalStore):
        def put(self, relative_path: str, data: bytes) -> None:
            super().put(relative_path, data)
            stop.set()

    worker = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        StopAfterCurrent(tmp_path / "formal"),
        bundle_builder=lambda job_id: bundles[job_id],
        stop_requested=stop.is_set,
    )

    results = worker.run_once()

    assert len(results) == 1
    assert state.get_job(jobs[0])["status"] == "published"
    assert state.get_job(jobs[1])["status"] == "publish_queued"


def test_expired_publication_lease_requeues_and_receipt_retry_indexes_only(tmp_path: Path) -> None:
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


def test_publication_worker_heartbeat_keeps_blocking_formal_write_lease_alive(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=artifacts,
        lease_seconds=2,
        heartbeat_seconds=1,
    )
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    started = Event()
    release = Event()

    class BlockingStore(LocalFormalStore):
        @override
        def put(self, relative_path: str, data: bytes) -> None:
            started.set()
            if not release.wait(timeout=8):
                raise TimeoutError("test store was not released")
            super().put(relative_path, data)

    worker = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        BlockingStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
    )
    result: list[object] = []
    thread = Thread(target=lambda: result.extend(worker.run_once()), daemon=True)
    thread.start()
    assert started.wait(timeout=3)
    time.sleep(2.5)
    release.set()
    thread.join(timeout=8)

    assert result and result[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"


def test_stale_publication_worker_cannot_commit_after_lease_takeover_during_write(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    takeover = Event()
    release = Event()

    class TakeoverStore(LocalFormalStore):
        @override
        def put(self, relative_path: str, data: bytes) -> None:
            if not takeover.is_set():
                takeover.set()
                state.recover_expired(
                    now=datetime.now(timezone.utc) + timedelta(days=1)
                )
            release.set()
            super().put(relative_path, data)

    result = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        TakeoverStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
    ).run_once()[0]

    assert result.status == "failed"
    assert state.get_job(job_id)["status"] == "publish_queued"
    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "paper" in tables:
            assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 0
        if "pipeline_publication_receipt" in tables:
            assert conn.execute(
                "SELECT COUNT(*) FROM pipeline_publication_receipt"
            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_publication_worker_heartbeat_keeps_blocking_indexer_lease_alive(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=artifacts,
        lease_seconds=2,
        heartbeat_seconds=1,
    )
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    started = Event()
    release = Event()

    def blocking_index(_: object) -> None:
        started.set()
        if not release.wait(timeout=8):
            raise TimeoutError("test indexer was not released")

    worker = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=blocking_index,
    )
    result: list[object] = []
    thread = Thread(target=lambda: result.extend(worker.run_once()), daemon=True)
    thread.start()
    assert started.wait(timeout=3)
    time.sleep(2.5)
    release.set()
    thread.join(timeout=8)

    assert result and result[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"


def test_guarded_index_ignores_heartbeat_lock_contention(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    heartbeat_blocked = Event()

    class GuardContentionState(PipelineState):
        def heartbeat(self, job_id: str, lease_token: str, now=None):  # noqa: ANN001
            if heartbeat_blocked.is_set():
                raise sqlite3.OperationalError("database is locked")
            return super().heartbeat(job_id, lease_token, now=now)

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = GuardContentionState(
        tmp_path / "queue.sqlite3",
        artifact_store=artifacts,
        heartbeat_seconds=1.0,
    )
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    started = Event()

    def blocking_index(_: object) -> None:
        heartbeat_blocked.set()
        started.set()
        time.sleep(1.3)

    result = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=blocking_index,
    ).run_once()

    assert started.is_set()
    assert result[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"


def test_cancellation_before_and_during_formal_writes_prevents_receipt(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    def setup() -> tuple[PipelineState, str, object]:
        artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
        state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
        job_id = state.create_job()
        state.admin_transition(job_id, "running")
        state.admin_transition(job_id, "review_ready")
        queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
        return state, job_id, _bundle(tmp_path, job_id=job_id)

    state, job_id, bundle = setup()
    state.request_cancel(job_id)
    before = PublicationWorker(
        state,
        tmp_path / "before.sqlite3",
        LocalFormalStore(tmp_path / "before-formal"),
        bundle_builder=lambda _: bundle,
    ).run_once()[0]
    assert before.status == "cancelled"
    assert state.get_job(job_id)["status"] == "cancelled"

    state, job_id, bundle = setup()
    uploaded = Event()

    class CancellingStore(LocalFormalStore):
        @override
        def put(self, relative_path: str, data: bytes) -> None:
            super().put(relative_path, data)
            if not uploaded.is_set():
                uploaded.set()
                state.request_cancel(job_id)

    during = PublicationWorker(
        state,
        tmp_path / "during.sqlite3",
        CancellingStore(tmp_path / "during-formal"),
        bundle_builder=lambda _: bundle,
    ).run_once()[0]
    assert during.status == "cancelled"
    conn = sqlite3.connect(tmp_path / "during.sqlite3")
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "paper" in tables:
            assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 0
    finally:
        conn.close()


def test_cancellation_requested_after_receipt_does_not_undo_publication(
    tmp_path: Path,
) -> None:
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    queue_publication(state, job_id, int(state.get_job(job_id)["revision"]))
    bundle = _bundle(tmp_path, job_id=job_id)
    cancellation_thread: list[Thread] = []

    def index(_: object) -> None:
        thread = Thread(target=lambda: state.request_cancel(job_id), daemon=True)
        cancellation_thread.append(thread)
        thread.start()
        time.sleep(0.05)

    result = PublicationWorker(
        state,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        bundle_builder=lambda _: bundle,
        indexer=index,
    ).run_once()[0]

    assert result.status == "published"
    assert state.get_job(job_id)["status"] == "published"
    assert state.get_job(job_id)["cancel_requested"] is False
    cancellation_thread[0].join(timeout=3)
    assert not cancellation_thread[0].is_alive()


def test_summary_templates_are_read_by_snapshot_embed_loader(tmp_path: Path) -> None:
    from deepresearch_flow.paper.embed_source import load_from_snapshot

    paper = _paper()
    paper["templates"] = {
        "simple": {"summary": "short"},
        "detailed": {"summary": "long"},
    }
    bundle = build_publication_bundle(
        "job-templates",
        paper,
        bibtex={"status": "not_provided"},
        resources={"pdf": b"%PDF-1.7", "source_markdown": b"# title"},
        work_dir=tmp_path,
    )
    publish_bundle(bundle, tmp_path / "snapshot.sqlite3", LocalFormalStore(tmp_path / "formal"))

    docs = load_from_snapshot(tmp_path / "snapshot.sqlite3", tmp_path / "formal")
    assert len(docs) == 1
    assert set(docs[0].template_records) == {"simple", "detailed"}
    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        rows = conn.execute(
            "SELECT template_tag,resource_path,content_hash FROM paper_summary "
            "WHERE paper_id=? ORDER BY template_tag",
            (bundle.paper_id,),
        ).fetchall()
    finally:
        conn.close()
    assert {row[0] for row in rows} == {"detailed", "simple"}
    assert all(str(row[1]).startswith(f"summary/{bundle.paper_id}/") for row in rows)
    assert all(len(str(row[2])) == 64 for row in rows)


def test_snapshot_embed_loader_falls_back_to_legacy_summary_path(tmp_path: Path) -> None:
    from deepresearch_flow.paper.embed_source import load_from_snapshot

    bundle = _bundle(tmp_path, job_id="job-legacy-summary")
    store = LocalFormalStore(tmp_path / "formal")
    publish_bundle(bundle, tmp_path / "snapshot.sqlite3", store)
    current_path = bundle.references["summary_json"]
    current_content = (tmp_path / "formal" / current_path).read_bytes()
    legacy_path = tmp_path / "formal" / "summary" / bundle.paper_id / "simple.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(current_content)
    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        conn.execute(
            "UPDATE paper_summary SET resource_path=NULL,content_hash=NULL "
            "WHERE paper_id=? AND template_tag='simple'",
            (bundle.paper_id,),
        )
        conn.commit()
    finally:
        conn.close()

    docs = load_from_snapshot(tmp_path / "snapshot.sqlite3", tmp_path / "formal")
    assert docs[0].template_records["simple"][0]["summary"] == "A short summary."


def test_no_bibtex_input_removes_stale_metadata_value(tmp_path: Path) -> None:
    paper = _paper()
    paper["bibtex"] = {
        "raw": "@article{stale, title={Old}}",
        "key": "stale",
        "type": "article",
        "fields": {"title": "Old"},
    }
    bundle = build_publication_bundle(
        "job-no-bib",
        paper,
        bibtex={"status": "not_provided"},
        resources={"pdf": b"%PDF-1.7", "source_markdown": b"# title"},
        work_dir=tmp_path,
    )
    publish_bundle(bundle, tmp_path / "snapshot.sqlite3", LocalFormalStore(tmp_path / "formal"))

    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper_bibtex").fetchone()[0] == 0
    finally:
        conn.close()


def test_duplicate_paper_publications_never_overwrite_immutable_resources(
    tmp_path: Path,
) -> None:
    first = _bundle(tmp_path, job_id="job-first")
    second = build_publication_bundle(
        "job-second",
        _paper(),
        bibtex={"status": "not_provided"},
        resources={
            "pdf": b"%PDF-1.7 tiny",
            "source_markdown": b"# different source\n",
            "summary_json": b'{"summary":"different"}\n',
            "translated_markdown": b"# different translation\n",
        },
        work_dir=tmp_path,
    )
    store = LocalFormalStore(tmp_path / "formal")
    publish_bundle(first, tmp_path / "snapshot.sqlite3", store)
    with pytest.raises(PublicationConflict):
        publish_bundle(second, tmp_path / "snapshot.sqlite3", store)

    summary_resource = first.resource_map[first.references["summary_json"]]
    assert (
        tmp_path / "formal" / first.references["summary_json"]
    ).read_bytes() == summary_resource.content


def test_concurrent_duplicate_jobs_publish_one_snapshot_paper(tmp_path: Path) -> None:
    first = _bundle(tmp_path, job_id="job-concurrent-first")
    second = _bundle(tmp_path, job_id="job-concurrent-second")
    db = tmp_path / "snapshot.sqlite3"
    store = LocalFormalStore(tmp_path / "formal")

    def attempt(bundle: PublicationBundle) -> object:
        try:
            return publish_bundle(bundle, db, store)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (first, second)))

    assert sum(isinstance(result, PublicationConflict) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM paper").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM pipeline_publication_receipt"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_queue_guard_blocks_takeover_until_snapshot_and_indexing_finish(
    tmp_path: Path,
) -> None:
    from contextlib import contextmanager
    from deepresearch_flow.pipeline import ArtifactStore, PipelineState

    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    initial = state.acquire_lease(job_id, "publisher")
    assert initial is not None
    state.transition(job_id, "review_ready", initial.token)
    state.admin_transition(job_id, "publish_queued")
    lease = state.acquire_lease(job_id, "publisher")
    assert lease is not None
    state.transition(job_id, "publishing", lease.token)
    bundle = _bundle(tmp_path, job_id=job_id)
    takeover_started = Event()
    takeover_finished = Event()
    snapshot_committed = Event()
    recovered: list[str] = []
    takeover_thread: list[Thread] = []

    def attempt_takeover() -> None:
        takeover_started.set()
        recovered.extend(
            state.recover_expired(now=datetime.now(timezone.utc) + timedelta(days=1))
        )
        takeover_finished.set()

    @contextmanager
    def guarded() -> object:
        with state.lease_guard(
            job_id, lease.token, owner=lease.owner, reject_cancel=True
        ) as guard:
            thread = Thread(target=attempt_takeover, daemon=True)
            takeover_thread.append(thread)
            thread.start()
            assert takeover_started.wait(timeout=2)
            assert not takeover_finished.wait(timeout=0.2)
            yield guard

    def index(_: object) -> None:
        snapshot_committed.set()
        assert not takeover_finished.wait(timeout=0.2)

    result = publish_bundle(
        bundle,
        tmp_path / "snapshot.sqlite3",
        LocalFormalStore(tmp_path / "formal"),
        indexer=index,
        lease_guard=guarded,
    )
    takeover_thread[0].join(timeout=3)

    assert result.paper_id == bundle.paper_id
    assert snapshot_committed.is_set()
    assert takeover_finished.is_set()
    assert recovered == [job_id]
    conn = sqlite3.connect(tmp_path / "snapshot.sqlite3")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM pipeline_publication_receipt WHERE job_id=?",
            (job_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()
