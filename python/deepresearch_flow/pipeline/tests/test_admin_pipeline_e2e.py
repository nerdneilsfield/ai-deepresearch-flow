"""Black-box end-to-end coverage for the optional administrative pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from deepresearch_flow.paper.snapshot.api import create_app as create_snapshot_app
from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import ModelAllowlist, PipelineConfig
from deepresearch_flow.pipeline.ingestion import BatchIngestor, UploadPart
from deepresearch_flow.pipeline.publication import LocalFormalStore, PublicationWorker
from deepresearch_flow.pipeline.runtime import build_publication_bundle_from_state
from deepresearch_flow.pipeline.state import PipelineState
from deepresearch_flow.pipeline.steps import PreviewArtifacts
from deepresearch_flow.pipeline.worker import PipelineWorker


ADMIN_TOKEN = "e2e-admin-token"

# The ingestion boundary validates the PDF signature and size.  Keep fixture
# small while retaining ordinary PDF catalog/page structure.
TINY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


class E2EAdapters:
    """Deterministic provider adapters used through the worker constructor seam."""

    def ocr(self, pdf_path: Path, model_key: str) -> str:
        assert pdf_path.read_bytes() == TINY_PDF
        assert model_key == "ocr-e2e"
        return "# E2E paper\n\nA tiny source document."

    def source_repair(self, markdown: str, **_: object) -> str:
        return markdown

    def math_repair(self, markdown: str, **_: object) -> str:
        return markdown

    def organize(self, markdown: str, **_: object) -> str:
        return markdown.strip() + "\n"

    def extract(self, markdown: str, model_key: str, **_: object) -> dict[str, object]:
        assert model_key == "extract-e2e"
        return {
            "paper_title": "E2E paper",
            "paper_authors": ["Ada Lovelace"],
            "publication_date": "2026",
            "publication_venue": "E2E Journal",
            "source_hash": hashlib.sha256(markdown.encode()).hexdigest(),
            "templates": {"simple": {"summary": "A tiny E2E summary."}},
        }

    def validate(self, summary: dict[str, object], **_: object) -> bool:
        return bool(summary.get("paper_title"))

    def summary_repair(self, summary: dict[str, object], **_: object) -> dict[str, object]:
        return summary

    def translate(self, markdown: str, model_key: str, **_: object) -> str:
        assert model_key == "translate-e2e"
        return "# E2E paper (translated)\n\n" + markdown

    def translation_repair(self, markdown: str, **_: object) -> str:
        return markdown


def _stack(
    tmp_path: Path,
    *,
    preview_regenerator: Any | None = None,
) -> tuple[PipelineConfig, PipelineState, ArtifactStore, Any]:
    config = PipelineConfig(
        enabled=True,
        work_dir=str(tmp_path / "work"),
        preview_root=str(tmp_path / "previews"),
        static_root=str(tmp_path / "static"),
        snapshot_root=str(tmp_path / "snapshot"),
        snapshot_db=str(tmp_path / "snapshot.sqlite3"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        pdfs_per_batch=2,
        max_pdf_bytes=4096,
        max_batch_bytes=8192,
        bibtex_max_bytes=4096,
        extract_templates=("simple",),
        ocr=ModelAllowlist(("ocr-e2e",), "ocr-e2e"),
        extract=ModelAllowlist(("extract-e2e",), "extract-e2e"),
        translate=ModelAllowlist(("translate-e2e",), "translate-e2e"),
        lease_seconds=30,
        heartbeat_seconds=1,
    )
    artifacts = ArtifactStore(config.work_dir, config.preview_root)
    state = PipelineState(
        config.queue_db,
        lease_seconds=config.lease_seconds,
        heartbeat_seconds=config.heartbeat_seconds,
        artifact_store=artifacts,
        publication_cache_root=config.static_root,
    )
    app = create_snapshot_app(
        snapshot_db=Path(config.snapshot_db),
        static_base_url="",
        mcp_access_token="snapshot-token",
        admin_token=ADMIN_TOKEN,
        pipeline_config=config,
        pipeline_state=state,
        pipeline_artifacts=artifacts,
        pipeline_preview_regenerator=preview_regenerator,
    )
    return config, state, artifacts, app


def _request(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://e2e") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _upload(
    app: Any,
    *,
    bibtex: bytes | None = None,
    filename: str = "e2e-paper.pdf",
) -> httpx.Response:
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("pdfs[]", (filename, TINY_PDF, "application/pdf")),
    ]
    if bibtex is not None:
        files.append(("bibtex", ("references.bib", bibtex, "text/plain")))
    return _request(
        app,
        "POST",
        "/api/v1/admin/pipeline/batches",
        files=files,
        data={
            "ocr_model": "ocr-e2e",
            "extract_model": "extract-e2e",
            "translate_model": "translate-e2e",
        },
        headers=_auth(),
    )


def _process(
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
    job_id: str,
) -> Any:
    return PipelineWorker(
        config,
        state,
        artifacts,
        adapters=E2EAdapters(),
        worker_id="e2e-processing-worker",
    ).run_job(job_id)


def _bundle_builder(
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
):
    return lambda job_id: build_publication_bundle_from_state(job_id, state, artifacts, config)


def _preview_regenerator(artifacts: ArtifactStore):
    def regenerate(job_id: str) -> PreviewArtifacts:
        input_pdf = artifacts.resolve(job_id, "pdf")
        if input_pdf is None:
            raise AssertionError("uploaded PDF is unavailable")
        contents = {
            "preview_pdf": input_pdf.path.read_bytes(),
            "preview_source_md": b"# E2E paper\n",
            "preview_summary_json": b'{"paper_title":"E2E paper"}',
            "preview_translated_md": b"# E2E paper (translated)\n",
        }
        protected = tuple(
            artifacts.protect(job_id, kind, contents[kind]) for kind in contents
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

    return regenerate


def test_enabled_http_upload_processing_artifacts_and_publication_are_end_to_end(
    tmp_path: Path,
) -> None:
    config, state, artifacts, app = _stack(tmp_path)
    uploaded = _upload(
        app,
        bibtex=b"@article{e2e-ref, title={E2E paper}, author={Lovelace, Ada}, year={2026}}",
    )
    assert uploaded.status_code == 200
    job_id = uploaded.json()["job_ids"][0]

    unauthorized = _request(
        app,
        "GET",
        f"/api/v1/admin/pipeline/jobs/{job_id}/artifacts/pdf",
    )
    assert unauthorized.status_code == 401
    assert _request(
        app,
        "GET",
        f"/api/v1/admin/pipeline/jobs/{job_id}/artifacts/pdf",
        headers=_auth(),
    ).status_code == 404
    static_root = Path(config.static_root)
    assert not list(static_root.rglob("*")) if static_root.exists() else True

    processed = _process(config, state, artifacts, job_id)
    assert processed.status == "review_ready"

    expected_artifacts = {
        "pdf": (TINY_PDF, "application/pdf"),
        "source_markdown": (b"# E2E paper\n", "text/markdown"),
        "summary_json": (None, "application/json"),
        "translated_markdown": (b"# E2E paper (translated)", "text/markdown"),
    }
    for kind, (prefix, content_type) in expected_artifacts.items():
        response = _request(
            app,
            "GET",
            f"/api/v1/admin/pipeline/jobs/{job_id}/artifacts/{kind}",
            headers=_auth(),
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(content_type)
        if kind == "summary_json":
            summary = json.loads(response.content)
            assert summary["paper_title"] == "E2E paper"
            assert summary["paper_authors"] == ["Ada Lovelace"]
        else:
            assert prefix is not None
            assert response.content.startswith(prefix)

    job = _request(
        app,
        "GET",
        f"/api/v1/admin/pipeline/jobs/{job_id}",
        headers=_auth(),
    ).json()["job"]
    revision = job["revision"]
    stale = _request(
        app,
        "POST",
        f"/api/v1/admin/pipeline/jobs/{job_id}/publish",
        json={"expected_revision": revision - 1},
        headers=_auth(),
    )
    assert stale.status_code == 409
    queued = _request(
        app,
        "POST",
        f"/api/v1/admin/pipeline/jobs/{job_id}/publish",
        json={"expected_revision": revision},
        headers=_auth(),
    )
    assert queued.status_code == 200
    assert queued.json()["job"]["status"] == "publish_queued"

    indexed: list[str] = []
    publisher = PublicationWorker(
        state,
        config.snapshot_db,
        LocalFormalStore(config.static_root),
        bundle_builder=_bundle_builder(config, state, artifacts),
        indexer=lambda bundle: indexed.append(bundle.paper_id),
        worker_id="e2e-publication-worker",
    )
    published = publisher.run_once()
    assert published[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"
    assert len(indexed) == 1

    search = _request(app, "GET", "/api/v1/search", params={"q": "E2E paper"})
    assert search.status_code == 200
    assert search.json()["total"] == 1
    paper_id = search.json()["items"][0]["paper_id"]
    detail = _request(app, "GET", f"/api/v1/papers/{paper_id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "E2E paper"
    bibtex = _request(app, "GET", f"/api/v1/papers/{paper_id}/bibtex")
    assert bibtex.status_code == 200
    assert bibtex.json()["bibtex_key"] == "e2e-ref"


def test_ambiguous_bibtex_manual_binding_regenerates_revision_and_publishes_selected_entry(
    tmp_path: Path,
) -> None:
    artifact_holder: dict[str, ArtifactStore] = {}
    config, state, artifacts, app = _stack(
        tmp_path,
        preview_regenerator=lambda job_id: _preview_regenerator(artifact_holder["store"])(job_id),
    )
    artifact_holder["store"] = artifacts
    uploaded = _upload(
        app,
        bibtex=(
            b"@article{candidate-a, title={E2E paper}, author={Lovelace, Ada}}\n"
            b"@article{candidate-b, title={E2E paper}, author={Lovelace, Ada}}\n"
        ),
    )
    assert uploaded.status_code == 200
    job_id = uploaded.json()["job_ids"][0]
    assert _process(config, state, artifacts, job_id).status == "needs_attention"

    detail = _request(
        app,
        "GET",
        f"/api/v1/admin/pipeline/jobs/{job_id}",
        headers=_auth(),
    ).json()["job"]
    assert detail["status"] == "needs_attention"
    assert {item["key"] for item in detail["bibtex"]["candidates"]} == {
        "candidate-a",
        "candidate-b",
    }
    stale_revision = detail["revision"]

    binding = _request(
        app,
        "PUT",
        f"/api/v1/admin/pipeline/jobs/{job_id}/bibtex-match",
        json={"entry_key": "candidate-a"},
        headers=_auth(),
    )
    assert binding.status_code == 200
    bound_job = binding.json()["job"]
    assert bound_job["status"] == "review_ready"
    assert bound_job["revision"] > stale_revision
    assert bound_job["bibtex"]["entry_key"] == "candidate-a"

    stale_publish = _request(
        app,
        "POST",
        f"/api/v1/admin/pipeline/jobs/{job_id}/publish",
        json={"expected_revision": stale_revision},
        headers=_auth(),
    )
    assert stale_publish.status_code == 409
    current_publish = _request(
        app,
        "POST",
        f"/api/v1/admin/pipeline/jobs/{job_id}/publish",
        json={"expected_revision": bound_job["revision"]},
        headers=_auth(),
    )
    assert current_publish.status_code == 200
    assert current_publish.json()["job"]["status"] == "publish_queued"

    publisher = PublicationWorker(
        state,
        config.snapshot_db,
        LocalFormalStore(config.static_root),
        bundle_builder=_bundle_builder(config, state, artifacts),
        indexer=lambda _: None,
        worker_id="e2e-manual-publication-worker",
    )
    assert publisher.run_once()[0].status == "published"

    search = _request(app, "GET", "/api/v1/search", params={"q": "E2E paper"})
    assert search.status_code == 200
    paper_id = search.json()["items"][0]["paper_id"]
    bibtex = _request(app, "GET", f"/api/v1/papers/{paper_id}/bibtex")
    assert bibtex.status_code == 200
    assert bibtex.json()["bibtex_key"] == "candidate-a"


def test_publication_receipt_recovery_reindexes_once_without_duplicate_static_or_snapshot_records(
    tmp_path: Path,
) -> None:
    config, state, artifacts, app = _stack(tmp_path)
    uploaded = _upload(app)
    assert uploaded.status_code == 200
    job_id = uploaded.json()["job_ids"][0]
    assert _process(config, state, artifacts, job_id).status == "review_ready"
    job = _request(
        app,
        "GET",
        f"/api/v1/admin/pipeline/jobs/{job_id}",
        headers=_auth(),
    ).json()["job"]
    queued = _request(
        app,
        "POST",
        f"/api/v1/admin/pipeline/jobs/{job_id}/publish",
        json={"expected_revision": job["revision"]},
        headers=_auth(),
    )
    assert queued.status_code == 200

    unrelated_static = Path(config.static_root) / "unrelated.txt"
    unrelated_static.parent.mkdir(parents=True, exist_ok=True)
    unrelated_static.write_bytes(b"keep-static")
    unrelated_vector = tmp_path / "vector" / "unrelated.bin"
    unrelated_vector.parent.mkdir()
    unrelated_vector.write_bytes(b"keep-vector")

    first_index_calls: list[str] = []

    def crash_after_receipt(_: object) -> None:
        first_index_calls.append("crashed")
        raise KeyboardInterrupt("simulated process crash")

    crashing = PublicationWorker(
        state,
        config.snapshot_db,
        LocalFormalStore(config.static_root),
        bundle_builder=_bundle_builder(config, state, artifacts),
        indexer=crash_after_receipt,
        worker_id="e2e-crashed-publication-worker",
    )
    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        crashing.run_once()
    assert state.get_job(job_id)["status"] == "indexing"
    formal_before = {
        path.relative_to(Path(config.static_root)): path.read_bytes()
        for path in Path(config.static_root).rglob("*")
        if path.is_file()
    }
    visible_before = _request(app, "GET", "/api/v1/search", params={"q": "E2E paper"})
    assert visible_before.status_code == 200
    assert visible_before.json()["total"] == 1

    recovered_jobs = state.recover_expired(
        now=datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert job_id in recovered_jobs
    recovery_index_calls: list[str] = []
    restarted = PublicationWorker(
        state,
        config.snapshot_db,
        LocalFormalStore(config.static_root),
        bundle_builder=_bundle_builder(config, state, artifacts),
        indexer=lambda bundle: recovery_index_calls.append(bundle.paper_id),
        worker_id="e2e-restarted-publication-worker",
    )
    result = restarted.run_once()
    assert result[0].status == "published"
    assert state.get_job(job_id)["status"] == "published"
    assert first_index_calls == ["crashed"]
    assert len(recovery_index_calls) == 1
    formal_after = {
        path.relative_to(Path(config.static_root)): path.read_bytes()
        for path in Path(config.static_root).rglob("*")
        if path.is_file()
    }
    assert formal_after == formal_before
    assert unrelated_static.read_bytes() == b"keep-static"
    assert unrelated_vector.read_bytes() == b"keep-vector"

    visible_after = _request(app, "GET", "/api/v1/search", params={"q": "E2E paper"})
    assert visible_after.status_code == 200
    assert visible_after.json()["total"] == 1
