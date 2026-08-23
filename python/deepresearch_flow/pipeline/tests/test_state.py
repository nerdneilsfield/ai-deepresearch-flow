from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.state import LeaseError, PipelineState


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


def test_model_change_invalidates_selected_step_and_downstream(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
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
    state = PipelineState(tmp_path / "queue.sqlite3")
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    pending = artifacts.begin(job_id, "ocr")
    pending.write(b"a")
    state.record_step_success(job_id, "ocr", lease.token, artifact=pending.promote())
    assert state.next_step(job_id) == "extract"


def test_step_success_exposes_digest_size_and_clears_on_invalidation(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
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
    state = PipelineState(tmp_path / "queue.sqlite3")
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
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


def test_step_success_rejects_cross_job_wrong_kind_and_external_artifacts(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
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
