from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from threading import Event, Thread

import pytest

from deepresearch_flow.pipeline.artifacts import Artifact, ArtifactStore
from deepresearch_flow.pipeline.state import LeaseError, PipelineState


def test_enabled_startup_migrates_registered_legacy_previews_idempotently(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    legacy_static = tmp_path / "legacy-static"
    queue = tmp_path / "queue.sqlite3"
    old_artifacts = ArtifactStore(work, legacy_static)
    old_state = PipelineState(queue, artifact_store=old_artifacts)
    job_id = old_state.create_job()
    lease = old_state.acquire_lease(job_id, "legacy")
    assert lease is not None
    old = old_artifacts.protect(job_id, "preview_pdf", b"%PDF-1.7 legacy")
    old_state.register_protected_artifact(job_id, "preview_pdf", old, lease.token)

    previews = tmp_path / "previews"
    new_artifacts = ArtifactStore(work, previews)
    new_state = PipelineState(queue, artifact_store=new_artifacts)

    assert new_state.migrate_legacy_previews(legacy_static) == [job_id]
    migrated = new_state.get_job_details(job_id)["artifacts"][0]
    migrated_path = Path(str(migrated["path"]))
    assert migrated_path.is_relative_to(previews.resolve())
    assert migrated_path.read_bytes() == b"%PDF-1.7 legacy"
    assert not old.path.exists()
    assert new_state.migrate_legacy_previews(legacy_static) == []


def test_legacy_preview_symlink_fails_closed_without_deleting_static_content(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    legacy_static = tmp_path / "legacy-static"
    old_artifacts = ArtifactStore(work, legacy_static)
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=old_artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "legacy")
    assert lease is not None
    old = old_artifacts.protect(job_id, "preview_pdf", b"legacy")
    state.register_protected_artifact(job_id, "preview_pdf", old, lease.token)
    target = tmp_path / "outside"
    target.mkdir()
    old.path.parent.rename(tmp_path / "moved-job")
    old.path.parent.symlink_to(target, target_is_directory=True)

    new_state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=ArtifactStore(work, tmp_path / "previews"),
    )
    with pytest.raises(ValueError, match="legacy preview"):
        new_state.migrate_legacy_previews(legacy_static)


def test_unregistered_legacy_preview_shaped_orphan_fails_closed(
    tmp_path: Path,
) -> None:
    legacy_static = tmp_path / "legacy-static"
    orphan_directory = legacy_static / "123e4567-e89b-12d3-a456-426614174000"
    orphan_directory.mkdir(parents=True)
    orphan = orphan_directory / ("preview_pdf-" + "a" * 32 + ".artifact")
    orphan.write_bytes(b"orphan preview")
    state = PipelineState(
        tmp_path / "queue.sqlite3",
        artifact_store=ArtifactStore(tmp_path / "work", tmp_path / "previews"),
    )

    with pytest.raises(ValueError, match="legacy preview"):
        state.migrate_legacy_previews(legacy_static)

    assert orphan.read_bytes() == b"orphan preview"


