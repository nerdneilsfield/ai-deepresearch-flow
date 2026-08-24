"""Resumable fixed-step processing worker for administrative jobs.

This module is deliberately independent of HTTP.  Supervisor can call
``run_worker`` from a process or schedule one ``PipelineWorker.run_once`` loop.
Production entrypoints construct adapters from service configuration.  A
separately named adapter seam remains available to black-box tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .adapters import ProductionAdapters, build_production_adapters
from .artifacts import Artifact, ArtifactStore
from .config import PipelineConfig
from .matching import complete_batch
from .steps import (
    AdapterProtocol,
    PipelineAdapters,
    PreviewArtifacts,
    WorkerResult,
    as_bytes as _as_bytes,
    as_markdown as _as_markdown,
    as_summary as _as_summary,
    invoke as _invoke,
)
from .state import (
    PROCESSING_STEP_NAMES,
    Lease,
    LeaseError,
    PipelineState,
)
from .worker_support import error_type as _error_type
from .worker_support import retryable as _retryable
from .worker_support import safe_error as _safe_error


# Public alias makes the immutable sequence easy for Supervisor and tests to
# inspect without coupling them to the state-table compatibility constants.
FIXED_STEP_SEQUENCE = PROCESSING_STEP_NAMES
PROCESSING_STEPS = FIXED_STEP_SEQUENCE
STEP_SEQUENCE = FIXED_STEP_SEQUENCE
FIXED_STEPS = FIXED_STEP_SEQUENCE


class WorkerFailure(RuntimeError):
    """Public-safe worker failure carrying retryability."""

    def __init__(self, message: str, *, retryable: bool = True, error_type: str = "step_failed") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_type = error_type


class ModelInvalidation(WorkerFailure):
    def __init__(self, message: str = "job model selection is no longer valid") -> None:
        super().__init__(message, retryable=False, error_type="model_invalidated")


class ValidationFailure(WorkerFailure):
    def __init__(self, message: str = "validation retry limit exceeded") -> None:
        super().__init__(message, retryable=False, error_type="validation_failed")


class LeaseLost(WorkerFailure):
    def __init__(self) -> None:
        super().__init__("worker lease is no longer current", retryable=True, error_type="lease_lost")


class CancelledAtBoundary(WorkerFailure):
    def __init__(self) -> None:
        super().__init__("job cancellation observed at step boundary", retryable=False, error_type="cancelled")


class PipelineWorker:
    @classmethod
    def from_production_config(
        cls,
        config: PipelineConfig,
        state: PipelineState,
        artifacts: ArtifactStore,
        *,
        paper_config_path: str | Path | None = None,
        ocr_config_path: str | Path | None = None,
        worker_id: str | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> "PipelineWorker":
        """Construct worker with real OCR/provider seams for Supervisor."""
        adapters = build_production_adapters(
            paper_config_path=paper_config_path,
            ocr_config_path=ocr_config_path,
            staging_root=config.work_dir,
            extract_template=config.extract_templates[0]
            if config.extract_templates
            else None,
            output_language=config.translation_language,
            ocr_model_map=config.ocr_model_map,
        )
        return cls(
            config,
            state,
            artifacts,
            adapters=adapters,
            worker_id=worker_id,
            stop_requested=stop_requested,
        )

    def __init__(
        self,
        config: PipelineConfig,
        state: PipelineState,
        artifacts: ArtifactStore,
        *,
        adapters: PipelineAdapters | object | None = None,
        worker_id: str | None = None,
        concurrency: int | None = None,
        _test_supporting_models: Mapping[str, str] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.artifacts = artifacts
        self.adapters = adapters if adapters is not None else ProductionAdapters()
        self.worker_id = worker_id or f"worker-{id(self):x}"
        self.concurrency = max(1, int(concurrency or config.max_concurrent_jobs))
        self.stop_requested = stop_requested or (lambda: False)
        defaults = dict(config.supporting_models)
        if _test_supporting_models is not None and isinstance(self.adapters, ProductionAdapters):
            raise ValueError("supporting model overrides are test-only")
        defaults.update(dict(_test_supporting_models or {}))
        self.supporting_models = defaults
        if self.state.artifact_store is None:
            self.state.artifact_store = artifacts

    def run_job(self, job_id: str) -> WorkerResult:
        return asyncio.run(self.run_job_async(job_id))

    async def run_job_async(self, job_id: str) -> WorkerResult:
        lease = self.state.acquire_lease(job_id, self.worker_id)
        if lease is None:
            job = self.state.get_job(job_id)
            return WorkerResult(job_id, str(job["status"]))
        heartbeat_failed = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(lease, heartbeat_failed))
        try:
            try:
                return await self._process_job(lease, heartbeat_failed)
            except LeaseLost:
                return WorkerResult(job_id, "lease_lost", failed_step=None, error_type="lease_lost", retryable=True)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat_loop(self, lease: Lease, failed: asyncio.Event) -> None:
        interval = min(
            max(0.01, float(self.config.heartbeat_seconds)),
            max(0.01, float(self.config.lease_seconds) / 3.0),
        )
        while True:
            await asyncio.sleep(interval)
            try:
                self.state.heartbeat(lease.job_id, lease.token)
            except LeaseError:
                failed.set()
                return

    async def _process_job(self, lease: Lease, heartbeat_failed: asyncio.Event) -> WorkerResult:
        job_id, token = lease.job_id, lease.token
        try:
            self._validate_models(job_id)
            self.state.ensure_processing_steps(job_id, token)
            self.state.resume_step(job_id, token)
        except (ModelInvalidation, LeaseError) as exc:
            if isinstance(exc, LeaseError):
                raise LeaseLost() from exc
            return await self._fail(lease, "ocr", exc)

        try:
            context: dict[str, Any] = {"pdf": self._input_pdf(job_id)}
        except WorkerFailure as exc:
            return await self._fail(lease, "ocr", exc)
        for step in FIXED_STEP_SEQUENCE:
            if heartbeat_failed.is_set() or not self.state.lease_valid(job_id, token):
                raise LeaseLost()
            try:
                boundary = self.state.step_boundary(job_id, token)
            except LeaseError as exc:
                raise LeaseLost() from exc
            if boundary == "cancelled":
                return WorkerResult(job_id, "cancelled")
            if self.stop_requested():
                return self._requeue_after_shutdown(lease)

            existing = self._load_existing(job_id, step)
            if existing is not None:
                context[step] = self._decode_step(step, existing)
                if step == "summary_repair":
                    try:
                        self.state.record_job_summary(job_id, context[step], token)
                    except LeaseError as exc:
                        raise LeaseLost() from exc
                continue

            started = time.monotonic()
            try:
                output = await self._execute_step(step, context, job_id, token)
                if heartbeat_failed.is_set() or not self.state.lease_valid(job_id, token):
                    raise LeaseLost()
                if self.state.cancel_requested(job_id):
                    self._mark_cancelled(lease, step)
                    return WorkerResult(job_id, "cancelled", failed_step=step)
                artifact = self._promote_step(job_id, step, output)
                protected = output.protected if isinstance(output, PreviewArtifacts) else ()
                try:
                    committed = self.state.record_step_success_if_active(
                        job_id,
                        step,
                        token,
                        artifact=artifact,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        protected_artifacts=protected,
                    )
                except LeaseError as exc:
                    self._discard_uncommitted_output(artifact, protected)
                    raise LeaseLost() from exc
                if not committed:
                    self._discard_uncommitted_output(artifact, protected)
                    self._mark_cancelled(lease, step)
                    return WorkerResult(job_id, "cancelled", failed_step=step)
                context[step] = output
                if step == "summary_repair":
                    try:
                        self.state.record_job_summary(job_id, output, token)
                    except LeaseError as exc:
                        raise LeaseLost() from exc
                if self.state.step_boundary(job_id, token) == "cancelled":
                    return WorkerResult(job_id, "cancelled", failed_step=step)
                if self.stop_requested():
                    return self._requeue_after_shutdown(lease)
            except CancelledAtBoundary:
                self._mark_cancelled(lease, step)
                return WorkerResult(job_id, "cancelled", failed_step=step)
            except LeaseError as exc:
                raise LeaseLost() from exc
            except LeaseLost:
                raise
            except Exception as exc:
                failure = exc if isinstance(exc, WorkerFailure) else WorkerFailure(_safe_error(exc), retryable=_retryable(exc), error_type=_error_type(exc))
                return await self._fail(lease, step, failure, duration_ms=int((time.monotonic() - started) * 1000))

        preview = context.get("preview")
        if not isinstance(preview, PreviewArtifacts):
            raise WorkerFailure("preview output unavailable", retryable=True)
        job = self.state.get_job(job_id)
        batch_id = job.get("batch_id")
        if batch_id and self.state.list_bibtex_entries(str(batch_id)) and not self.state.batch_summary_ready(str(batch_id)):
            try:
                self.state.transition(job_id, "batch_waiting", token)
            except LeaseError as exc:
                raise LeaseLost() from exc
            return WorkerResult(job_id, "batch_waiting")
        status, bibtex_status = self._completion_status(job_id, context.get("summary_repair"), token)
        if bibtex_status != preview.bibtex_status:
            preview = PreviewArtifacts(
                preview.pdf,
                preview.source_markdown,
                preview.summary_json,
                preview.translated_markdown,
                preview.digest,
                bibtex_status,
                preview.protected,
            )
            context["preview"] = preview
        if self.state.cancel_requested(job_id):
            self._mark_cancelled(lease, "preview")
            return WorkerResult(job_id, "cancelled", failed_step="preview")
        self.state.set_digests(job_id, preview_digest=preview.digest, bundle_digest=preview.digest, lease_token=token)
        try:
            current_status = str(self.state.get_job(job_id)["status"])
            if current_status == status:
                self.state.release_lease(job_id, token)
                return WorkerResult(job_id, status, preview.digest, preview)
            self.state.transition(job_id, status, token)
        except LeaseError as exc:
            raise LeaseLost() from exc
        return WorkerResult(job_id, status, preview.digest, preview)

    def _validate_models(self, job_id: str) -> None:
        job = self.state.get_job(job_id)
        fingerprint = str(job.get("config_fingerprint") or "")
        if fingerprint and fingerprint != self.config.fingerprint():
            raise ModelInvalidation()
        selected = job.get("selected_models") or {}
        groups = {"ocr": self.config.ocr, "extract": self.config.extract, "translate": self.config.translate}
        for name, group in groups.items():
            if not isinstance(selected.get(name), str) or selected[name] not in group.allowlist:
                raise ModelInvalidation()

    def _input_pdf(self, job_id: str) -> Path:
        artifact = self.artifacts.resolve(job_id, "pdf")
        if artifact is None:
            raise WorkerFailure("input PDF artifact is missing", retryable=False, error_type="input_missing")
        try:
            expected = self.state.get_job_input(job_id)
        except KeyError as exc:
            raise WorkerFailure("input PDF artifact is missing", retryable=False, error_type="input_missing") from exc
        content = artifact.path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(expected["digest"]) or len(content) != int(expected["size"]):
            raise WorkerFailure(
                "input PDF does not match uploaded source",
                retryable=False,
                error_type="input_tampered",
            )
        return artifact.path

    def _load_existing(self, job_id: str, step: str) -> Any | None:
        metadata = self.state.step_artifact(job_id, step)
        if metadata is None:
            return None
        artifact = self.artifacts.resolve(job_id, step)
        if artifact is None or artifact.digest != metadata["artifact_digest"] or artifact.size != metadata["artifact_size"]:
            return None
        if step == "preview":
            return self._load_preview(job_id)
        return artifact.path.read_bytes()

    def _load_preview(self, job_id: str) -> PreviewArtifacts | None:
        names = ("preview_pdf", "preview_source_md", "preview_summary_json", "preview_translated_md")
        resolved: list[Path] = []
        digests: list[str] = []
        for name in names:
            metadata = self.state.artifact_metadata(job_id, name)
            if metadata is None:
                return None
            path = Path(str(metadata["path"]))
            if not path.is_file():
                return None
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != metadata["digest"] or len(content) != metadata["size"]:
                return None
            resolved.append(path)
            digests.append(digest)
        preview_digest = hashlib.sha256(b"".join(digest.encode("ascii") for digest in digests)).hexdigest()
        return PreviewArtifacts(
            pdf=resolved[0],
            source_markdown=resolved[1],
            summary_json=resolved[2],
            translated_markdown=resolved[3],
            digest=preview_digest,
            bibtex_status="not_provided",
        )

    @staticmethod
    def _decode_step(step: str, raw: Any) -> Any:
        if step == "preview" and isinstance(raw, PreviewArtifacts):
            return raw
        if step in {"extract", "validation", "summary_repair"}:
            return _as_summary(raw)
        if step == "preview":
            return None
        return _as_markdown(raw)

    def _model_key(self, job_id: str, step: str) -> str | None:
        selected = self.state.get_job(job_id).get("selected_models") or {}
        if step in {"ocr", "extract", "translate"} and selected.get(step):
            return str(selected[step])
        aliases = {
            "source_repair": "repair",
            "math_repair": "repair",
            "organize": "markdown",
            "validation": "validation",
            "summary_repair": "summary",
            "translation_repair": "translation_repair",
        }
        return self.supporting_models.get(step) or self.supporting_models.get(aliases.get(step, ""))

    async def _execute_step(self, step: str, context: dict[str, Any], job_id: str, token: str) -> Any:
        if step == "ocr":
            func = self._adapter("ocr")
            return _as_markdown(await _invoke(func, context["pdf"], self._model_key(job_id, step), model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "source_repair":
            return _as_markdown(await _invoke(self._adapter(step), context["ocr"], model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "math_repair":
            return _as_markdown(await _invoke(self._adapter(step), context["source_repair"], model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "organize":
            return _as_markdown(await _invoke(self._adapter(step), context["math_repair"], model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "extract":
            return _as_summary(
                await _invoke(
                    self._adapter(step),
                    context["organize"],
                    self._model_key(job_id, step),
                    model_key=self._model_key(job_id, step),
                    templates=self.config.extract_templates,
                    job_id=job_id,
                )
            )
        if step == "validation":
            summary = context["extract"]
            limit = max(1, int(self.config.validation_retry_limit))
            last = "validation rejected summary"
            for attempt in range(limit + 1):
                result = await _invoke(self._adapter(step), summary, attempt=attempt, model_key=self._model_key(job_id, step), supporting_models=self.supporting_models)
                valid = bool(result) if not isinstance(result, Mapping) else bool(result.get("valid", False))
                if valid:
                    return summary
                last = str(result.get("message", last)) if isinstance(result, Mapping) else last
                if attempt < limit:
                    try:
                        self.state.record_step_attempt(job_id, step, token, "retrying", error=_safe_error(ValueError(last)), error_type="validation_retry", retryable=True)
                    except LeaseError as exc:
                        raise LeaseLost() from exc
            raise ValidationFailure(last)
        if step == "summary_repair":
            return _as_summary(await _invoke(self._adapter(step), context["validation"], model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "translate":
            return _as_markdown(await _invoke(self._adapter(step), context["organize"], self._model_key(job_id, step), model_key=self._model_key(job_id, step), target_language=self.config.translation_language))
        if step == "translation_repair":
            return _as_markdown(await _invoke(self._adapter(step), context["translate"], model_key=self._model_key(job_id, step), supporting_models=self.supporting_models))
        if step == "preview":
            return self._make_preview(job_id, context, token)
        raise ValueError(f"unknown processing step: {step}")

    def _adapter(self, name: str) -> Callable[..., Any]:
        lookups = {
            "validation": ("validation", "validate"),
            "organize": ("organize", "markdown_organize"),
        }.get(name, (name,))
        func = next((getattr(self.adapters, lookup, None) for lookup in lookups if callable(getattr(self.adapters, lookup, None))), None)
        if not callable(func):
            raise WorkerFailure(f"adapter unavailable: {name}", retryable=False, error_type="adapter_unavailable")
        return func

    def _promote_step(self, job_id: str, step: str, output: Any) -> Artifact:
        pending = self.artifacts.begin(job_id, step)
        try:
            if isinstance(output, PreviewArtifacts):
                payload = json.dumps({"digest": output.digest, "bibtex_status": output.bibtex_status}, sort_keys=True).encode("utf-8")
            else:
                payload = _as_bytes(output)
            pending.write(payload)
            return pending.promote()
        except BaseException:
            pending.abort()
            raise

    def _make_preview(self, job_id: str, context: dict[str, Any], token: str) -> PreviewArtifacts:
        if not self.state.lease_valid(job_id, token):
            raise LeaseLost()
        if self.state.cancel_requested(job_id):
            raise CancelledAtBoundary()
        pdf = self._input_pdf(job_id).read_bytes()
        source = _as_bytes(context["organize"])
        summary = _as_bytes(context["summary_repair"])
        translated = _as_bytes(context["translation_repair"])
        names = ("preview_pdf", "preview_source_md", "preview_summary_json", "preview_translated_md")
        contents = (pdf, source, summary, translated)
        try:
            protected = [self.artifacts.protect(job_id, name, value) for name, value in zip(names, contents, strict=True)]
        except BaseException:
            for artifact in locals().get("protected", []):
                self.artifacts.discard_artifact(artifact)
            raise
        digest = hashlib.sha256(b"".join(artifact.digest.encode("ascii") for artifact in protected)).hexdigest()
        return PreviewArtifacts(
            protected[0].path,
            protected[1].path,
            protected[2].path,
            protected[3].path,
            digest,
            "not_provided",
            tuple(protected),
        )

    def _completion_status(
        self,
        job_id: str,
        summary: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[str, str]:
        try:
            return complete_batch(self.state, job_id, token)
        except LeaseError as exc:
            raise LeaseLost() from exc

    def _discard_uncommitted_output(
        self, artifact: Artifact, protected: tuple[Artifact, ...]
    ) -> None:
        self.artifacts.validate_artifact(artifact, artifact.job_id, artifact.kind)
        artifact.path.unlink(missing_ok=True)
        for protected_artifact in protected:
            self.artifacts.discard_artifact(protected_artifact)

    async def _fail(self, lease: Lease, step: str, exc: BaseException, *, duration_ms: int | None = None) -> WorkerResult:
        token = lease.token
        safe = _safe_error(exc)
        retryable = _retryable(exc)
        error_type = _error_type(exc)
        try:
            self.state.record_step_attempt(lease.job_id, step, token, "failed", error=safe, error_type=error_type, retryable=retryable, duration_ms=duration_ms)
            self.state.transition(lease.job_id, "failed", token)
        except LeaseError as lease_exc:
            raise LeaseLost() from lease_exc
        return WorkerResult(lease.job_id, "failed", failed_step=step, error_type=error_type, retryable=retryable)

    def _abort_cancel(self, lease: Lease) -> None:
        try:
            self.state.step_boundary(lease.job_id, lease.token)
        except LeaseError as exc:
            raise LeaseLost() from exc

    def _requeue_after_shutdown(self, lease: Lease) -> WorkerResult:
        try:
            self.state.requeue_after_shutdown(lease.job_id, lease.token)
        except LeaseError as exc:
            raise LeaseLost() from exc
        return WorkerResult(
            lease.job_id,
            "queued",
            error_type="shutdown",
            retryable=True,
        )

    def _mark_cancelled(self, lease: Lease, step: str) -> None:
        try:
            self.state.record_step_attempt(
                lease.job_id,
                step,
                lease.token,
                "cancelled",
                error="job cancellation observed at step boundary",
                error_type="cancelled",
                retryable=False,
            )
            self._abort_cancel(lease)
        except LeaseError as exc:
            raise LeaseLost() from exc

    async def run_once_async(self, job_ids: list[str] | None = None) -> list[WorkerResult]:
        ids = self.state.list_job_ids({"queued", "failed", "batch_waiting"}) if job_ids is None else list(job_ids)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(job_id: str) -> WorkerResult:
            async with semaphore:
                return await self.run_job_async(job_id)

        if not ids:
            return []
        latest: dict[str, WorkerResult] = {}
        pending = ids
        for _ in range(len(ids) + 1):
            if not pending or self.stop_requested():
                break
            retry: list[str] = []
            for start in range(0, len(pending), self.concurrency):
                if self.stop_requested():
                    break
                batch = pending[start : start + self.concurrency]
                results = list(await asyncio.gather(*(run_one(job_id) for job_id in batch)))
                latest.update({result.job_id: result for result in results})
                retry.extend(result.job_id for result in results if result.status == "batch_waiting")
            pending = retry
            if not pending:
                break
        return [latest[job_id] for job_id in ids if job_id in latest]

    def run_once(self, job_ids: list[str] | None = None) -> list[WorkerResult]:
        return asyncio.run(self.run_once_async(job_ids))

    run = run_once


def run_test_worker(config: PipelineConfig, state: PipelineState, artifacts: ArtifactStore, *, adapters: PipelineAdapters | object | None = None, worker_id: str | None = None, job_ids: list[str] | None = None) -> list[WorkerResult]:
    """Black-box test seam with explicit adapter injection."""
    return PipelineWorker(config, state, artifacts, adapters=adapters, worker_id=worker_id).run_once(job_ids)


def run_worker(config: PipelineConfig, state: PipelineState, artifacts: ArtifactStore, *, paper_config_path: str | Path, ocr_config_path: str | Path, worker_id: str | None = None, job_ids: list[str] | None = None) -> list[WorkerResult]:
    """Canonical Supervisor entrypoint; always constructs real adapters."""
    return run_production_worker(config, state, artifacts, paper_config_path=paper_config_path, ocr_config_path=ocr_config_path, worker_id=worker_id, job_ids=job_ids)


def run_production_worker(config: PipelineConfig, state: PipelineState, artifacts: ArtifactStore, *, paper_config_path: str | Path, ocr_config_path: str | Path, worker_id: str | None = None, job_ids: list[str] | None = None) -> list[WorkerResult]:
    """Supervisor entrypoint building real adapters from config paths."""
    worker = PipelineWorker.from_production_config(config, state, artifacts, paper_config_path=paper_config_path, ocr_config_path=ocr_config_path, worker_id=worker_id)
    return worker.run_once(job_ids)


worker_entrypoint = run_worker
Worker = PipelineWorker
run_processing_worker = run_worker


__all__ = [
    "FIXED_STEP_SEQUENCE",
    "PROCESSING_STEPS",
    "STEP_SEQUENCE",
    "FIXED_STEPS",
    "PipelineAdapters",
    "ProductionAdapters",
    "build_production_adapters",
    "PreviewArtifacts",
    "WorkerResult",
    "WorkerFailure",
    "PipelineWorker",
    "Worker",
    "run_worker",
    "run_test_worker",
    "run_production_worker",
    "run_processing_worker",
    "worker_entrypoint",
]
