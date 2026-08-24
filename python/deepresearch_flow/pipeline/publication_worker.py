"""Lease-fenced publication worker."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from typing import Any

from .publication_models import (
    FormalStore,
    PublicationBundle,
    PublicationCancelled,
    PublicationError,
    PublicationWorkerResult,
)


class PublicationWorker:
    """Run queued publication and indexing retry jobs under one lease token."""

    def __init__(
        self,
        state: Any,
        snapshot_db: str | Path,
        formal_store: FormalStore,
        *,
        bundle_builder: Callable[[str], PublicationBundle],
        indexer: Callable[[PublicationBundle], Any] | None = None,
        worker_id: str = "pipeline-publisher",
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.state = state
        self.snapshot_db = Path(snapshot_db)
        self.formal_store = formal_store
        self.bundle_builder = bundle_builder
        self.indexer = indexer
        self.worker_id = worker_id
        self.stop_requested = stop_requested or (lambda: False)

    def run_job(self, job_id: str) -> PublicationWorkerResult:
        try:
            lease = self.state.acquire_lease(job_id, self.worker_id)
        except Exception as exc:
            return PublicationWorkerResult(job_id, "failed", error=str(exc))
        if lease is None:
            return PublicationWorkerResult(job_id, "busy")

        stop_heartbeat = Event()
        lease_lost = Event()
        active_guard: Any = None
        guard_active = Event()
        heartbeat_interval = max(
            0.1, float(getattr(self.state, "heartbeat_seconds", 30))
        )

        def maintain_lease() -> None:
            while not stop_heartbeat.wait(heartbeat_interval):
                if guard_active.is_set():
                    continue
                try:
                    self.state.heartbeat(job_id, lease.token)
                except Exception:
                    if guard_active.is_set():
                        continue
                    lease_lost.set()
                    return

        heartbeat_thread = Thread(
            target=maintain_lease,
            name=f"publication-heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat_thread.start()

        def check_lease() -> None:
            if lease_lost.is_set():
                raise PublicationError(f"publication lease lost for job {job_id}")
            if active_guard is not None:
                active_guard.assert_current()
                return
            valid = getattr(self.state, "lease_valid", None)
            if callable(valid) and not valid(job_id, lease.token):
                lease_lost.set()
                raise PublicationError(f"publication lease lost for job {job_id}")

        def cancellation_requested() -> bool:
            check_lease()
            return bool(self.state.cancel_requested(job_id))

        try:
            current = str(self.state.get_job(job_id)["status"])
            if current not in {"publish_queued", "indexing"}:
                try:
                    self.state.release_lease(job_id, lease.token)
                except Exception:
                    pass
                return PublicationWorkerResult(job_id, current)
            if current == "publish_queued":
                check_lease()
                self.state.transition(job_id, "publishing", lease.token)
            bundle = self.bundle_builder(job_id)
            if inspect.isawaitable(bundle):
                bundle = _await_sync(bundle)
            if not isinstance(bundle, PublicationBundle):
                raise TypeError("bundle_builder must return PublicationBundle")
            check_lease()
            manifest_recorder = getattr(self.state, "record_publication_manifest", None)
            if callable(manifest_recorder):
                from .publication import publication_manifest

                manifest_recorder(
                    job_id,
                    publication_manifest(bundle),
                    lease.token,
                )
            self.state.set_digests(job_id, bundle_digest=bundle.bundle_digest, lease_token=lease.token)

            @contextmanager
            def publication_guard() -> Any:
                nonlocal active_guard
                guard_method = getattr(self.state, "lease_guard", None)
                if not callable(guard_method):
                    yield None
                    return
                try:
                    with guard_method(
                        job_id,
                        lease.token,
                        owner=lease.owner,
                        reject_cancel=current == "publish_queued",
                    ) as guard:
                        active_guard = guard
                        guard_active.set()
                        yield guard
                finally:
                    guard_active.clear()
                    active_guard = None

            def index_after_snapshot(value: PublicationBundle) -> Any:
                if current == "publish_queued":
                    if active_guard is not None:
                        active_guard.transition("indexing")
                    else:
                        check_lease()
                        self.state.transition(job_id, "indexing", lease.token)
                if self.indexer is None:
                    return None
                return self.indexer(value)

            # Import at call time keeps publication.py facade import-compatible
            # while this worker remains independently testable.
            from .publication import publish_bundle

            publication = publish_bundle(
                bundle,
                self.snapshot_db,
                self.formal_store,
                indexer=index_after_snapshot,
                lease_check=check_lease,
                cancel_check=cancellation_requested if current == "publish_queued" else None,
                lease_guard=publication_guard,
            )
            final_status = "published_with_warning" if publication.index_warning else "published"
            check_lease()
            self.state.transition(job_id, final_status, lease.token)
            return PublicationWorkerResult(job_id, final_status, publication=publication)
        except PublicationCancelled as exc:
            try:
                status = self.state.step_boundary(job_id, lease.token)
            except Exception:
                status = "failed"
            return PublicationWorkerResult(job_id, status, error=str(exc))
        except Exception as exc:
            try:
                status = str(self.state.get_job(job_id)["status"])
                if status in {"publishing", "indexing"}:
                    self.state.transition(job_id, "failed", lease.token)
            except Exception:
                pass
            return PublicationWorkerResult(job_id, "failed", error=str(exc))
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval * 2))

    def run_once(self, job_ids: list[str] | None = None) -> list[PublicationWorkerResult]:
        ids = (
            list(job_ids)
            if job_ids is not None
            else self.state.list_job_ids({"publish_queued", "indexing"})
        )
        results: list[PublicationWorkerResult] = []
        for job_id in ids:
            if self.stop_requested():
                break
            results.append(self.run_job(job_id))
        return results

    run = run_once


def _await_sync(value: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError("publication callback returned awaitable in active event loop")


__all__ = ["PublicationWorker"]