def test_legacy_preview_migration_serializes_concurrent_starters(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    legacy_static = tmp_path / "legacy-static"
    queue = tmp_path / "queue.sqlite3"
    old_artifacts = ArtifactStore(work, legacy_static)
    old_state = PipelineState(queue, artifact_store=old_artifacts)
    job_id = old_state.create_job()
    lease = old_state.acquire_lease(job_id, "legacy")
    assert lease is not None
    old = old_artifacts.protect(job_id, "preview_pdf", b"legacy")
    old_state.register_protected_artifact(job_id, "preview_pdf", old, lease.token)
    private = tmp_path / "previews"

    def migrate() -> list[str]:
        state = PipelineState(queue, artifact_store=ArtifactStore(work, private))
        return state.migrate_legacy_previews(legacy_static)

    first_result: list[list[str]] = []
    second_result: list[list[str]] = []
    first_thread = Thread(target=lambda: first_result.append(migrate()))
    second_thread = Thread(target=lambda: second_result.append(migrate()))
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert sorted(first_result + second_result) == [[], [job_id]]
    assert any(path.is_file() for path in private.rglob("preview_pdf-*.artifact"))


def test_legacy_published_warning_without_manifest_is_not_requeued(
    tmp_path: Path,
) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", publication_cache_root=tmp_path / "formal")
    job_id = state.create_job()
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    state.admin_transition(job_id, "publish_queued")
    state.admin_transition(job_id, "publishing")
    state.admin_transition(job_id, "indexing")
    state.admin_transition(job_id, "published_with_warning")
    before = state.get_job(job_id)

    with pytest.raises(ValueError, match="durable publication metadata"):
        state.retry_indexing(job_id, int(before["revision"]))

    after = state.get_job(job_id)
    assert after["status"] == "published_with_warning"
    assert int(after["revision"]) == int(before["revision"])


def test_valid_transitions_and_invalid_transition_are_enforced(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()

    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    assert state.get_job(job_id)["status"] == "running"
    assert state.transition(job_id, "review_ready", lease.token) == "review_ready"
    with pytest.raises(ValueError):
        state.admin_transition(job_id, "queued")


def test_worker_lease_is_cleared_on_transition_and_admin_transition_is_explicit(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.transition(job_id, "review_ready", lease.token)
    with pytest.raises(LeaseError):
        state.set_digests(job_id, preview_digest="stale", lease_token=lease.token)
    assert state.admin_transition(job_id, "rejected") == "rejected"


def test_cleanup_limit_bounds_expired_terminal_artifact_batch(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "previews")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    jobs: list[str] = []
    for label in ("first", "second"):
        job_id = state.create_job()
        lease = state.acquire_lease(job_id, "cleanup-worker")
        assert lease is not None
        pending = artifacts.begin(job_id, "ocr")
        pending.write(label.encode("ascii"))
        pending.promote()
        state.transition(job_id, "review_ready", lease.token)
        state.admin_transition(job_id, "rejected")
        jobs.append(job_id)

    removed = state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        limit=1,
    )

    assert len(removed) == 1
    remaining = jobs[1] if removed[0] == jobs[0] else jobs[0]
    assert artifacts.resolve(remaining, "ocr") is not None


def test_cleanup_limit_makes_progress_when_oldest_private_directory_is_absent(
    tmp_path: Path,
) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "previews")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    first = state.create_job()
    state.admin_transition(first, "rejected")
    second = state.create_job()
    lease = state.acquire_lease(second, "cleanup-worker")
    assert lease is not None
    pending = artifacts.begin(second, "ocr")
    pending.write(b"second")
    pending.promote()
    state.transition(second, "review_ready", lease.token)
    state.admin_transition(second, "rejected")

    processed: list[str] = []
    for _ in range(3):
        processed.extend(
            state.cleanup_expired_artifacts(
                now=datetime(2030, 1, 1, tzinfo=timezone.utc),
                limit=1,
            )
        )

    assert set(processed) == {first, second}
    assert state.get_job(first)["status"] == "rejected"
    assert state.get_job(second)["status"] == "rejected"
    assert artifacts.resolve(second, "ocr") is None
    assert state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), limit=1
    ) == []


def test_cleanup_poisoned_oldest_job_does_not_starve_later_terminal_jobs(
    tmp_path: Path,
) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "previews")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    poisoned = state.create_job()
    state.admin_transition(poisoned, "rejected")
    poisoned_directory = artifacts.work_dir / poisoned
    poisoned_directory.symlink_to(tmp_path / "outside", target_is_directory=True)
    later = state.create_job()
    state.admin_transition(later, "rejected")

    first = state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), limit=1
    )
    second = state.cleanup_expired_artifacts(
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), limit=1
    )

    assert first == []
    assert second == [later]
    assert state.get_job(poisoned)["cleanup_completed_at"] is None
    assert state.get_job(poisoned)["cleanup_error"]
    assert state.get_job(later)["cleanup_completed_at"] is not None


