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
import stat
from threading import Event
import time
from typing import Any, Mapping

from .artifacts import ArtifactStore
from .config import PipelineConfig, load_pipeline_config
from .publication import (
    build_publication_bundle,
    build_publication_bundle_from_manifest,
)
from .publication_indexing import LanceDBIndexer
from .publication_store import LocalFormalStore, MirroredFormalStore, WebDavFormalStore, safe_relative_path
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


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _decode_mountinfo_path(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _mount_points(mountinfo: str) -> tuple[Path, ...]:
    points: list[Path] = []
    for line in mountinfo.splitlines():
        left, separator, _right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        if len(fields) < 5:
            continue
        points.append(Path(_decode_mountinfo_path(fields[4])))
    return tuple(points)


def _mounted_under(path: Path, mounts: tuple[Path, ...]) -> bool:
    candidates = [mount for mount in mounts if path == mount or path.is_relative_to(mount)]
    return bool(candidates and max(candidates, key=lambda item: len(item.parts)) != Path("/"))


def _writable_directory(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    writable_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    return bool(mode & writable_bits) and os.access(path, os.W_OK)


def _as_absolute_path(value: str, role: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"pipeline {role} path must be absolute")
    return path.resolve(strict=False)


def _validate_directory(path: Path, role: str) -> None:
    if not path.is_dir() or not _writable_directory(path):
        raise ValueError(f"pipeline {role} path is missing or not writable")


def _validate_database_path(path: Path, role: str) -> None:
    parent = path.parent
    if not parent.is_dir() or not _writable_directory(parent):
        raise ValueError(f"pipeline {role} parent is missing or not writable")
    if path.exists() and (not path.is_file() or not os.access(path, os.W_OK)):
        raise ValueError(f"pipeline {role} is not writable")


def validate_pipeline_mounts(
    config_or_path: PipelineConfig | str | Path,
    environ: Mapping[str, str] | None = None,
    *,
    require_mounts: bool | None = None,
) -> dict[str, str]:
    """Validate enabled pipeline storage paths and optional container mounts.

    ``PAPER_PIPELINE_REQUIRE_MOUNTS=1`` or Docker runtime enables mount-boundary
    checks.  ``PAPER_PIPELINE_MOUNTINFO`` is a public test/deployment seam; in
    production the kernel mount table is read.  Errors identify only logical
    roles, never credentials or raw operational paths.
    """

    env = os.environ if environ is None else environ
    config = (
        load_pipeline_config(config_or_path)
        if isinstance(config_or_path, (str, Path))
        else config_or_path
    )
    if not config.enabled:
        return {}
    values = {
        "work_dir": _as_absolute_path(config.work_dir, "work_dir"),
        "preview_root": _as_absolute_path(config.preview_root, "preview_root"),
        "queue_db": _as_absolute_path(config.queue_db, "queue_db"),
        "static_root": _as_absolute_path(config.static_root, "static_root"),
        "snapshot_db": _as_absolute_path(config.snapshot_db, "snapshot_db"),
    }
    for role in ("work_dir", "preview_root", "static_root"):
        _validate_directory(values[role], role)
    _validate_database_path(values["queue_db"], "queue_db")
    _validate_database_path(values["snapshot_db"], "snapshot_db")
    roots = (values["work_dir"], values["preview_root"], values["static_root"])
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError("pipeline storage roots must be physically separate")

    raw_force = str(env.get("PAPER_PIPELINE_REQUIRE_MOUNTS", "")).strip().lower()
    if raw_force and raw_force not in _TRUE_VALUES | _FALSE_VALUES:
        raise ValueError("PAPER_PIPELINE_REQUIRE_MOUNTS must be 0 or 1")
    in_docker = str(env.get("PAPER_PIPELINE_DOCKER", "")).strip().lower() in _TRUE_VALUES
    in_docker = in_docker or Path("/.dockerenv").exists() or bool(env.get("container"))
    enforce_mounts = (
        raw_force in _TRUE_VALUES if require_mounts is None else bool(require_mounts)
    ) or in_docker
    if enforce_mounts:
        supplied = env.get("PAPER_PIPELINE_MOUNTINFO")
        try:
            mountinfo = supplied if supplied is not None else Path("/proc/self/mountinfo").read_text()
        except OSError as exc:
            raise ValueError("pipeline persistent mount information is unavailable") from exc
        mounts = _mount_points(mountinfo)
        for role, path in values.items():
            target = path if path.exists() else path.parent
            if not _mounted_under(target, mounts):
                raise ValueError(f"pipeline {role} is not on a persistent mount")
    return {role: str(path) for role, path in values.items()}


def resolve_snapshot_db(
    config: PipelineConfig, requested: str | Path | None = None
) -> Path:
    """Resolve one Snapshot path and reject CLI/env/config disagreement."""
    configured = Path(config.snapshot_db).expanduser().resolve()
    if requested is None:
        return configured
    candidate = Path(requested).expanduser().resolve()
    if candidate != configured:
        raise ValueError("snapshot_db CLI value must match pipeline configuration")
    return configured


def validate_pipeline_environment(
    config_path: str | Path,
    environ: Mapping[str, str] | None = None,
    *,
    require_runtime_files: bool = True,
    require_runtime_storage: bool | None = None,
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
            raise ValueError("OCR config file is missing")
        if require_runtime_storage is None:
            require_runtime_storage = require_runtime_files
        if require_runtime_storage:
            validate_pipeline_mounts(config, env)
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
            stop_requested=stop.is_set,
        )
    publication = publication_worker
    closeables: list[Any] = []
    if publication is None and snapshot_db is not None:
        resolved_snapshot_db = resolve_snapshot_db(config, snapshot_db)
        publication, closeables = _build_production_publication_worker(
            config,
            state,
            artifacts,
            snapshot_db=resolved_snapshot_db,
            vector_dir=Path(vector_dir) if vector_dir is not None else None,
            paper_config_path=paper_config_path,
            worker_id=worker_name,
            stop_requested=stop.is_set,
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
                    cleaned += len(
                        state.cleanup_expired_artifacts(limit=config.cleanup_batch_size)
                    )
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
    stop_requested: Any | None = None,
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
        formal_store = MirroredFormalStore(WebDavFormalStore(storage), LocalFormalStore(config.static_root))
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
        return build_publication_bundle_from_state(job_id, state, artifacts, config)

    return (
        PublicationWorker(
            state,
            snapshot_db,
            formal_store,
            bundle_builder=bundle_builder,
            indexer=indexer,
            worker_id=worker_id,
            stop_requested=stop_requested,
        ),
        closeables,
    )


def build_publication_bundle_from_state(
    job_id: str,
    state: PipelineState,
    artifacts: ArtifactStore,
    config: PipelineConfig,
) -> Any:
    details = state.get_job_details(job_id)
    manifest_getter = getattr(state, "get_publication_manifest", None)
    manifest = manifest_getter(job_id) if callable(manifest_getter) else None
    artifact_rows = {
        str(row.get("kind")): row
        for row in details.get("artifacts", [])
        if isinstance(row, Mapping)
    }
    required_kinds = {
        "preview_pdf",
        "preview_source_md",
        "preview_summary_json",
        "preview_translated_md",
    }
    if isinstance(manifest, Mapping) and not required_kinds.issubset(artifact_rows):
        return _bundle_from_published_manifest(
            manifest,
            static_root=Path(config.static_root),
            work_dir=Path(artifacts.work_dir),
        )
    summary = details.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError(f"job {job_id} has no normalized summary")
    resources: dict[str, bytes] = {}
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
            artifacts.preview_root,
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


def _bundle_from_published_manifest(
    manifest: Mapping[str, Any], *, static_root: Path, work_dir: Path
) -> Any:
    """Load content-addressed formal cache without touching private previews."""
    root = static_root.resolve()
    resources: dict[str, bytes] = {}
    records = manifest.get("resources")
    if not isinstance(records, list):
        raise ValueError("publication manifest has invalid resources")
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("publication manifest has invalid resource metadata")
        relative_path = safe_relative_path(str(record.get("path") or ""))
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("published resource cache is unavailable")
        resources[relative_path] = path.read_bytes()
    return build_publication_bundle_from_manifest(
        manifest,
        resources,
        work_dir=work_dir,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deepresearch-flow pipeline Worker")
    parser.add_argument("--config", default=os.environ.get("PAPER_DB_CONFIG", "config.toml"))
    parser.add_argument(
        "--ocr-config", default=os.environ.get("PAPER_OCR_CONFIG", "ocr.toml")
    )
    parser.add_argument("--snapshot-db", default=os.environ.get("PAPER_DB_SNAPSHOT_DB"))
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
    snapshot_db = resolve_snapshot_db(config, args.snapshot_db)
    artifacts = ArtifactStore(
        config.work_dir,
        config.preview_root,
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
        snapshot_db=snapshot_db,
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
    "resolve_snapshot_db",
    "build_publication_bundle_from_state",
    "validate_pipeline_mounts",
    "validate_pipeline_environment",
]
