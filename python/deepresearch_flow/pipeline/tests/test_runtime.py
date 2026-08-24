from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import httpx
import pytest

from deepresearch_flow.pipeline.artifacts import ArtifactStore
from deepresearch_flow.pipeline.config import load_pipeline_config
from deepresearch_flow.pipeline.runtime import (
    WorkerLoopResult,
    resolve_snapshot_db,
    run_worker_until_stopped,
    validate_pipeline_mounts,
    validate_pipeline_environment,
)
from deepresearch_flow.pipeline.state import PipelineState


def _config(path: Path, *, enabled: bool) -> Path:
    root = path.parent
    path.write_text(
        "[pipeline]\n"
        f"enabled = {'true' if enabled else 'false'}\n"
        f"work_dir = {str(root / 'work')!r}\n"
        f"queue_db = {str(root / 'work' / 'queue.sqlite3')!r}\n"
        f"static_root = {str(root / 'formal')!r}\n"
        f"snapshot_root = {str(root / 'snapshot')!r}\n"
        f"snapshot_db = {str(root / 'snapshot.sqlite3')!r}\n"
        "[pipeline.models.ocr]\n"
        "allowlist = ['ocr/test']\n"
        "default = 'ocr/test'\n"
        "[pipeline.models.extract]\n"
        "allowlist = ['extract/test']\n"
        "default = 'extract/test'\n"
        "[pipeline.models.translate]\n"
        "allowlist = ['translate/test']\n"
        "default = 'translate/test'\n",
        encoding="utf-8",
    )
    return path


def test_pipeline_environment_requires_explicit_consistent_worker_bridge(
    tmp_path: Path,
) -> None:
    disabled_path = _config(tmp_path / "disabled.toml", enabled=False)
    enabled_path = _config(tmp_path / "enabled.toml", enabled=True)

    disabled = validate_pipeline_environment(disabled_path, {})
    assert disabled.pipeline_enabled is False
    assert disabled.worker_enabled is False

    enabled = validate_pipeline_environment(
        enabled_path,
        {
            "PAPER_PIPELINE_ENABLED": "1",
            "PAPER_DB_ADMIN_TOKEN": "admin-token",
            "PAPER_OCR_CONFIG": str(tmp_path / "ocr.toml"),
        },
        require_runtime_files=False,
    )
    assert enabled.pipeline_enabled is True
    assert enabled.worker_enabled is True

    with pytest.raises(ValueError, match="disagree"):
        validate_pipeline_environment(enabled_path, {"PAPER_PIPELINE_ENABLED": "0"})

    with pytest.raises(ValueError, match="disabled"):
        validate_pipeline_environment(disabled_path, {"PAPER_PIPELINE_ENABLED": "1"})


