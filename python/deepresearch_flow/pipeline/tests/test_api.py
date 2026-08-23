"""Black-box HTTP tests for the authenticated pipeline API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from deepresearch_flow.pipeline.api import create_pipeline_admin_app
from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import ModelAllowlist, PipelineConfig
from deepresearch_flow.pipeline.state import PipelineState
from deepresearch_flow.paper.snapshot.api import create_app as create_snapshot_app


TOKEN = "admin-token"


def _make_app(tmp_path: Path, *, enabled: bool = True):
    config = PipelineConfig(
        enabled=enabled,
        work_dir=str(tmp_path / "work"),
        static_root=str(tmp_path / "formal"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        pdfs_per_batch=3,
        max_pdf_bytes=64,
        max_batch_bytes=128,
        bibtex_max_bytes=512,
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(config.work_dir, config.static_root)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    app = create_pipeline_admin_app(
        config=config, state=state, artifacts=artifacts, admin_token=TOKEN
    )
    return app, state, artifacts


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _upload(app, *, files, data=None):
    return _request(
        app,
        "POST",
        "/batches",
        files=files,
        data=data or {},
        headers=_headers(),
    )


def _review_ready(state: PipelineState, job_id: str) -> dict[str, object]:
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "review_ready", lease.token)
    return state.get_job(job_id)


def test_config_and_auth_are_protected_and_redacted(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path)

    assert _request(app, "GET", "/config").status_code == 401
    response = _request(app, "GET", "/config", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401

    response = _request(app, "GET", "/config", headers=_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["models"]["ocr"]["allowlist"] == ["ocr-a"]
    assert payload["worker"]["status"] == "offline"
    assert "work_dir" not in payload
    assert "queue_db" not in payload


def test_multipart_upload_returns_public_batch_and_jobs(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs[]", ("first.pdf", b"%PDF-1.7 first", "application/pdf")),
            ("pdfs[]", ("second.pdf", b"%PDF-1.7 second", "application/pdf")),
            ("bibtex", ("papers.bib", b"@article{first, title={First}}", "text/plain")),
        ],
        data={"ocr_model": "ocr-a", "extract_model": "extract-a", "translate_model": "translate-a"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["job_ids"]) == 2
    assert payload["bibtex"]["status"] == "provided"
    assert payload["batch"]["jobs"][0]["filename"] == "first.pdf"
    assert "work" not in response.text
    assert "queue.sqlite" not in response.text

    listed = _request(app, "GET", "/batches?page=1&page_size=1", headers=_headers())
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["has_more"] is False


def test_upload_validation_and_model_allowlist_are_http_errors(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(tmp_path)
    bad = _upload(app, files=[("pdfs", ("bad.pdf", b"not-pdf", "application/pdf"))])
    assert bad.status_code == 400
    assert bad.json()["error"] == "bad_request"
    assert state.list_batches() == []

    duplicate = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 same", "application/pdf")),
            ("pdfs", ("b.pdf", b"%PDF-1.7 same", "application/pdf")),
        ],
    )
    assert duplicate.status_code == 400
    assert state.list_batches() == []

    invalid_model = _upload(
        app,
        files=[("pdfs", ("a.pdf", b"%PDF-1.7 same", "application/pdf"))],
        data={"ocr_model": "unlisted"},
    )
    assert invalid_model.status_code == 400
    assert state.list_batches() == []


def test_publish_requires_current_revision_and_batch_is_partial(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("pdfs", ("b.pdf", b"%PDF-1.7 b", "application/pdf")),
        ],
    )
    batch_id = response.json()["batch_id"]
    first, second = response.json()["job_ids"]
    first_job = _review_ready(state, first)
    first_revision = first_job["revision"]
    assert isinstance(first_revision, int)

    stale = _request(
        app,
        "POST",
        f"/jobs/{first}/publish",
        json={"expected_revision": first_revision - 1},
        headers=_headers(),
    )
    assert stale.status_code == 409

    ready = _request(
        app,
        "POST",
        f"/batches/{batch_id}/publish-ready",
        json={
            "items": [
                {"job_id": first, "expected_revision": first_revision},
                {"job_id": second, "expected_revision": 0},
                {"job_id": "outside", "expected_revision": 0},
            ]
        },
        headers=_headers(),
    )
    assert ready.status_code == 200
    outcomes = ready.json()["outcomes"]
    assert outcomes[0]["status"] == "queued"
    assert outcomes[1]["status"] == "conflict"
    assert outcomes[2]["status"] == "not_found"


def test_job_actions_and_manual_bibtex_binding_are_authenticated(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("bibtex", ("a.bib", b"@article{key, title={A}}", "text/plain")),
        ],
    )
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "needs_attention", lease.token)

    binding = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert binding.status_code == 200
    assert binding.json()["binding"]["entry_key"] == "key"

    retry = _request(app, "POST", f"/jobs/{job_id}/retry", json={}, headers=_headers())
    assert retry.status_code == 200
    assert retry.json()["job"]["status"] == "queued"

    cancel = _request(app, "POST", f"/jobs/{job_id}/cancel", headers=_headers())
    assert cancel.status_code == 200
    assert cancel.json()["job"]["status"] == "cancelled"


def test_artifact_response_is_allowlisted_and_containment_safe(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf"))],
    )
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    protected = artifacts.protect(job_id, "preview_pdf", b"%PDF-1.7 preview")
    state.register_protected_artifact(job_id, "preview_pdf", protected, lease.token)
    state.transition(job_id, "review_ready", lease.token)

    artifact = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf", headers=_headers())
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("application/pdf")
    assert artifact.headers["content-disposition"] == 'inline; filename="paper.pdf"'
    assert artifact.content.startswith(b"%PDF-")

    unknown = _request(app, "GET", f"/jobs/{job_id}/artifacts/secret", headers=_headers())
    assert unknown.status_code == 404
    denied = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf")
    assert denied.status_code == 401


def test_disabled_factory_has_no_routes(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path, enabled=False)
    response = _request(app, "GET", "/config", headers=_headers())
    assert response.status_code == 404


def test_snapshot_app_mounts_pipeline_only_when_enabled(tmp_path: Path) -> None:
    config = PipelineConfig(
        enabled=True,
        work_dir=str(tmp_path / "work"),
        static_root=str(tmp_path / "formal"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(config.work_dir, config.static_root)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    app = create_snapshot_app(
        snapshot_db=tmp_path / "snapshot.sqlite3",
        static_base_url="",
        mcp_access_token="mcp-token",
        admin_token=TOKEN,
        pipeline_config=config,
        pipeline_state=state,
        pipeline_artifacts=artifacts,
    )
    response = _request(app, "GET", "/api/v1/admin/pipeline/config", headers=_headers())
    assert response.status_code == 200

    disabled = create_snapshot_app(
        snapshot_db=tmp_path / "snapshot-disabled.sqlite3",
        static_base_url="",
        mcp_access_token="mcp-token",
        admin_token=TOKEN,
        pipeline_config=PipelineConfig(enabled=False),
    )
    response = _request(
        disabled, "GET", "/api/v1/admin/pipeline/config", headers=_headers()
    )
    assert response.status_code == 404
