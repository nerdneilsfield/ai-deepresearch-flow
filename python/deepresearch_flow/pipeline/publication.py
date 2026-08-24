"""Durable publication seam for the optional administrative pipeline.

The pipeline has two independent durability boundaries: formal static objects
and the Snapshot database.  This module keeps the boundary explicit.  A
bundle is immutable and content addressed; one serialized Snapshot transaction
reserves paper identity before formal writes and commits its receipt only after
all resources succeed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import nullcontext
import hashlib
import inspect
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from deepresearch_flow.paper.snapshot.publication import (
    InsertStats,
    insert_paper_metadata,
    open_snapshot_connection,
)
from deepresearch_flow.paper.snapshot.schema import (
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
from .publication_models import (
    FormalStore,
    PublicationBundle,
    PublicationCancelled,
    PublicationConflict,
    PublicationError,
    PublicationResource,
    PublicationResult,
    PublicationWorkerResult,
    plain,
)
from .publication_store import (
    LocalFormalStore,
    MirroredFormalStore,
    PUBLICATION_SERIALIZATION_LOCK,
    WebDavFormalStore,
    safe_relative_path,
)
from .publication_indexing import LanceDBIndexer
from .publication_worker import PublicationWorker


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
    paths.  The former become content-addressed paths; the latter must already
    end in their verified content digest.  ``Path`` and Task-3 ``Artifact``
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

    normalized_bibtex = _normalize_bibtex(bibtex)
    if normalized_bibtex.get("status") == "not_provided":
        # A preview may carry metadata copied from an earlier run.  An
        # explicit no-BibTeX decision is authoritative and must clear it
        # before Snapshot identity and insertion are prepared.
        normalized_paper.pop("bibtex", None)
        normalized_paper.pop("bibtex_raw", None)

    paper_id = _paper_id_for(normalized_paper)
    normalized_paper["paper_id"] = paper_id

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
        relative_path = safe_relative_path(relative_path)
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

    bib_for_paper = _bibtex_paper_value(normalized_bibtex)
    if bib_for_paper is not None:
        normalized_paper["bibtex"] = bib_for_paper

    # Snapshot's publication rows point at immutable content-addressed summary
    # resources.  The embedding loader retains legacy stable-path fallback.
    if summary_ref:
        summary_resource = resource_map.pop(summary_ref)
        payloads = _summary_template_payloads(
            normalized_paper.get("templates"), summary_resource.content
        )
        if payloads:
            preferred_tag = _preferred_summary_tag(normalized_paper, payloads)
            for template_tag, payload in payloads.items():
                content = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                digest = hashlib.sha256(content).hexdigest()
                path = f"summary/{paper_id}/{template_tag}/{digest}.json"
                resource_map[path] = PublicationResource(
                    relative_path=path,
                    content=content,
                    digest=digest,
                    size=len(content),
                    media_type="application/json",
                )
                references[f"summary:{template_tag}"] = path
            references["summary_json"] = references[f"summary:{preferred_tag}"]
            aliases["summary_json"] = references["summary_json"]
        else:
            # Keep malformed preview payload available for review, but put it
            # on immutable paper/template layout rather than a replaceable
            # top-level metadata path.
            path = f"summary/{paper_id}/simple/{summary_resource.digest}.json"
            resource = PublicationResource(
                relative_path=path,
                content=summary_resource.content,
                digest=summary_resource.digest,
                size=summary_resource.size,
                media_type="application/json",
            )
            resource_map[path] = resource
            references["summary_json"] = path
            aliases["summary_json"] = path
    elif isinstance(normalized_paper.get("templates"), Mapping):
        payloads = _summary_template_payloads(normalized_paper.get("templates"), b"")
        if payloads:
            preferred_tag = _preferred_summary_tag(normalized_paper, payloads)
            for template_tag, payload in payloads.items():
                content = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                digest = hashlib.sha256(content).hexdigest()
                path = f"summary/{paper_id}/{template_tag}/{digest}.json"
                resource_map[path] = PublicationResource(
                    relative_path=path,
                    content=content,
                    digest=digest,
                    size=len(content),
                    media_type="application/json",
                )
                references[f"summary:{template_tag}"] = path
            references["summary_json"] = references[f"summary:{preferred_tag}"]
            aliases["summary_json"] = references["summary_json"]

    _validate_publication_resources(resource_map, references)

    payload = _publication_manifest_payload(
        normalized_job_id,
        paper_id,
        normalized_paper,
        normalized_bibtex,
        references,
        resource_map,
    )
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


def publication_manifest(bundle: PublicationBundle) -> dict[str, Any]:
    """Return metadata required to reconstruct one published bundle.

    Only paper metadata, references, and content-addressed resource metadata
    are returned.  Resource bytes are intentionally excluded.
    """
    payload = _publication_manifest_payload(
        bundle.job_id,
        bundle.paper_id,
        bundle.paper,
        bundle.bibtex,
        bundle.references,
        bundle.resource_map,
    )
    return {
        "version": 1,
        **payload,
        "bundle_digest": bundle.bundle_digest,
    }


def build_publication_bundle_from_manifest(
    manifest: Mapping[str, Any],
    resources: Mapping[str, bytes],
    *,
    work_dir: str | Path | None = None,
) -> PublicationBundle:
    """Reconstruct immutable bundle from metadata and formal cached bytes."""
    if int(manifest.get("version", 0)) != 1:
        raise ValueError("unsupported publication manifest version")
    job_id = str(manifest.get("job_id") or "").strip()
    paper_id = str(manifest.get("paper_id") or "").strip()
    paper = manifest.get("paper")
    bibtex = manifest.get("bibtex")
    references = manifest.get("references")
    records = manifest.get("resources")
    bundle_digest = str(manifest.get("bundle_digest") or "").strip()
    if (
        not job_id
        or not paper_id
        or not isinstance(paper, Mapping)
        or not isinstance(bibtex, Mapping)
        or not isinstance(references, Mapping)
        or not isinstance(records, list)
        or not bundle_digest
    ):
        raise ValueError("publication manifest is incomplete")
    resource_map: dict[str, PublicationResource] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("publication manifest resource is invalid")
        relative_path = safe_relative_path(str(record.get("path") or ""))
        digest = str(record.get("digest") or "")
        size = record.get("size")
        media_type = str(record.get("media_type") or "application/octet-stream")
        if not isinstance(size, int) or isinstance(size, bool) or not digest:
            raise ValueError("publication manifest resource metadata is invalid")
        if relative_path not in resources:
            raise PublicationError(f"published resource cache is missing {relative_path}")
        content = resources[relative_path]
        if not isinstance(content, bytes):
            raise ValueError("published resource cache must contain bytes")
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != digest or len(content) != size:
            raise PublicationError(f"published resource cache is corrupt for {relative_path}")
        resource_map[relative_path] = PublicationResource(
            relative_path=relative_path,
            content=content,
            digest=digest,
            size=size,
            media_type=media_type,
        )
    if set(resources) != set(resource_map):
        raise ValueError("published resource cache contains unexpected resources")
    normalized_references = {str(key): str(value) for key, value in references.items()}
    _validate_publication_resources(resource_map, normalized_references)
    payload = _publication_manifest_payload(
        job_id,
        paper_id,
        paper,
        bibtex,
        normalized_references,
        resource_map,
    )
    expected_digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if expected_digest != bundle_digest:
        raise PublicationConflict("publication manifest bundle digest does not match")
    return PublicationBundle(
        job_id=job_id,
        paper_id=paper_id,
        paper=paper,
        bibtex=bibtex,
        resource_map=resource_map,
        references=normalized_references,
        bundle_digest=bundle_digest,
        work_dir=Path(work_dir).resolve() if work_dir is not None else None,
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
    lease_check: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    lease_guard: Callable[[], Any] | None = None,
) -> PublicationResult:
    """Publish formal resources, commit one Snapshot receipt, then index.

    A supplied queue guard is acquired before formal writes and held through
    Snapshot commit and indexing.  Formal writes and the receipt transaction
    share one serialization primitive, so reference GC cannot cross that
    boundary.  Content-addressed orphans remain recoverable after failures.
    """
    _validate_publication_resources(bundle.resource_map, bundle.references)
    db_path = Path(snapshot_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _check_lease(lease_check)
    receipt_hint = _read_publication_receipt(db_path, bundle.job_id)
    if receipt_hint is not None and receipt_hint[0] != bundle.bundle_digest:
        raise PublicationConflict(
            f"publication receipt for job {bundle.job_id} has conflicting bundle digest"
        )
    guard_context = lease_guard() if lease_guard is not None else nullcontext()
    already_published = False
    published_paper_id = bundle.paper_id
    published_bundle_digest = bundle.bundle_digest
    lock_held = False
    _SNAPSHOT_COMMIT_LOCK.acquire()
    lock_held = True
    try:
        if receipt_hint is None:
            _check_cancel(cancel_check)
            for resource in bundle.resources:
                _check_lease(lease_check)
                _check_cancel(cancel_check)
                try:
                    formal_store.put(resource.relative_path, resource.content)
                except PublicationConflict:
                    raise
                except Exception as exc:
                    raise PublicationError(
                        f"formal resource write failed for {resource.relative_path}: {exc}"
                    ) from exc

        with guard_context as guard:
            _guard_check(guard, lease_check)
            conn = open_snapshot_connection(db_path)
            try:
                _guard_check(guard, lease_check)
                conn.execute("BEGIN IMMEDIATE")
                _guard_check(guard, lease_check)
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
                    already_published = True
                    published_paper_id = existing_paper_id
                    published_bundle_digest = existing_digest
                else:
                    if receipt_hint is not None:
                        raise PublicationError(
                            f"publication receipt for job {bundle.job_id} disappeared"
                        )
                    _guard_cancel(guard, cancel_check)

                if not already_published:
                    duplicate = conn.execute(
                        "SELECT paper_id FROM paper WHERE paper_id=?", (bundle.paper_id,)
                    ).fetchone()
                    if duplicate is not None:
                        raise PublicationConflict(
                            f"paper {bundle.paper_id} already exists without matching publication receipt"
                        )

                if not already_published:
                    _guard_cancel(guard, cancel_check)
                    stats = InsertStats()
                    try:
                        insert_paper_metadata(
                            conn, dict(plain(bundle.paper)), 0, stats, overwrite=False
                        )
                    except Exception as exc:
                        raise PublicationError(f"Snapshot metadata commit failed: {exc}") from exc
                    if stats.errors:
                        raise PublicationError(
                            f"Snapshot metadata commit failed: {stats.errors[0]['error']}"
                        )
                    if stats.skipped or not stats.paper_ids:
                        raise PublicationConflict(
                            f"paper {bundle.paper_id} already exists without matching publication receipt"
                        )
                    paper_id = str(stats.paper_ids[0])
                    if paper_id != bundle.paper_id:
                        raise PublicationConflict(
                            f"bundle paper_id {bundle.paper_id} does not match Snapshot paper_id {paper_id}"
                        )
                    _update_snapshot_summary_references(conn, bundle, paper_id)
                    _update_snapshot_static_references(conn, bundle, paper_id)
                    _guard_check(guard, lease_check)
                    _guard_cancel(guard, cancel_check)
                    conn.execute(
                        "INSERT INTO pipeline_publication_receipt(job_id,bundle_digest,paper_id,published_at) VALUES(?,?,?,?)",
                        (bundle.job_id, bundle.bundle_digest, paper_id, _now_iso()),
                    )
                    recompute_paper_index(conn)
                    recompute_facet_counts(conn)
                    recompute_facet_edges(conn)
                    _guard_check(guard, lease_check)
                    conn.commit()
            except PublicationCancelled:
                conn.rollback()
                raise
            except PublicationConflict:
                conn.rollback()
                raise
            except PublicationError:
                conn.rollback()
                raise
            except Exception as exc:
                conn.rollback()
                raise PublicationError(f"Snapshot commit failed: {exc}") from exc
            finally:
                conn.close()

            # GC must not cross formal writes and Snapshot receipt commit, but
            # indexing may proceed after that shared lock is released.
            _SNAPSHOT_COMMIT_LOCK.release()
            lock_held = False
            return _run_indexer(
                bundle,
                paper_id=published_paper_id,
                bundle_digest=published_bundle_digest,
                already_published=already_published,
                indexer=indexer,
            )
    finally:
        if lock_held:
            _SNAPSHOT_COMMIT_LOCK.release()


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


_SNAPSHOT_COMMIT_LOCK = PUBLICATION_SERIALIZATION_LOCK


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


def _read_publication_receipt(
    db_path: Path, job_id: str
) -> tuple[str, str] | None:
    """Read receipt hint without mutating Snapshot or taking commit lock."""
    if not db_path.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        row = connection.execute(
            "SELECT bundle_digest,paper_id FROM pipeline_publication_receipt WHERE job_id=?",
            (job_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise PublicationError(f"publication receipt precheck failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return None
    return str(row["bundle_digest"]), str(row["paper_id"])


def _validate_publication_resources(
    resource_map: Mapping[str, PublicationResource],
    references: Mapping[str, str],
) -> None:
    """Reject paths that cannot be immutable without a WebDAV HEAD."""
    map_paths = {str(path) for path in resource_map}
    for map_path, resource in resource_map.items():
        if not isinstance(resource, PublicationResource):
            raise ValueError("publication resource has invalid type")
        try:
            normalized_path = safe_relative_path(resource.relative_path)
        except (TypeError, ValueError) as exc:
            raise ValueError("publication resource path is unsafe") from exc
        if normalized_path != str(map_path):
            raise ValueError("publication resource map path does not match resource path")
        if not isinstance(resource.content, bytes):
            raise ValueError("publication resource content must be bytes")
        digest = hashlib.sha256(resource.content).hexdigest()
        if resource.digest != digest or resource.size != len(resource.content):
            raise ValueError("publication resource digest does not match content")
        if PurePosixPath(normalized_path).stem != digest:
            raise ValueError(
                "publication resource path must be content-addressed by digest"
            )
    missing = sorted(
        str(path) for path in references.values() if str(path) not in map_paths
    )
    if missing:
        raise ValueError("publication resource reference is missing from resource map")


def _publication_manifest_payload(
    job_id: str,
    paper_id: str,
    paper: Mapping[str, Any],
    bibtex: Mapping[str, Any],
    references: Mapping[str, str],
    resource_map: Mapping[str, PublicationResource],
) -> dict[str, Any]:
    """Build stable metadata payload used for bundle identity and recovery."""
    return {
        "job_id": str(job_id),
        "paper_id": str(paper_id),
        "paper": plain(paper),
        "bibtex": plain(bibtex),
        "references": {str(key): str(references[key]) for key in sorted(references)},
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


def _check_lease(check: Callable[[], None] | None) -> None:
    if check is not None:
        try:
            check()
        except PublicationError:
            raise
        except Exception as exc:
            raise PublicationError(f"publication lease lost: {exc}") from exc


def _guard_check(guard: Any, check: Callable[[], None] | None) -> None:
    if guard is not None:
        guard.assert_current()
    else:
        _check_lease(check)


def _check_cancel(check: Callable[[], bool] | None) -> None:
    if check is not None and check():
        raise PublicationCancelled("publication cancelled before Snapshot receipt")


def _guard_cancel(guard: Any, check: Callable[[], bool] | None) -> None:
    if guard is not None:
        if bool(getattr(guard, "reject_cancel", False)) and guard.cancel_requested:
            raise PublicationCancelled("publication cancelled before Snapshot receipt")
    else:
        _check_cancel(check)


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


def _update_snapshot_summary_references(
    conn: sqlite3.Connection, bundle: PublicationBundle, paper_id: str
) -> None:
    for reference_name, relative_path in bundle.references.items():
        if not str(reference_name).startswith("summary:"):
            continue
        template_tag = str(reference_name).split(":", 1)[1]
        resource = bundle.resource_map.get(relative_path)
        if resource is None:
            continue
        conn.execute(
            "UPDATE paper_summary SET resource_path=?,content_hash=? "
            "WHERE paper_id=? AND template_tag=?",
            (resource.relative_path, resource.digest, paper_id, template_tag),
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


def _summary_template_payloads(value: Any, raw_summary: bytes) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    if isinstance(value, Mapping):
        for raw_tag, raw_payload in value.items():
            if raw_payload is None:
                continue
            tag = _canonical_template_tag(str(raw_tag))
            if isinstance(raw_payload, Mapping):
                payloads[tag] = {
                    str(key): plain(item) for key, item in raw_payload.items()
                }
            else:
                payloads[tag] = {"summary": str(raw_payload)}
    if payloads or not raw_summary:
        return dict(sorted(payloads.items()))
    try:
        parsed = json.loads(raw_summary.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if isinstance(parsed, dict):
        return {"simple": parsed}
    return {}


def _preferred_summary_tag(paper: Mapping[str, Any], payloads: Mapping[str, Any]) -> str:
    for key in ("preferred_summary_template", "template_tag", "prompt_template"):
        value = str(paper.get(key) or "").strip()
        if value:
            candidate = _canonical_template_tag(value)
            if candidate in payloads:
                return candidate
    if "simple" in payloads:
        return "simple"
    return sorted(payloads)[0]


def _canonical_template_tag(value: str) -> str:
    tag = re.sub(r"[^a-z0-9_-]+", "_", str(value).strip().lower())
    tag = re.sub(r"_+", "_", tag).strip("_")
    return tag or "default"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "FormalStore",
    "LanceDBIndexer",
    "LocalFormalStore",
    "MirroredFormalStore",
    "PublicationBundle",
    "PublicationCancelled",
    "PublicationConflict",
    "PublicationError",
    "PublicationResource",
    "PublicationResult",
    "PublicationWorker",
    "PublicationWorkerResult",
    "WebDavFormalStore",
    "build_bundle_from_preview",
    "build_publication_bundle",
    "build_publication_bundle_from_manifest",
    "publication_manifest",
    "publish_bundle",
    "queue_publication",
    "validate_vector_index",
]
