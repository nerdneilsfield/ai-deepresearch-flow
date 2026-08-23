from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deepresearch_flow.pipeline.state import LeaseError, PipelineState


def test_valid_transitions_and_invalid_transition_are_enforced(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()

    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    assert state.get_job(job_id)["status"] == "running"
    assert state.transition(job_id, "review_ready", lease.token) == "review_ready"
    with pytest.raises(ValueError):
        state.transition(job_id, "queued")


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
    job_id = state.create_job(selected_models={"ocr": "one", "extract": "one", "translate": "one"})
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.record_step_success(job_id, "ocr", lease.token, "a", 1)
    state.record_step_success(job_id, "extract", lease.token, "b", 1)
    state.record_step_success(job_id, "translate", lease.token, "c", 1)
    state.change_model(job_id, "extract", "two", lease.token)

    assert state.step_artifact(job_id, "ocr") is not None
    assert state.step_artifact(job_id, "extract") is None
    assert state.step_artifact(job_id, "translate") is None


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
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.record_step_success(job_id, "ocr", lease.token, "a", 1)
    assert state.next_step(job_id) == "extract"


def test_step_success_exposes_digest_size_and_clears_on_invalidation(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "queue.sqlite3")
    job_id = state.create_job()
    lease = state.acquire_lease(job_id, "worker")
    assert lease is not None
    state.record_step_success(job_id, "ocr", lease.token, "digest", 12, path="work/ocr")
    assert state.artifact_metadata(job_id, "ocr") == {
        "digest": "digest", "size": 12, "path": "work/ocr"
    }
    state.change_model(job_id, "ocr", "new", lease.token)
    assert state.artifact_metadata(job_id, "ocr") is None
