"""Black-box HTTP tests for the authenticated pipeline API."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

import httpx

from deepresearch_flow.pipeline.api import create_pipeline_admin_app
from deepresearch_flow.pipeline.artifacts import Artifact, ArtifactStore
from deepresearch_flow.pipeline.config import ModelAllowlist, PipelineConfig
from deepresearch_flow.pipeline.steps import PreviewArtifacts
from deepresearch_flow.pipeline.state import PipelineState
from deepresearch_flow.paper.snapshot.api import create_app as create_snapshot_app


TOKEN = "admin-token"


def _make_app(
    tmp_path: Path,
    *,
    enabled: bool = True,
    worker_status_provider=None,
    preview_regenerator=None,
    max_pdf_bytes: int = 64,
    max_batch_bytes: int = 128,
    pdfs_per_batch: int = 3,
    bibtex_max_bytes: int = 512,
):
    config = PipelineConfig(
        enabled=enabled,
        work_dir=str(tmp_path / "work"),
        static_root=str(tmp_path / "formal"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        pdfs_per_batch=pdfs_per_batch,
        max_pdf_bytes=max_pdf_bytes,
        max_batch_bytes=max_batch_bytes,
        bibtex_max_bytes=bibtex_max_bytes,
        ocr=ModelAllowlist(("ocr-a",), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
    )
    artifacts = ArtifactStore(config.work_dir, config.static_root)
    state = PipelineState(config.queue_db, artifact_store=artifacts)
    app = create_pipeline_admin_app(
        config=config,
        state=state,
        artifacts=artifacts,
        admin_token=TOKEN,
        worker_status_provider=worker_status_provider,
        preview_regenerator=preview_regenerator,
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


def _valid_preview(
    artifacts: ArtifactStore, job_id: str, *, label: bytes = b"regenerated"
) -> PreviewArtifacts:
    names = (
        "preview_pdf",
        "preview_source_md",
        "preview_summary_json",
        "preview_translated_md",
    )
    contents = (
        b"%PDF-1.7 " + label,
        b"# " + label + b" source",
        b'{"summary":"' + label + b'"}',
        b"# " + label + b" translation",
    )
    protected = tuple(
        artifacts.protect(job_id, name, content)
        for name, content in zip(names, contents, strict=True)
    )
    digest = hashlib.sha256(
        b"".join(item.digest.encode("ascii") for item in protected)
    ).hexdigest()
    return PreviewArtifacts(
        protected[0].path,
        protected[1].path,
        protected[2].path,
        protected[3].path,
        digest,
        "manual",
        protected,
    )


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
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_upload"
    assert state.list_batches() == []

    duplicate = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 same", "application/pdf")),
            ("pdfs", ("b.pdf", b"%PDF-1.7 same", "application/pdf")),
        ],
    )
    assert duplicate.status_code == 422
    assert state.list_batches() == []

    invalid_model = _upload(
        app,
        files=[("pdfs", ("a.pdf", b"%PDF-1.7 same", "application/pdf"))],
        data={"ocr_model": "unlisted"},
    )
    assert invalid_model.status_code == 422
    assert invalid_model.json()["error"]["code"] == "invalid_model"
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
    app, state, artifacts = _make_app(tmp_path)
    app.state.preview_regenerator = lambda job_id: _valid_preview(artifacts, job_id)
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


def test_job_and_batch_details_expose_only_safe_bibtex_candidates(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            (
                "bibtex",
                (
                    "papers.bib",
                    b"@article{safe, title={Safe title}, author={Doe, Jane}, doi={10.1/abc}, api_key={never-show}, url={file:///secret/path}}",
                    "text/plain",
                ),
            ),
        ],
    )
    assert response.status_code == 200
    batch_id = response.json()["batch_id"]
    job_id = response.json()["job_ids"][0]

    job = _request(app, "GET", f"/jobs/{job_id}", headers=_headers())
    assert job.status_code == 200
    candidates = job.json()["job"]["bibtex"]["candidates"]
    assert candidates[0]["key"] == "safe"
    assert candidates[0]["title"] == "Safe title"
    assert candidates[0]["doi"] == "10.1/abc"
    assert candidates[0]["author"] == "Doe, Jane"
    assert "api_key" not in candidates[0]
    assert "url" not in candidates[0]
    assert "/secret" not in job.text

    batch = _request(app, "GET", f"/batches/{batch_id}", headers=_headers())
    assert batch.status_code == 200
    assert batch.json()["batch"]["jobs"][0]["bibtex"]["candidates"][0]["key"] == "safe"


def test_returned_bibtex_candidate_key_round_trips_through_manual_binding(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("bibtex", ("a.bib", b"@article{round-trip, title={A}}", "text/plain")),
        ],
    )
    assert response.status_code == 200
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "needs_attention", lease.token)
    app.state.preview_regenerator = lambda current_job: _valid_preview(artifacts, current_job)

    detail = _request(app, "GET", f"/jobs/{job_id}", headers=_headers())
    assert detail.status_code == 200
    candidate_key = detail.json()["job"]["bibtex"]["candidates"][0]["key"]
    binding = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": candidate_key},
        headers=_headers(),
    )
    assert binding.status_code == 200
    assert binding.json()["binding"]["entry_key"] == candidate_key


def test_bibtex_key_that_requires_unsafe_normalization_is_rejected(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("bibtex", ("unsafe.bib", b"@article{control\x7fkey, title={A}}", "text/plain")),
        ],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_upload"
    assert state.list_batches() == []


def test_manual_binding_requires_regenerator_and_invalidates_stale_preview(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
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
    old_preview = artifacts.protect(job_id, "preview_pdf", b"%PDF-1.7 old")
    state.register_protected_artifact(job_id, "preview_pdf", old_preview, lease.token)
    state.transition(job_id, "needs_attention", lease.token)

    unavailable = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "preview_regeneration_unavailable"
    assert unavailable.json()["job"]["status"] == "needs_attention"
    assert unavailable.json()["job"]["bibtex"]["entry_key"] is None

    artifact = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf", headers=_headers())
    assert artifact.status_code == 200


def test_failed_preview_regeneration_is_recoverable_and_repeatable(tmp_path: Path) -> None:
    calls: list[str] = []

    def fail(job_id: str) -> bool:
        calls.append(job_id)
        raise RuntimeError("preview provider failed")

    app, state, artifacts = _make_app(tmp_path, preview_regenerator=fail)
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
    old_preview = artifacts.protect(job_id, "preview_pdf", b"%PDF-1.7 old")
    state.register_protected_artifact(job_id, "preview_pdf", old_preview, lease.token)
    state.transition(job_id, "needs_attention", lease.token)

    failed = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "preview_regeneration_failed"
    assert failed.json()["job"]["status"] == "needs_attention"
    assert failed.json()["job"]["bibtex"]["entry_key"] == "key"
    assert failed.json()["job"]["preview_digest"] is None
    stale = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf", headers=_headers())
    assert stale.status_code == 404

    def regenerate(current_job: str) -> PreviewArtifacts:
        calls.append(current_job)
        return _valid_preview(artifacts, current_job)

    app.state.preview_regenerator = regenerate
    retried = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert retried.status_code == 200
    assert retried.json()["job"]["status"] == "review_ready"
    assert retried.json()["job"]["bibtex"]["entry_key"] == "key"
    assert calls == [job_id, job_id]


def test_preview_regenerator_requires_four_integrity_checked_artifacts(tmp_path: Path) -> None:
    invalid_results = (None, True, {"preview_pdf": "incomplete"})
    for index, invalid in enumerate(invalid_results):
        case_dir = tmp_path / f"invalid-{index}"
        app, state, artifacts = _make_app(case_dir, preview_regenerator=lambda _job_id, value=invalid: value)
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

        failed = _request(
            app,
            "PUT",
            f"/jobs/{job_id}/bibtex-match",
            json={"entry_key": "key"},
            headers=_headers(),
        )
        assert failed.status_code == 409
        assert failed.json()["error"]["code"] == "preview_regeneration_failed"
        assert failed.json()["job"]["status"] == "needs_attention"
        assert failed.json()["job"]["preview_digest"] is None

    wrong_root_dir = tmp_path / "wrong-root"
    app, state, artifacts = _make_app(wrong_root_dir)
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
    valid = _valid_preview(artifacts, job_id)
    outside = tmp_path / "outside-preview.artifact"
    outside.write_bytes(b"%PDF-1.7 outside")
    wrong_pdf = Artifact(
        job_id,
        "preview_pdf",
        outside,
        hashlib.sha256(outside.read_bytes()).hexdigest(),
        outside.stat().st_size,
        artifacts.formal_root,
        outside.parent,
    )
    wrong = PreviewArtifacts(
        wrong_pdf.path,
        valid.source_markdown,
        valid.summary_json,
        valid.translated_markdown,
        valid.digest,
        valid.bibtex_status,
        (wrong_pdf, *valid.protected[1:]),
    )
    app.state.preview_regenerator = lambda _job_id: wrong
    failed = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "preview_regeneration_failed"

    tampered_dir = tmp_path / "tampered"
    app, state, artifacts = _make_app(tampered_dir)
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
    tampered = _valid_preview(artifacts, job_id)
    tampered.protected[0].path.write_bytes(b"tampered")
    app.state.preview_regenerator = lambda _job_id: tampered
    failed = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "key"},
        headers=_headers(),
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "preview_regeneration_failed"


def test_valid_preview_regenerator_registers_all_artifacts_and_enables_publish(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    app.state.preview_regenerator = lambda job_id: _valid_preview(artifacts, job_id)
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
    job = binding.json()["job"]
    assert job["status"] == "review_ready"
    assert isinstance(job["preview_digest"], str)
    assert len(job["preview_digest"]) == 64
    for kind in ("pdf", "source_markdown", "summary_json", "translated_markdown"):
        artifact = _request(app, "GET", f"/jobs/{job_id}/artifacts/{kind}", headers=_headers())
        assert artifact.status_code == 200

    published = _request(
        app,
        "POST",
        f"/jobs/{job_id}/publish",
        json={"expected_revision": job["revision"]},
        headers=_headers(),
    )
    assert published.status_code == 200
    assert published.json()["job"]["status"] == "publish_queued"


def test_concurrent_manual_binding_fences_stale_success_callback(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            (
                "bibtex",
                (
                    "a.bib",
                    b"@article{first, title={First}} @article{second, title={Second}}",
                    "text/plain",
                ),
            ),
        ],
    )
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "needs_attention", lease.token)

    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def regenerate(current_job: str) -> PreviewArtifacts:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            result = _valid_preview(artifacts, current_job, label=b"first")
            first_started.set()
            assert release_first.wait(5)
            return result
        return _valid_preview(artifacts, current_job, label=b"second")

    app.state.preview_regenerator = regenerate
    responses: dict[str, httpx.Response] = {}

    def put(name: str, entry_key: str) -> None:
        responses[name] = _request(
            app,
            "PUT",
            f"/jobs/{job_id}/bibtex-match",
            json={"entry_key": entry_key},
            headers=_headers(),
        )

    first_thread = threading.Thread(target=put, args=("first", "first"))
    first_thread.start()
    assert first_started.wait(5)
    second_thread = threading.Thread(target=put, args=("second", "second"))
    second_thread.start()
    second_thread.join(5)
    assert not second_thread.is_alive()
    assert responses["second"].status_code == 200
    release_first.set()
    first_thread.join(5)
    assert not first_thread.is_alive()

    assert responses["first"].status_code == 409
    assert responses["first"].json()["error"]["code"] == "stale_binding"
    final = _request(app, "GET", f"/jobs/{job_id}", headers=_headers())
    assert final.status_code == 200
    final_job = final.json()["job"]
    assert final_job["bibtex"]["entry_key"] == "second"
    assert final_job["status"] == "review_ready"
    artifact = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf", headers=_headers())
    assert artifact.status_code == 200
    assert b"second" in artifact.content


def test_concurrent_manual_binding_fences_stale_failure_callback(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            (
                "bibtex",
                (
                    "a.bib",
                    b"@article{first, title={First}} @article{second, title={Second}}",
                    "text/plain",
                ),
            ),
        ],
    )
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "needs_attention", lease.token)

    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def regenerate(current_job: str) -> PreviewArtifacts:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            _valid_preview(artifacts, current_job, label=b"stale")
            first_started.set()
            assert release_first.wait(5)
            raise RuntimeError("stale callback failed")
        return _valid_preview(artifacts, current_job, label=b"current")

    app.state.preview_regenerator = regenerate
    responses: dict[str, httpx.Response] = {}

    def put(name: str, entry_key: str) -> None:
        responses[name] = _request(
            app,
            "PUT",
            f"/jobs/{job_id}/bibtex-match",
            json={"entry_key": entry_key},
            headers=_headers(),
        )

    first_thread = threading.Thread(target=put, args=("first", "first"))
    first_thread.start()
    assert first_started.wait(5)
    second_thread = threading.Thread(target=put, args=("second", "second"))
    second_thread.start()
    second_thread.join(5)
    assert not second_thread.is_alive()
    assert responses["second"].status_code == 200
    release_first.set()
    first_thread.join(5)
    assert not first_thread.is_alive()

    assert responses["first"].status_code == 409
    assert responses["first"].json()["error"]["code"] == "stale_binding"
    final = _request(app, "GET", f"/jobs/{job_id}", headers=_headers())
    assert final.json()["job"]["bibtex"]["entry_key"] == "second"
    assert final.json()["job"]["status"] == "review_ready"
    artifact = _request(app, "GET", f"/jobs/{job_id}/artifacts/pdf", headers=_headers())
    assert artifact.status_code == 200
    assert b"current" in artifact.content


def test_unsafe_bibtex_keys_are_rejected_or_redacted(tmp_path: Path) -> None:
    app, state, artifacts = _make_app(tmp_path)
    unsafe_upload = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("bibtex", ("unsafe.bib", b"@article{../secret, title={Secret}}", "text/plain")),
        ],
    )
    assert unsafe_upload.status_code == 422
    assert unsafe_upload.json()["error"]["code"] == "invalid_upload"
    assert state.list_batches() == []

    response = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-1.7 a", "application/pdf")),
            ("bibtex", ("safe.bib", b"@article{safe-key, title={Safe}}", "text/plain")),
        ],
    )
    job_id = response.json()["job_ids"][0]
    lease = state.acquire_lease(job_id, "api-test")
    assert lease is not None
    state.transition(job_id, "needs_attention", lease.token)
    app.state.preview_regenerator = lambda current_job: _valid_preview(artifacts, current_job)
    unsafe_binding = _request(
        app,
        "PUT",
        f"/jobs/{job_id}/bibtex-match",
        json={"entry_key": "../secret"},
        headers=_headers(),
    )
    assert unsafe_binding.status_code == 422
    assert "/secret" not in unsafe_binding.text

    legacy_batch = state.create_batch()
    legacy_job = state.create_job(legacy_batch)
    state.persist_bibtex_entries(legacy_batch, [{"key": "../legacy-secret", "title": "Legacy"}])
    legacy_lease = state.acquire_lease(legacy_job, "api-test")
    assert legacy_lease is not None
    state.transition(legacy_job, "needs_attention", legacy_lease.token)
    state.bind_job_bibtex(legacy_job, "../legacy-secret")
    legacy = _request(app, "GET", f"/jobs/{legacy_job}", headers=_headers())
    assert legacy.status_code == 200
    assert "legacy-secret" not in legacy.text


def test_upload_and_body_error_taxonomy_is_stable(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path, max_pdf_bytes=8, max_batch_bytes=16, pdfs_per_batch=1)

    oversized = _upload(app, files=[("pdfs", ("large.pdf", b"%PDF-1.7 large", "application/pdf"))])
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "payload_too_large"

    too_many = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-a", "application/pdf")),
            ("pdfs", ("b.pdf", b"%PDF-b", "application/pdf")),
        ],
    )
    assert too_many.status_code == 413
    assert too_many.json()["error"]["code"] == "payload_too_large"

    invalid_bib = _upload(
        app,
        files=[
            ("pdfs", ("a.pdf", b"%PDF-a", "application/pdf")),
            ("bibtex", ("a.bib", b"not bibtex", "text/plain")),
        ],
    )
    assert invalid_bib.status_code == 422
    assert invalid_bib.json()["error"]["code"] == "invalid_upload"

    malformed = _request(app, "POST", "/jobs/nope/publish", content=b"{", headers=_headers())
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_body"

    missing = _request(app, "GET", "/jobs/no-such-job", headers=_headers())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_persisted_heartbeat_is_authoritative_over_provider_diagnostics(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(
        tmp_path,
        worker_status_provider=lambda: {"status": "online", "active_jobs": 99},
    )
    before = _request(app, "GET", "/config", headers=_headers())
    assert before.json()["worker"]["status"] == "offline"
    job = _upload(app, files=[("pdfs", ("a.pdf", b"%PDF-a", "application/pdf"))]).json()["job_ids"][0]
    lease = state.acquire_lease(job, "api-test")
    assert lease is not None
    after = _request(app, "GET", "/config", headers=_headers())
    assert after.json()["worker"]["status"] == "online"
    assert after.json()["worker"]["active_jobs"] == 1
    assert after.json()["worker"]["diagnostics"]["active_jobs"] == 99


def test_terminal_cancel_reports_conflict_or_explicit_noop(tmp_path: Path) -> None:
    app, state, _artifacts = _make_app(tmp_path)
    response = _upload(
        app,
        files=[
            ("pdfs", ("published.pdf", b"%PDF-published", "application/pdf")),
            ("pdfs", ("rejected.pdf", b"%PDF-rejected", "application/pdf")),
            ("pdfs", ("cancelled.pdf", b"%PDF-cancelled", "application/pdf")),
        ],
    )
    published, rejected, cancelled = response.json()["job_ids"]
    state.admin_transition(published, "running")
    state.admin_transition(published, "review_ready")
    state.admin_transition(published, "publish_queued")
    state.admin_transition(published, "publishing")
    state.admin_transition(published, "indexing")
    state.admin_transition(published, "published")
    state.admin_transition(rejected, "rejected")
    state.request_cancel(cancelled)

    published_response = _request(app, "POST", f"/jobs/{published}/cancel", headers=_headers())
    assert published_response.status_code == 409
    assert published_response.json()["error"]["code"] == "terminal_state"
    rejected_response = _request(app, "POST", f"/jobs/{rejected}/cancel", headers=_headers())
    assert rejected_response.status_code == 409
    cancelled_response = _request(app, "POST", f"/jobs/{cancelled}/cancel", headers=_headers())
    assert cancelled_response.status_code == 200
    assert cancelled_response.json()["cancel"]["no_op"] is True
    assert cancelled_response.json()["job"]["status"] == "cancelled"


def test_every_pipeline_route_requires_admin_authentication(tmp_path: Path) -> None:
    app, _state, _artifacts = _make_app(tmp_path)
    requests = [
        ("GET", "/config"),
        ("POST", "/batches"),
        ("GET", "/batches"),
        ("GET", "/batches/nope"),
        ("POST", "/batches/nope/publish-ready"),
        ("POST", "/batches/nope/cancel"),
        ("GET", "/jobs/nope"),
        ("POST", "/jobs/nope/retry"),
        ("POST", "/jobs/nope/cancel"),
        ("POST", "/jobs/nope/reject"),
        ("POST", "/jobs/nope/publish"),
        ("PUT", "/jobs/nope/bibtex-match"),
        ("GET", "/jobs/nope/artifacts/pdf"),
    ]
    for method, path in requests:
        response = _request(app, method, path)
        assert response.status_code == 401, (method, path, response.text)


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
