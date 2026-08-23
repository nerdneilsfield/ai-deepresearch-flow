"""Durable publication seam for the optional administrative pipeline.

The pipeline has two independent durability boundaries: formal static objects
and the Snapshot database.  This module keeps the boundary explicit.  A
bundle is immutable and content addressed, formal objects are written first,
and the receipt is committed with Snapshot metadata in one SQLite transaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from deepresearch_flow.paper.snapshot.admin import _InsertStats, _insert_paper_metadata
from deepresearch_flow.paper.snapshot.common import _open_rw_conn
from deepresearch_flow.paper.snapshot.schema import (
    init_snapshot_db,
    recompute_facet_counts,
    recompute_facet_edges,
    recompute_paper_index,
)
from deepresearch_flow.paper.snapshot.text import markdown_to_plain_text
from deepresearch_flow.paper.snapshot.identity import (
    build_paper_key_candidates,
    choose_preferred_key,
    paper_id_for_key,
)
from deepresearch_flow.paper.utils import stable_hash


class PublicationError(RuntimeError):
    """Publication failed before a formally published result was established."""


class PublicationConflict(PublicationError):
    """A job or paper already has a different durable publication identity."""


@runtime_checkable
class FormalStore(Protocol):
    """Minimal write-only interface shared by local and WebDAV stores."""

    def put(self, relative_path: str, data: bytes) -> None:
        """Write one relative formal object idempotently."""


@dataclass(frozen=True)
class PublicationResource:
    """One immutable content-addressed formal object."""

    relative_path: str
    content: bytes
    digest: str
    size: int
    media_type: str

    @property
    def path(self) -> str:
        """Compatibility alias used by callers that call paths ``path``."""
        return self.relative_path


@dataclass(frozen=True)
class PublicationBundle:
    """Immutable publication input and deterministic bundle identity."""

    job_id: str
    paper_id: str
    paper: Mapping[str, Any]
    bibtex: Mapping[str, Any]
    resource_map: Mapping[str, PublicationResource]
    references: Mapping[str, str]
    bundle_digest: str
    work_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper", _freeze(self.paper))
        object.__setattr__(self, "bibtex", _freeze(self.bibtex))
        object.__setattr__(self, "resource_map", MappingProxyType(dict(self.resource_map)))
        object.__setattr__(self, "references", MappingProxyType(dict(self.references)))

    @property
    def resources(self) -> tuple[PublicationResource, ...]:
        """Return resources in deterministic path order."""
        return tuple(self.resource_map[key] for key in sorted(self.resource_map))


@dataclass(frozen=True)
class PublicationResult:
    """Result of Snapshot receipt commit and optional indexing."""

    paper_id: str
    bundle_digest: str
    already_published: bool = False
    indexed: bool = False
    index_warning: str | None = None


@dataclass(frozen=True)
class PublicationWorkerResult:
    """Public result for one publication-worker attempt."""

    job_id: str
    status: str
    publication: PublicationResult | None = None
    error: str | None = None


class LocalFormalStore:
    """Atomic local formal store with content-addressed idempotency."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, relative_path: str, data: bytes) -> None:
        rel = _safe_relative_path(relative_path)
        destination = (self.root / rel).resolve()
        if not destination.is_relative_to(self.root):
            raise PublicationError("formal resource path escapes configured root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Content-addressed names make repeated writes safe.  Explicit
        # metadata names (summary/<paper>.json) are replaced atomically so a
        # manually regenerated preview cannot expose a partial file.
        fd, temporary_name = tempfile.mkstemp(prefix=".publication-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


class WebDavFormalStore:
    """Write-only WebDAV formal store.

    A successful PUT is the durability acknowledgement.  In particular this
    adapter deliberately does not call ``exists``/``HEAD`` before or after
    upload; content-addressed paths make retries safe without the extra round
    trip and some WebDAV servers do not implement HEAD consistently.
    """

    def __init__(self, storage: Any, *, prefix: str = ""):
        self.storage = storage
        self.prefix = _safe_relative_path(prefix) if prefix else ""

    def put(self, relative_path: str, data: bytes) -> None:
        rel = _safe_relative_path(relative_path)
        target = f"{self.prefix}/{rel}" if self.prefix else rel
        mkdir = getattr(self.storage, "mkdir", None)
        if callable(mkdir):
            parts = target.split("/")[:-1]
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                mkdir(current)
        self.storage.upload(target, data)


def build_publication_bundle(
    job_id: str,
    paper: Mapping[str, Any],
    *,
    bibtex: Mapping[str, Any] | None = None,
    resources: Mapping[str, bytes | bytearray | memoryview | str | Path | Any],
    work_dir: str | Path | None = None,
    translation_language: str = "en",
) -> PublicationBundle:
    """Build a deterministic bundle from normalized metadata and preview files.

    ``resources`` accepts semantic names (``pdf``, ``source_markdown``,
    ``summary_json``, ``translated_markdown``) or already relative formal
    paths.  The former become content-addressed paths; the latter are checked
    for safe containment and retained.  ``Path`` and Task-3 ``Artifact``
    values are read only from the configured work area when ``work_dir`` is
    supplied.
    """
    normalized_job_id = str(job_id).strip()
    if not normalized_job_id:
        raise ValueError("job_id must not be empty")
    normalized_paper = dict(_normalize_value(dict(paper)))
    title = str(normalized_paper.get("paper_title") or normalized_paper.get("title") or "").strip()
    if not title:
        raise ValueError("paper_title is required for publication")
    normalized_paper["paper_title"] = title
    if "title" in normalized_paper:
        normalized_paper["title"] = str(normalized_paper["title"]).strip()

    raw_work_dir = Path(work_dir).resolve() if work_dir is not None else None
    resource_map: dict[str, PublicationResource] = {}
    references: dict[str, str] = {}
    aliases: dict[str, str] = {}

    for alias, value in resources.items():
        name = str(alias).strip()
        if not name:
            raise ValueError("publication resource name must not be empty")
        content = _read_resource(value, raw_work_dir)
        digest = hashlib.sha256(content).hexdigest()
        relative_path = _resource_path(
            name,
            digest,
            paper_id=_paper_id_for(normalized_paper),
            translation_language=translation_language,
        )
        relative_path = _safe_relative_path(relative_path)
        media_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        resource_map[relative_path] = PublicationResource(
            relative_path=relative_path,
            content=content,
            digest=digest,
            size=len(content),
            media_type=media_type,
        )
        references[name] = relative_path
        aliases[name.casefold()] = relative_path

    paper_id = _paper_id_for(normalized_paper)
    if paper_id != _paper_id_for(normalized_paper, explicit_id=normalized_paper.get("paper_id")):
        paper_id = _paper_id_for(normalized_paper, explicit_id=normalized_paper.get("paper_id"))
    normalized_paper["paper_id"] = paper_id

    # Resource-derived hashes are the canonical values consumed by Snapshot
    # and the vector loader.  Existing source_hash remains a provenance key.
    pdf_ref = aliases.get("pdf") or aliases.get("preview_pdf")
    source_ref = aliases.get("source_markdown") or aliases.get("preview_source_md")
    translated_ref = aliases.get("translated_markdown") or aliases.get("preview_translated_md")
    if pdf_ref:
        normalized_paper["pdf_content_hash"] = resource_map[pdf_ref].digest
    if source_ref:
        normalized_paper["source_md_content_hash"] = resource_map[source_ref].digest
    if translated_ref:
        normalized_paper["translations"] = {
            str(translation_language).strip().lower(): resource_map[translated_ref].digest
        }

    summary_ref = aliases.get("summary_json") or aliases.get("preview_summary_json")
    if summary_ref and "templates" not in normalized_paper:
        try:
            summary = json.loads(resource_map[summary_ref].content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            summary = None
        if isinstance(summary, dict):
            normalized_paper["templates"] = {"simple": summary}

    normalized_bibtex = _normalize_bibtex(bibtex)
    bib_for_paper = _bibtex_paper_value(normalized_bibtex)
    if bib_for_paper is not None:
        normalized_paper["bibtex"] = bib_for_paper

    # Rebuild the resource paths once paper_id is known.  Semantic summary
    # paths include paper_id; the usual metadata-only identity does not.
    if summary_ref:
        expected_summary = f"summary/{paper_id}.json"
        if summary_ref != expected_summary:
            resource = resource_map.pop(summary_ref)
            resource = PublicationResource(
                relative_path=expected_summary,
                content=resource.content,
                digest=resource.digest,
                size=resource.size,
                media_type=resource.media_type,
            )
            resource_map[expected_summary] = resource
            for key, ref in list(references.items()):
                if ref == summary_ref:
                    references[key] = expected_summary

    payload = {
        "job_id": normalized_job_id,
        "paper_id": paper_id,
        "paper": _plain(normalized_paper),
        "bibtex": _plain(normalized_bibtex),
        "references": {key: references[key] for key in sorted(references)},
        "resources": [
            {
                "path": path,
                "digest": resource_map[path].digest,
                "size": resource_map[path].size,
                "media_type": resource_map[path].media_type,
            }
            for path in sorted(resource_map)
        ],
    }
    bundle_digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return PublicationBundle(
        job_id=normalized_job_id,
        paper_id=paper_id,
        paper=normalized_paper,
        bibtex=normalized_bibtex,
        resource_map=resource_map,
        references=references,
        bundle_digest=bundle_digest,
        work_dir=raw_work_dir,
    )


def build_bundle_from_preview(
    job_id: str,
    paper: Mapping[str, Any],
    preview: Any,
    *,
    bibtex: Mapping[str, Any] | None = None,
    work_dir: str | Path | None = None,
    translation_language: str = "en",
) -> PublicationBundle:
    """Build a bundle from Task-3 ``PreviewArtifacts`` or a compatible value."""
    resources = {
        "pdf": getattr(preview, "pdf"),
        "source_markdown": getattr(preview, "source_markdown"),
        "summary_json": getattr(preview, "summary_json"),
        "translated_markdown": getattr(preview, "translated_markdown"),
    }
    return build_publication_bundle(
        job_id,
        paper,
        bibtex=bibtex,
        resources=resources,
        work_dir=work_dir,
        translation_language=translation_language,
    )


def publish_bundle(
    bundle: PublicationBundle,
    snapshot_db: str | Path,
    formal_store: FormalStore,
    *,
    indexer: Callable[[PublicationBundle], Any] | None = None,
) -> PublicationResult:
    """Publish formal resources, commit one Snapshot receipt, then index.

    Formal writes happen before the first Snapshot schema write.  A matching
    receipt short-circuits both duplicate Snapshot insertion and formal PUTs,
    and still runs the supplied indexer, which makes embedding recovery an
    indexing-only operation.  A conflicting receipt is never overwritten.
    """
    db_path = Path(snapshot_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _SNAPSHOT_COMMIT_LOCK:
        existing = _read_receipt(db_path, bundle.job_id)
        if existing is not None:
            existing_digest, existing_paper_id = existing
            if existing_digest != bundle.bundle_digest:
                raise PublicationConflict(
                    f"publication receipt for job {bundle.job_id} has conflicting bundle digest"
                )
            return _run_indexer(
                bundle,
                paper_id=existing_paper_id,
                bundle_digest=existing_digest,
                already_published=True,
                indexer=indexer,
            )

        for resource in bundle.resources:
            try:
                formal_store.put(resource.relative_path, resource.content)
            except Exception as exc:
                raise PublicationError(
                    f"formal resource write failed for {resource.relative_path}: {exc}"
                ) from exc

        conn = _open_rw_conn(db_path)
        try:
            init_snapshot_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute(
                "SELECT bundle_digest,paper_id FROM pipeline_publication_receipt WHERE job_id=?",
                (bundle.job_id,),
            ).fetchone()
            if receipt is not None:
                existing_digest = str(receipt["bundle_digest"])
                existing_paper_id = str(receipt["paper_id"])
                conn.rollback()
                if existing_digest != bundle.bundle_digest:
                    raise PublicationConflict(
                        f"publication receipt for job {bundle.job_id} has conflicting bundle digest"
                    )
                return _run_indexer(
                    bundle,
                    paper_id=existing_paper_id,
                    bundle_digest=existing_digest,
                    already_published=True,
                    indexer=indexer,
                )

            stats = _InsertStats()
            try:
                _insert_paper_metadata(conn, dict(_plain(bundle.paper)), 0, stats, overwrite=False)
            except Exception as exc:
                raise PublicationError(f"Snapshot metadata commit failed: {exc}") from exc
            if stats.errors:
                raise PublicationError(f"Snapshot metadata commit failed: {stats.errors[0]['error']}")
            if stats.skipped or not stats.paper_ids:
                raise PublicationConflict(
                    f"paper {bundle.paper_id} already exists without matching publication receipt"
                )
            paper_id = str(stats.paper_ids[0])
            if paper_id != bundle.paper_id:
                raise PublicationConflict(
                    f"bundle paper_id {bundle.paper_id} does not match Snapshot paper_id {paper_id}"
                )
            _update_snapshot_static_references(conn, bundle, paper_id)
            conn.execute(
                "INSERT INTO pipeline_publication_receipt(job_id,bundle_digest,paper_id,published_at) VALUES(?,?,?,?)",
                (bundle.job_id, bundle.bundle_digest, paper_id, _now_iso()),
            )
            recompute_paper_index(conn)
            recompute_facet_counts(conn)
            recompute_facet_edges(conn)
            conn.commit()
        except PublicationError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise PublicationError(f"Snapshot commit failed: {exc}") from exc
        finally:
            conn.close()

        return _run_indexer(
            bundle,
            paper_id=bundle.paper_id,
            bundle_digest=bundle.bundle_digest,
            already_published=False,
            indexer=indexer,
        )


def queue_publication(state: Any, job_id: str, expected_revision: int) -> dict[str, Any]:
    """CAS ``review_ready`` and expected revision into ``publish_queued``."""
    method = getattr(state, "queue_publication", None)
    if not callable(method):
        raise TypeError("state does not expose queue_publication CAS seam")
    return method(job_id, expected_revision)


def validate_vector_index(
    vector_dir: str | Path,
    *,
    model: str,
    dimensions: int,
    normalized: bool,
    canonical_model: str | None = None,
    provider: str = "",
) -> None:
    """Reuse existing LanceDB metadata validation for publication indexing."""
    from deepresearch_flow.paper.vector_store import validate_index_meta

    validate_index_meta(
        Path(vector_dir),
        model=model,
        canonical_model=canonical_model,
        dimensions=dimensions,
        normalized=normalized,
        provider=provider,
    )


@dataclass(frozen=True)
class LanceDBIndexer:
    """Callable incremental indexer backed by existing embed pipeline."""

    config: Any
    snapshot_db: Path
    static_root: Path
    vector_dir: Path

    def __call__(self, bundle: PublicationBundle) -> None:
        del bundle
        from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

        result = run_embed_pipeline(
            config=self.config,
            snapshot_db=self.snapshot_db,
            static_export_dir=self.static_root,
            vector_dir=self.vector_dir,
        )
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError("LanceDBIndexer cannot run inside an active event loop")


class PublicationWorker:
    """Lease-fenced worker for ``publish_queued`` and indexing retries."""

    def __init__(
        self,
        state: Any,
        snapshot_db: str | Path,
        formal_store: FormalStore,
        *,
        bundle_builder: Callable[[str], PublicationBundle],
        indexer: Callable[[PublicationBundle], Any] | None = None,
        worker_id: str = "pipeline-publisher",
    ) -> None:
        self.state = state
        self.snapshot_db = Path(snapshot_db)
        self.formal_store = formal_store
        self.bundle_builder = bundle_builder
        self.indexer = indexer
        self.worker_id = worker_id

    def run_job(self, job_id: str) -> PublicationWorkerResult:
        try:
            lease = self.state.acquire_lease(job_id, self.worker_id)
        except Exception as exc:
            return PublicationWorkerResult(job_id, "failed", error=str(exc))
        if lease is None:
            return PublicationWorkerResult(job_id, "busy")
        try:
            current = str(self.state.get_job(job_id)["status"])
            if current not in {"publish_queued", "indexing"}:
                self.state.release_lease(job_id, lease.token)
                return PublicationWorkerResult(job_id, current)
            if current == "publish_queued":
                self.state.transition(job_id, "publishing", lease.token)
            bundle = self.bundle_builder(job_id)
            if inspect.isawaitable(bundle):
                bundle = _await_sync(bundle)
            if not isinstance(bundle, PublicationBundle):
                raise TypeError("bundle_builder must return PublicationBundle")
            self.state.set_digests(job_id, bundle_digest=bundle.bundle_digest, lease_token=lease.token)

            def index_after_snapshot(value: PublicationBundle) -> Any:
                if str(self.state.get_job(job_id)["status"]) == "publishing":
                    self.state.transition(job_id, "indexing", lease.token)
                if self.indexer is None:
                    return None
                return self.indexer(value)

            publication = publish_bundle(
                bundle,
                self.snapshot_db,
                self.formal_store,
                indexer=index_after_snapshot,
            )
            final_status = "published_with_warning" if publication.index_warning else "published"
            self.state.transition(job_id, final_status, lease.token)
            return PublicationWorkerResult(job_id, final_status, publication=publication)
        except Exception as exc:
            try:
                status = str(self.state.get_job(job_id)["status"])
                if status in {"publishing", "indexing"}:
                    self.state.transition(job_id, "failed", lease.token)
            except Exception:
                pass
            return PublicationWorkerResult(job_id, "failed", error=str(exc))

    def run_once(self, job_ids: list[str] | None = None) -> list[PublicationWorkerResult]:
        ids = (
            list(job_ids)
            if job_ids is not None
            else self.state.list_job_ids({"publish_queued", "indexing"})
        )
        return [self.run_job(job_id) for job_id in ids]

    run = run_once


_SNAPSHOT_COMMIT_LOCK = RLock()


def _run_indexer(
    bundle: PublicationBundle,
    *,
    paper_id: str,
    bundle_digest: str,
    already_published: bool,
    indexer: Callable[[PublicationBundle], Any] | None,
) -> PublicationResult:
    if indexer is None:
        return PublicationResult(paper_id, bundle_digest, already_published=already_published)
    try:
        result = indexer(bundle)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError("publication indexer returned awaitable in active event loop")
    except Exception as exc:
        return PublicationResult(
            paper_id,
            bundle_digest,
            already_published=already_published,
            indexed=False,
            index_warning=str(exc),
        )
    return PublicationResult(
        paper_id,
        bundle_digest,
        already_published=already_published,
        indexed=True,
    )


def _await_sync(value: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError("publication callback returned awaitable in active event loop")


def _read_receipt(db_path: Path, job_id: str) -> tuple[str, str] | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        row = conn.execute(
            "SELECT bundle_digest,paper_id FROM pipeline_publication_receipt WHERE job_id=?",
            (job_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # Existing pre-pipeline snapshots simply do not have receipt schema.
        return None
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass
    return None if row is None else (str(row["bundle_digest"]), str(row["paper_id"]))


def _update_snapshot_static_references(
    conn: sqlite3.Connection, bundle: PublicationBundle, paper_id: str
) -> None:
    pdf = _reference(bundle, "pdf", "preview_pdf")
    source = _reference(bundle, "source_markdown", "preview_source_md")
    if pdf:
        conn.execute(
            "UPDATE paper SET pdf_content_hash=? WHERE paper_id=?",
            (bundle.resource_map[pdf].digest, paper_id),
        )
    if source:
        conn.execute(
            "UPDATE paper SET source_md_content_hash=? WHERE paper_id=?",
            (bundle.resource_map[source].digest, paper_id),
        )
    source_text = ""
    if source:
        source_text = markdown_to_plain_text(bundle.resource_map[source].content.decode("utf-8", "replace"))
    translated_text = ""
    translated = _reference(bundle, "translated_markdown", "preview_translated_md")
    if translated:
        translated_text = markdown_to_plain_text(
            bundle.resource_map[translated].content.decode("utf-8", "replace")
        )
    conn.execute(
        "UPDATE paper_fts SET source=?,translated=? WHERE paper_id=?",
        (source_text, translated_text, paper_id),
    )


def _reference(bundle: PublicationBundle, *names: str) -> str | None:
    wanted = {name.casefold() for name in names}
    for name, relative_path in bundle.references.items():
        if str(name).casefold() in wanted:
            return relative_path
    return None


def _resource_path(
    name: str,
    digest: str,
    *,
    paper_id: str,
    translation_language: str,
) -> str:
    lower = name.casefold()
    suffix = Path(name).suffix.lower()
    if lower in {"pdf", "preview_pdf"}:
        return f"pdf/{digest}.pdf"
    if lower in {"source_markdown", "preview_source_md", "source_md", "markdown"}:
        return f"md/{digest}.md"
    if lower in {"translated_markdown", "preview_translated_md", "translation"}:
        language = _safe_component(translation_language.lower()) or "en"
        return f"md_translate/{language}/{digest}.md"
    if lower in {"summary_json", "preview_summary_json", "summary"}:
        return f"summary/{paper_id}.json"
    if "/" in name or "\\" in name:
        return name.replace("\\", "/")
    if suffix:
        return f"{name}/{digest}{suffix}"
    return f"objects/{digest}"


def _paper_id_for(paper: Mapping[str, Any], explicit_id: Any | None = None) -> str:
    explicit = str(explicit_id if explicit_id is not None else paper.get("paper_id") or "").strip()
    if explicit:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", explicit):
            raise ValueError("paper_id contains unsupported characters")
        return explicit
    candidates = build_paper_key_candidates(dict(paper))
    if not candidates:
        title = str(paper.get("paper_title") or "").strip()
        if not title:
            raise ValueError("paper identity requires paper_title or an identity field")
        return stable_hash(title)[:32]
    return paper_id_for_key(choose_preferred_key(candidates).paper_key)


def _normalize_bibtex(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"status": "not_provided"}
    payload = dict(_normalize_value(dict(value)))
    status = str(payload.get("status") or "matched").strip().lower()
    if status not in {"not_provided", "matched", "ambiguous", "unmatched", "manual"}:
        raise ValueError(f"unsupported BibTeX publication status: {status}")
    payload["status"] = status
    return payload


def _bibtex_paper_value(value: Mapping[str, Any]) -> dict[str, Any] | None:
    if value.get("status") == "not_provided":
        return None
    entry = value.get("entry")
    source: Mapping[str, Any] = entry if isinstance(entry, Mapping) else value
    fields = source.get("fields")
    normalized_fields = dict(fields) if isinstance(fields, Mapping) else {
        str(key): item
        for key, item in source.items()
        if key not in {"status", "entry", "raw", "key", "type"}
    }
    key = str(source.get("key") or value.get("entry_key") or "paper").strip()
    entry_type = str(source.get("type") or "misc").strip().lower()
    raw = str(source.get("raw") or value.get("raw") or "").strip()
    if not raw:
        fields_text = ",\n".join(
            f"  {name} = {{{str(item)}}}" for name, item in sorted(normalized_fields.items())
        )
        raw = f"@{entry_type}{{{key},\n{fields_text}\n}}"
    return {"raw": raw, "key": key, "type": entry_type, "fields": normalized_fields}


def _read_resource(value: Any, work_dir: Path | None) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    path = getattr(value, "path", value)
    if isinstance(path, (str, Path)):
        resolved = Path(path).resolve()
        if work_dir is not None and not resolved.is_relative_to(work_dir):
            raise ValueError("publication resource must be inside work directory")
        return resolved.read_bytes()
    raise TypeError("publication resources must be bytes or readable paths")


def _safe_relative_path(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    if "\x00" in normalized or not normalized or normalized.startswith("/"):
        raise ValueError("publication resource path must be relative and traversal-free")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or re.fullmatch(r"[A-Za-z]:", path.parts[0] if path.parts else "")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("publication resource path must be relative and traversal-free")
    return path.as_posix()


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "FormalStore",
    "LanceDBIndexer",
    "LocalFormalStore",
    "PublicationBundle",
    "PublicationConflict",
    "PublicationError",
    "PublicationResource",
    "PublicationResult",
    "PublicationWorker",
    "PublicationWorkerResult",
    "WebDavFormalStore",
    "build_bundle_from_preview",
    "build_publication_bundle",
    "publish_bundle",
    "queue_publication",
    "validate_vector_index",
]
