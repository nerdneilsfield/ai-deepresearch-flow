"""Black-box tests for the restricted Agent submission API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from deepresearch_flow.pipeline.api import create_pipeline_agent_app
from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import ModelAllowlist, PipelineConfig
from deepresearch_flow.pipeline.state import PipelineState


TOKEN = "agent-token"


def _make_app(tmp_path: Path):
    config = PipelineConfig(
        enabled=True,
        work_dir=str(tmp_path / "work"),
        preview_root=str(tmp_path / "previews"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        static_root=str(tmp_path / "formal"),
        pdfs_per_batch=2,
        max_pdf_bytes=128,
        max_batch_bytes=256,
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(config.work_dir, config.preview_root)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    return create_pipeline_agent_app(
        config=config, state=state, artifacts=artifacts, agent_token=TOKEN
    )


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _headers(key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {TOKEN}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def _upload(app, key: str, content: bytes = b"%PDF-1.7 paper") -> httpx.Response:
    return _request(
        app,
        "POST",
        "/submissions",
        headers=_headers(key),
        files={"pdfs": ("paper.pdf", content, "application/pdf")},
    )


def test_submission_is_async_and_idempotent(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    first = _upload(app, "request-1")
    assert first.status_code == 202
    submission_id = first.json()["submission"]["id"]

    replay = _upload(app, "request-1")
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["submission"]["id"] == submission_id

    conflict = _upload(app, "request-1", b"%PDF-1.7 different")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_agent_can_read_and_control_only_owned_job(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    created = _upload(app, "request-2")
    job_id = created.json()["submission"]["jobs"][0]

    status = _request(app, "GET", f"/jobs/{job_id}", headers=_headers())
    assert status.status_code == 200
    assert status.json()["job"]["status"] == "queued"

    cancel = _request(app, "POST", f"/jobs/{job_id}/cancel", headers=_headers())
    assert cancel.status_code == 200
    assert cancel.json()["job"]["status"] == "cancelled"

    publish = _request(app, "POST", f"/jobs/{job_id}/publish", headers=_headers())
    assert publish.status_code == 404


def test_agent_retry_rejects_model_override_and_non_retryable_job(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    created = _upload(app, "request-3")
    job_id = created.json()["submission"]["jobs"][0]

    body = {"ocr": "other-model"}
    response = _request(
        app,
        "POST",
        f"/jobs/{job_id}/retry",
        headers={**_headers(), "Content-Type": "application/json"},
        json=body,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_body"


def test_agent_principals_cannot_read_each_others_jobs(tmp_path: Path) -> None:
    config = PipelineConfig(
        enabled=True,
        work_dir=str(tmp_path / "work"),
        preview_root=str(tmp_path / "previews"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        static_root=str(tmp_path / "formal"),
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(config.work_dir, config.preview_root)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    app = create_pipeline_agent_app(
        config=config,
        state=state,
        artifacts=artifacts,
        agent_token={"hermes": "hermes-token", "grok": "grok-token"},
    )
    created = _request(
        app,
        "POST",
        "/submissions",
        headers={"Authorization": "Bearer hermes-token", "Idempotency-Key": "owned"},
        files={"pdfs": ("paper.pdf", b"%PDF-1.7 paper", "application/pdf")},
    )
    job_id = created.json()["submission"]["jobs"][0]
    other = _request(
        app,
        "GET",
        f"/jobs/{job_id}",
        headers={"Authorization": "Bearer grok-token"},
    )
    assert other.status_code == 404


def test_agent_submission_records_audit_event(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    response = _upload(app, "audit-request")
    assert response.status_code == 202

    with app.state.pipeline_state._connect() as db:
        event = db.execute(
            "SELECT event_type,principal_id,result_code FROM agent_audit_events"
        ).fetchone()
    assert event is not None
    assert tuple(event) == ("submission.created", "static-agent", "accepted")


def test_agent_cannot_cancel_publication_domain_job(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    created = _upload(app, "publish-boundary")
    job_id = created.json()["submission"]["jobs"][0]
    state = app.state.pipeline_state
    state.admin_transition(job_id, "running")
    state.admin_transition(job_id, "review_ready")
    state.queue_publication(job_id, state.get_job_details(job_id)["revision"])
    response = _request(app, "POST", f"/jobs/{job_id}/cancel", headers=_headers())
    assert response.status_code == 409


def test_agent_admission_rejects_second_inflight_submission(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    app.state.pipeline_config = app.state.pipeline_config.__class__(
        **{**app.state.pipeline_config.__dict__, "agent_max_inflight_jobs": 1}
    )
    assert _upload(app, "admission-1").status_code == 202
    response = _upload(app, "admission-2")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "agent_inflight_limit"


def test_worker_cannot_claim_pending_agent_batch(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    state = app.state.pipeline_state
    submission_id, *_ = state.reserve_agent_submission(
        "static-agent", "pending-worker", "params", "request", request_bytes=10
    )
    batch_id = state.create_agent_batch(submission_id)
    job_id = state.create_job(batch_id)
    assert state.acquire_lease(job_id, "worker") is None


def test_agent_admission_counts_all_jobs_in_multi_pdf_submission(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    config = app.state.pipeline_config
    app.state.pipeline_config = config.__class__(**{**config.__dict__, "agent_max_inflight_jobs": 1})
    response = _request(
        app,
        "POST",
        "/submissions",
        headers=_headers("multi"),
        files=[
            ("pdfs", ("one.pdf", b"%PDF-1.7 one", "application/pdf")),
            ("pdfs", ("two.pdf", b"%PDF-1.7 two", "application/pdf")),
        ],
    )
    assert response.status_code == 429
