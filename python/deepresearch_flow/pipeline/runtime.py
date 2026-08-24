"""Long-running production runtime for the optional pipeline Worker.

The HTTP process and Worker intentionally share only the durable queue and work
roots.  This module owns the process lifecycle: lease recovery, idle
heartbeats, processing, queued publication, bounded cleanup, and signal-safe
shutdown between polling cycles.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
from threading import Event
import time
from typing import Any, Mapping

from .artifacts import ArtifactStore
from .config import PipelineConfig, load_pipeline_config
from .publication import build_publication_bundle
from .publication_indexing import LanceDBIndexer
from .publication_store import LocalFormalStore, WebDavFormalStore
from .publication_worker import PublicationWorker
from .state import PipelineState
from .worker import PipelineWorker

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineEnvironment:
    """Validated relation between TOML feature state and process bridge."""

    config: PipelineConfig
    pipeline_enabled: bool
    worker_enabled: bool


@dataclass(frozen=True)
class WorkerLoopResult:
    """Externally useful counters from one graceful Worker lifetime."""

    cycles: int
    processed_jobs: int
    published_jobs: int
    recovered_jobs: int
    cleaned_jobs: int
    errors: int = 0


def validate_pipeline_environment(
    config_path: str | Path,
    environ: Mapping[str, str] | None = None,
    *,
    require_runtime_files: bool = True,
) -> PipelineEnvironment:
    """Validate deployment bridge without mutating state or revealing secrets.

    ``PAPER_PIPELINE_ENABLED`` is deliberately a process-materialization
    bridge, not a second feature flag.  An explicit value must agree with
    ``[pipeline].enabled``; an unset value leaves Worker materialization off.
    """

    env = os.environ if environ is None else environ
    config = load_pipeline_config(config_path)
    raw_bridge = str(env.get("PAPER_PIPELINE_ENABLED", "")).strip().lower()
    bridge: bool | None
    if not raw_bridge:
        bridge = None
    elif raw_bridge in {"1", "true", "yes", "on"}:
        bridge = True
    elif raw_bridge in {"0", "false", "no", "off"}:
        bridge = False
    else:
        raise ValueError("PAPER_PIPELINE_ENABLED must be 0 or 1")

    if bridge is True and not config.enabled:
        raise ValueError("PAPER_PIPELINE_ENABLED cannot enable a disabled pipeline")
    if bridge is False and config.enabled:
        raise ValueError("PAPER_PIPELINE_ENABLED and [pipeline].enabled disagree")
    if bridge is None and config.enabled:
        raise ValueError(
            "PAPER_PIPELINE_ENABLED must be 1 when [pipeline].enabled is true"
        )

    worker_enabled = bool(bridge and config.enabled)
    if worker_enabled:
        if not str(env.get("PAPER_DB_ADMIN_TOKEN", "")).strip():
            raise ValueError("PAPER_DB_ADMIN_TOKEN is required when pipeline is enabled")
        ocr_config = str(env.get("PAPER_OCR_CONFIG", "ocr.toml")).strip()
        if require_runtime_files and not Path(ocr_config).is_file():
            raise ValueError(f"OCR config file not found: {ocr_config}")
    return PipelineEnvironment(config, config.enabled, worker_enabled)


def run_worker_until_stopped(
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
    *,
    paper_config_path: str | Path | None = None,
    ocr_config_path: str | Path | None = None,
    snapshot_db: str | Path | None = None,
    vector_dir: str | Path | None = None,
    processing_worker: Any | None = None,
    publication_worker: Any | None = None,
    stop_event: Event | None = None,
    worker_id: str | None = None,
    poll_interval_seconds: float = 1.0,
    cleanup_interval_seconds: float = 300.0,
    max_cycles: int | None = None,
) -> WorkerLoopResult:
    """Run durable processing/publication polling until TERM or test bound.

    The optional worker objects are public dependency seams for black-box
    lifecycle tests.  Production callers omit them and receive real adapters.
    Active Job calls are allowed to finish; the stop flag is observed before
    the next cycle, so processing workers stop at their existing step
    boundaries.
    """

    if not config.enabled:
        raise ValueError("pipeline Worker requires [pipeline].enabled = true")
    if poll_interval_seconds < 0 or cleanup_interval_seconds < 0:
        raise ValueError("Worker intervals must not be negative")
    worker_name = str(worker_id or f"pipeline-worker-{os.getpid()}").strip()
    if not worker_name:
        raise ValueError("worker_id must not be empty")
    stop = stop_event or Event()
    processing = processing_worker
    if processing is None:
        if paper_config_path is None or ocr_config_path is None:
            raise ValueError("production Worker requires paper and OCR config paths")
        processing = PipelineWorker.from_production_config(
            config,
            state,
            artifacts,
            paper_config_path=paper_config_path,
            ocr_config_path=ocr_config_path,
            worker_id=worker_name,
        )
    publication = publication_worker
    closeables: list[Any] = []
    if publication is None and snapshot_db is not None:
        publication, closeables = _build_production_publication_worker(
            config,
            state,
            artifacts,
            snapshot_db=Path(snapshot_db),
            vector_dir=Path(vector_dir) if vector_dir is not None else None,
            paper_config_path=paper_config_path,
            worker_id=worker_name,
        )

    cycles = processed = published = recovered = cleaned = errors = 0
    next_cleanup = 0.0
    try:
        while not stop.is_set() and (max_cycles is None or cycles < max_cycles):
            try:
                recovered += len(state.recover_expired())
                state.worker_heartbeat(worker_name)
            except Exception:
                errors += 1
                LOGGER.exception("pipeline Worker recovery/heartbeat failed")

            try:
                processing_ids = state.list_job_ids({"queued", "failed", "batch_waiting"})
                processed += len(processing.run_once(processing_ids))
            except Exception:
                errors += 1
                LOGGER.exception("pipeline processing cycle failed")

            if publication is not None and not stop.is_set():
                try:
                    publication_ids = state.list_job_ids({"publish_queued", "indexing"})
                    published += len(publication.run_once(publication_ids))
                except Exception:
                    errors += 1
                    LOGGER.exception("pipeline publication cycle failed")

            now = time.monotonic()
            if cleanup_interval_seconds == 0 or now >= next_cleanup:
                try:
                    cleaned += len(state.cleanup_expired_artifacts())
                except Exception:
                    errors += 1
                    LOGGER.exception("pipeline cleanup cycle failed")
                next_cleanup = now + cleanup_interval_seconds
            state.worker_heartbeat(worker_name)
            cycles += 1
            if stop.is_set() or (max_cycles is not None and cycles >= max_cycles):
                break
            stop.wait(poll_interval_seconds)
    finally:
        for closeable in closeables:
            close = getattr(closeable, "close", None)
            if callable(close):
                close()
    return WorkerLoopResult(cycles, processed, published, recovered, cleaned, errors)


def run_production_worker_forever(
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
    *,
    paper_config_path: str | Path,
    ocr_config_path: str | Path,
    snapshot_db: str | Path,
    vector_dir: str | Path | None = None,
    worker_id: str | None = None,
    stop_event: Event | None = None,
) -> WorkerLoopResult:
    """Canonical production entrypoint used by Supervisor."""

    return run_worker_until_stopped(
        config,
        state,
        artifacts,
        paper_config_path=paper_config_path,
        ocr_config_path=ocr_config_path,
        snapshot_db=snapshot_db,
        vector_dir=vector_dir,
        worker_id=worker_id,
        stop_event=stop_event,
    )


def _build_production_publication_worker(
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
    *,
    snapshot_db: Path,
    vector_dir: Path | None,
    paper_config_path: str | Path | None,
    worker_id: str,
) -> tuple[PublicationWorker, list[Any]]:
    closeables: list[Any] = []
    if config.webdav_url:
        username = os.environ.get("PAPER_PIPELINE_WEBDAV_USERNAME", "")
        password = os.environ.get("PAPER_PIPELINE_WEBDAV_PASSWORD", "")
        if not username or not password:
            raise ValueError(
                "WebDAV publication requires PAPER_PIPELINE_WEBDAV_USERNAME and "
                "PAPER_PIPELINE_WEBDAV_PASSWORD"
            )
        from deepresearch_flow.storage.webdav import WebDavStorage

        storage = WebDavStorage(config.webdav_url, username, password)
        formal_store = WebDavFormalStore(storage)
        closeables.append(storage)
    else:
        formal_store = LocalFormalStore(config.static_root)

    indexer = None
    if vector_dir is not None and paper_config_path is not None:
        from deepresearch_flow.paper.config import load_config

        indexer = LanceDBIndexer(
            load_config(str(paper_config_path)),
            snapshot_db,
            Path(config.static_root),
            vector_dir,
        )

    def bundle_builder(job_id: str) -> Any:
        return _bundle_from_state(job_id, state, artifacts, config)

    return (
        PublicationWorker(
            state,
            snapshot_db,
            formal_store,
            bundle_builder=bundle_builder,
            indexer=indexer,
            worker_id=worker_id,
        ),
        closeables,
    )


def _bundle_from_state(
    job_id: str,
    state: PipelineState,
    artifacts: ArtifactStore,
    config: PipelineConfig,
) -> Any:
    details = state.get_job_details(job_id)
    summary = details.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError(f"job {job_id} has no normalized summary")
    resources: dict[str, bytes] = {}
    artifact_rows = {
        str(row.get("kind")): row
        for row in details.get("artifacts", [])
        if isinstance(row, Mapping)
    }
    for kind, alias in (
        ("preview_pdf", "pdf"),
        ("preview_source_md", "source_markdown"),
        ("preview_summary_json", "summary_json"),
        ("preview_translated_md", "translated_markdown"),
    ):
        row = artifact_rows.get(kind)
        if row is None:
            raise ValueError(f"job {job_id} is missing protected artifact {kind}")
        path = Path(str(row.get("path", "")))
        from .artifacts import Artifact

        artifact = Artifact(
            job_id,
            kind,
            path,
            str(row.get("digest", "")),
            int(row.get("size", 0)),
            artifacts.formal_root,
            path.parent,
        )
        artifacts.validate_protected_artifact(artifact, job_id, kind)
        content = path.read_bytes()
        if len(content) != artifact.size:
            raise ValueError(f"protected artifact size mismatch for {kind}")
        resources[alias] = content

    bibtex: Mapping[str, Any] | None = None
    entry_key = details.get("bibtex_entry_key")
    if entry_key:
        batch_id = details.get("batch_id")
        entries = state.list_bibtex_entries(str(batch_id)) if batch_id else []
        entry = next((item for item in entries if str(item.get("key")) == str(entry_key)), None)
        if entry is None:
            raise ValueError(f"BibTeX entry {entry_key} is unavailable")
        bibtex = {"status": "matched", "entry": entry, "entry_key": str(entry_key)}
    else:
        bibtex = {"status": "not_provided"}
    return build_publication_bundle(
        job_id,
        summary,
        bibtex=bibtex,
        resources=resources,
        work_dir=artifacts.work_dir,
        translation_language=config.translation_language,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deepresearch-flow pipeline Worker")
    parser.add_argument("--config", default=os.environ.get("PAPER_DB_CONFIG", "config.toml"))
    parser.add_argument(
        "--ocr-config", default=os.environ.get("PAPER_OCR_CONFIG", "ocr.toml")
    )
    parser.add_argument(
        "--snapshot-db", default=os.environ.get("PAPER_DB_SNAPSHOT_DB", "/db/papers.db")
    )
    parser.add_argument("--vector-dir", default=os.environ.get("PAPER_DB_EMBED_DB"))
    parser.add_argument("--worker-id", default=os.environ.get("PAPER_PIPELINE_WORKER_ID"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Supervisor process entrypoint with TERM-aware graceful shutdown."""

    args = _parse_args(argv)
    environment = validate_pipeline_environment(args.config, require_runtime_files=True)
    if not environment.worker_enabled:
        raise ValueError("pipeline Worker requires PAPER_PIPELINE_ENABLED=1")
    config = environment.config
    artifacts = ArtifactStore(
        config.work_dir,
        config.static_root,
        retention_days=config.retention_days,
    )
    state = PipelineState(
        config.queue_db,
        lease_seconds=config.lease_seconds,
        heartbeat_seconds=config.heartbeat_seconds,
        artifact_store=artifacts,
    )
    stop = Event()

    def request_stop(signum: int, _frame: Any) -> None:
        LOGGER.info("pipeline Worker received signal %s; stopping at cycle boundary", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_production_worker_forever(
        config,
        state,
        artifacts,
        paper_config_path=args.config,
        ocr_config_path=args.ocr_config,
        snapshot_db=args.snapshot_db,
        vector_dir=args.vector_dir,
        worker_id=args.worker_id,
        stop_event=stop,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("PAPER_PIPELINE_LOG_LEVEL", "INFO"))
    raise SystemExit(main())


__all__ = [
    "PipelineEnvironment",
    "WorkerLoopResult",
    "run_production_worker_forever",
    "run_worker_until_stopped",
    "validate_pipeline_environment",
]