def test_pipeline_mount_validator_accepts_persistent_ancestors_and_rejects_root(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    work = private / "work"
    previews = private / "previews"
    work.mkdir(parents=True)
    previews.mkdir()
    static = tmp_path / "static"
    static.mkdir()
    db = tmp_path / "db"
    db.mkdir()
    config_path = tmp_path / "mounts.toml"
    config_path.write_text(
        "[pipeline]\n"
        "enabled = true\n"
        f"work_dir = {str(work)!r}\n"
        f"preview_root = {str(previews)!r}\n"
        f"queue_db = {str(db / 'queue.sqlite3')!r}\n"
        f"static_root = {str(static)!r}\n"
        f"snapshot_db = {str(db / 'papers.db')!r}\n"
        "[pipeline.models.ocr]\nallowlist=['ocr/test']\ndefault='ocr/test'\n"
        "[pipeline.models.extract]\nallowlist=['extract/test']\ndefault='extract/test'\n"
        "[pipeline.models.translate]\nallowlist=['translate/test']\ndefault='translate/test'\n",
        encoding="utf-8",
    )
    mountinfo = (
        "36 29 0:32 / / rw,relatime - overlay overlay rw\n"
        f"37 36 0:33 / {tmp_path} rw,relatime - ext4 /dev/test rw\n"
    )
    validated = validate_pipeline_mounts(
        config_path,
        {"PAPER_PIPELINE_REQUIRE_MOUNTS": "1", "PAPER_PIPELINE_MOUNTINFO": mountinfo},
    )
    assert validated["work_dir"] == str(work.resolve())
    assert validated["snapshot_db"] == str((db / "papers.db").resolve())

    with pytest.raises(ValueError, match="persistent mount"):
        validate_pipeline_mounts(
            config_path,
            {
                "PAPER_PIPELINE_REQUIRE_MOUNTS": "1",
                "PAPER_PIPELINE_MOUNTINFO": "36 29 0:32 / / rw - overlay overlay rw\n",
            },
        )


def test_snapshot_db_cli_value_must_match_pipeline_configuration(tmp_path: Path) -> None:
    config_path = _config(tmp_path / "config.toml", enabled=True)
    config = load_pipeline_config(config_path)
    assert resolve_snapshot_db(config) == Path(config.snapshot_db).resolve()
    with pytest.raises(ValueError, match="snapshot_db"):
        resolve_snapshot_db(config, tmp_path / "other.db")


def test_worker_loop_processes_jobs_persists_heartbeat_and_stops_at_boundary(
    tmp_path: Path,
) -> None:
    config = load_pipeline_config(_config(tmp_path / "config.toml", enabled=True))
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(
        tmp_path / "work" / "queue.sqlite3",
        lease_seconds=30,
        heartbeat_seconds=1,
        artifact_store=artifacts,
    )
    job_id = state.create_job(selected_models={"ocr": "ocr/test"})
    stop = Event()

    class Processing:
        def run_once(self, job_ids: list[str] | None = None) -> list[object]:
            lease = state.acquire_lease(job_id, "test-worker")
            assert lease is not None
            state.transition(job_id, "review_ready", lease.token)
            stop.set()
            return [object()]

    class Publication:
        def run_once(self, job_ids: list[str] | None = None) -> list[object]:
            return []

    result = run_worker_until_stopped(
        config,
        state,
        artifacts,
        processing_worker=Processing(),
        publication_worker=Publication(),
        stop_event=stop,
        poll_interval_seconds=0,
        cleanup_interval_seconds=0,
        worker_id="test-worker",
    )

    assert isinstance(result, WorkerLoopResult)
    assert result.cycles == 1
    assert state.get_job(job_id)["status"] == "review_ready"
    assert state.worker_heartbeat_metadata("test-worker") is not None


def test_production_publication_worker_publishes_registered_preview_artifacts(
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path / "config.toml", enabled=True)
    config = load_pipeline_config(config_path)
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(
        tmp_path / "work" / "queue.sqlite3",
        lease_seconds=30,
        heartbeat_seconds=1,
        artifact_store=artifacts,
    )
    job_id = state.create_job(selected_models={"ocr": "ocr/test"})
    lease = state.acquire_lease(job_id, "preview-worker")
    assert lease is not None
    summary = {
        "paper_title": "Runtime smoke paper",
        "paper_authors": ["Ada Lovelace"],
        "templates": {"simple": {"summary": "A runtime smoke."}},
    }
    state.record_job_summary(job_id, summary, lease.token)
    preview_values = {
        "preview_pdf": b"%PDF-1.7 runtime smoke",
        "preview_source_md": b"# Runtime smoke\n",
        "preview_summary_json": b'{"summary":"A runtime smoke."}\n',
        "preview_translated_md": b"# Runtime smoke\n",
    }
    for kind, content in preview_values.items():
        artifact = artifacts.protect(job_id, kind, content)
        state.register_protected_artifact(job_id, kind, artifact, lease.token)
    state.transition(job_id, "review_ready", lease.token)
    revision = int(state.get_job(job_id)["revision"])
    state.queue_publication(job_id, revision)

    class NoopProcessing:
        def run_once(self, job_ids: list[str] | None = None) -> list[object]:
            return []

    result = run_worker_until_stopped(
        config,
        state,
        artifacts,
        paper_config_path=config_path,
        ocr_config_path=tmp_path / "ocr.toml",
        snapshot_db=tmp_path / "snapshot.sqlite3",
        processing_worker=NoopProcessing(),
        worker_id="publisher-worker",
        poll_interval_seconds=0,
        cleanup_interval_seconds=0,
        max_cycles=1,
    )

    assert result.published_jobs == 1
    assert state.get_job(job_id)["status"] == "published"
    assert any(path.is_file() for path in (tmp_path / "formal").rglob("*.pdf"))

    # Verify publication through public Snapshot I/O.  Do not couple this
    # runtime smoke to the private receipt table used by recovery tests.
    from deepresearch_flow.paper.snapshot.api import create_app

    app = create_app(
        snapshot_db=tmp_path / "snapshot.sqlite3",
        static_base_url="",
        mcp_access_token="runtime-smoke-token",
    )
    async def get_search() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://runtime-smoke"
        ) as client:
            return await client.get("/api/v1/search", params={"q": "Runtime smoke"})

    response = asyncio.run(get_search())
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Runtime smoke paper"