def test_formal_gc_cursor_persists_across_state_restart(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    PipelineState(database).set_formal_gc_cursor("pdf/" + "a" * 64 + ".pdf")

    restarted = PipelineState(database)

    assert restarted.get_formal_gc_cursor() == "pdf/" + "a" * 64 + ".pdf"
    restarted.set_formal_gc_cursor(None)
    assert restarted.get_formal_gc_cursor() is None


def test_generic_worker_transition_rejects_missing_lease_token(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()
    with pytest.raises(LeaseError):
        state.transition(job_id, "running", None)


def test_only_one_worker_acquires_lease_and_stale_token_cannot_mutate(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", lease_seconds=60)
    job_id = state.create_job()
    first = state.acquire_lease(job_id, "worker-a")
    assert first is not None
    assert state.acquire_lease(job_id, "worker-b") is None
    with pytest.raises(LeaseError):
        state.transition(job_id, "failed", lease_token="stale")


def test_expired_lease_is_recoverable_and_old_token_stays_invalid(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", lease_seconds=1)
    job_id = state.create_job()
    old = state.acquire_lease(job_id, "worker-a", now=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert old is not None
    recovered = state.recover_expired(now=datetime(2020, 1, 1, 0, 0, 2, tzinfo=timezone.utc))
    assert job_id in recovered
    new = state.acquire_lease(job_id, "worker-b", now=datetime(2020, 1, 1, 0, 0, 2, tzinfo=timezone.utc))
    assert new is not None and new.token != old.token
    with pytest.raises(LeaseError):
        state.heartbeat(job_id, old.token)
    with pytest.raises(LeaseError):
        state.transition(job_id, "failed", old.token)


def test_expired_running_takeover_invalidates_batch_match_and_old_summary(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", lease_seconds=60, artifact_store=artifacts)
    batch = state.create_batch()
    first, second = state.create_job(batch), state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "first-ref", "title": "First"}])
    now = datetime.now(timezone.utc)
    first_lease = state.acquire_lease(first, "first-worker", now=now)
    second_lease = state.acquire_lease(second, "second-worker", now=now)
    assert first_lease is not None and second_lease is not None
    state.record_job_summary(first, {"paper_title": "First"}, first_lease.token)
    state.record_job_summary(second, {"paper_title": "Second"}, second_lease.token)
    snapshot = state.get_batch_matching_snapshot(batch)
    state.store_batch_match_result(
        batch,
        first,
        first_lease.token,
        expected_revision=snapshot["revision"],
        result={"matches": [], "needs_attention": [], "unmatched_entries": []},
    )

    takeover = state.acquire_lease(second, "takeover", now=now + timedelta(seconds=120))

    assert takeover is not None
    current = state.get_batch_matching_snapshot(batch)
    assert current["result"] is None
    assert second not in {item["job_id"] for item in current["summaries"]}


def test_model_change_invalidates_selected_step_and_downstream(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job(selected_models={"ocr": "one", "extract": "one", "translate": "one"})
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    for step in ("ocr", "extract", "translate"):
        pending = artifacts.begin(job_id, step)
        pending.write(step.encode())
        state.record_step_success(job_id, step, lease.token, artifact=pending.promote())
    state.change_model(job_id, "extract", "two", lease.token)

    assert state.step_artifact(job_id, "ocr") is not None
    assert state.step_artifact(job_id, "extract") is None
    assert state.step_artifact(job_id, "translate") is None
    assert state.get_job(job_id)["selected_models"] == {"ocr": "one", "extract": "two", "translate": "one"}


def test_resume_from_matching_step_invalidates_old_summary(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    batch = state.create_batch()
    job_id = state.create_job(batch)
    state.persist_bibtex_entries(batch, [{"key": "ref", "title": "Paper"}])
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.record_job_summary(job_id, {"paper_title": "Paper"}, lease.token)

    assert state.resume_step(job_id, lease.token) == "ocr"

    snapshot = state.get_batch_matching_snapshot(batch)
    assert snapshot["ready"] is False
    assert snapshot["summaries"] == []


def test_cancel_is_immediate_when_queued_and_observed_at_step_boundary(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    queued = state.create_job()
    state.request_cancel(queued)
    assert state.get_job(queued)["status"] == "cancelled"

    active = state.create_job()
    lease = state.acquire_lease(active, "worker")
    assert lease is not None
    state.request_cancel(active)
    assert state.get_job(active)["status"] == "running"
    assert state.cancel_requested(active) is True
    assert state.step_boundary(active, lease.token) == "cancelled"


def test_retry_starts_at_earliest_missing_step(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    pending.write(b"a")
    state.record_step_success(job_id, "ocr", lease.token, artifact=pending.promote())
    assert state.next_step(job_id) == "extract"


def test_step_success_exposes_digest_size_and_clears_on_invalidation(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    pending.write(b"artifact")
    artifact = pending.promote()
    state.record_step_success(job_id, "ocr", lease.token, artifact=artifact)
    assert state.artifact_metadata(job_id, "ocr") == {
        "digest": artifact.digest, "size": 8, "path": str(artifact.path)
    }
    state.change_model(job_id, "ocr", "new", lease.token)
    assert state.artifact_metadata(job_id, "ocr") is None


def test_unpromoted_or_mismatched_artifact_cannot_be_recorded(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    with pytest.raises(ValueError):
        state.record_step_success(job_id, "ocr", lease.token, artifact=pending)
    pending.write(b"good")
    artifact = pending.promote()
    with pytest.raises(ValueError):
        state.record_step_success(job_id, "ocr", lease.token, artifact=artifact.__class__(job_id, "ocr", artifact.path, "bad", artifact.size))


def test_step_success_cas_rejects_cancelled_job_without_metadata(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    pending.write(b"cancelled")
    artifact = pending.promote()
    state.request_cancel(job_id)

    committed = state.record_step_success_if_active(job_id, "ocr", lease.token, artifact=artifact)

    assert committed is False
    assert state.step_artifact(job_id, "ocr") is None
    assert state.artifact_metadata(job_id, "ocr") is None


def test_step_success_rejects_cross_job_wrong_kind_and_external_artifacts(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    source_job = state.create_job()
    target_job = state.create_job()
    source_lease = state.acquire_lease(source_job, "source")
    target_lease = state.acquire_lease(target_job, "target")
    assert source_lease is not None and target_lease is not None
    pending = artifacts.begin(source_job, "ocr")
    pending.write(b"source")
    source_artifact = pending.promote()
    with pytest.raises(ValueError):
        state.record_step_success(target_job, "ocr", target_lease.token, artifact=source_artifact)
    pending = artifacts.begin(target_job, "extract")
    pending.write(b"wrong kind")
    wrong_kind = pending.promote()
    with pytest.raises(ValueError):
        state.record_step_success(target_job, "ocr", target_lease.token, artifact=wrong_kind)
    external = tmp_path / "external.artifact"
    external.write_bytes(b"outside")
    forged = source_artifact.__class__(target_job, "ocr", external, source_artifact.digest, external.stat().st_size)
    with pytest.raises(ValueError):
        state.record_step_success(target_job, "ocr", target_lease.token, artifact=forged)


def test_step_success_rejects_fully_populated_external_forgery(tmp_path: Path) -> None:
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    pending.write(b"legitimate")
    legitimate = pending.promote()
    assert legitimate.job_directory is not None
    external_root = tmp_path / "external"
    external_job = external_root / legitimate.job_directory.name
    external_job.mkdir(parents=True)
    external_path = external_job / legitimate.path.name
    external_path.write_bytes(b"forged")
    forged = Artifact(
        job_id,
        "ocr",
        external_path,
        hashlib.sha256(b"forged").hexdigest(),
        6,
        external_root,
        external_job,
    )
    with pytest.raises(ValueError):
        state.record_step_success(job_id, "ocr", lease.token, artifact=forged)


def test_attempt_history_and_atomic_job_initialization(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    batch = state.create_batch()
    job_id = state.create_job(batch, selected_models={"ocr": "one"})
    assert state.list_attempts(job_id) == []
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.record_step_attempt(job_id, "ocr", lease.token, "failed", error="OCR failed")
    attempts = state.list_attempts(job_id, "ocr")
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error"] == "OCR failed"


def test_lease_guard_blocks_recovery_until_publication_boundary_finishes(
    tmp_path: Path,
) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3", lease_seconds=60)
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "publisher")
    assert lease is not None
    state.transition(job_id, "review_ready", lease.token)
    state.admin_transition(job_id, "publish_queued")
    lease = state.acquire_lease(job_id, "publisher-2")
    assert lease is not None
    state.transition(job_id, "publishing", lease.token)

    finished = Event()
    recovered: list[str] = []

    def recover() -> None:
        recovered.extend(
            state.recover_expired(now=datetime.now(timezone.utc) + timedelta(days=1))
        )
        finished.set()

    with state.lease_guard(job_id, lease.token):
        thread = Thread(target=recover, daemon=True)
        thread.start()
        assert not finished.wait(timeout=0.2)

    thread.join(timeout=3)
    assert finished.is_set()
    assert recovered == [job_id]
