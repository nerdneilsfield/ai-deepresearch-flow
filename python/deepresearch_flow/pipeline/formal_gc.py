"""Bounded reference-based collection for immutable formal resources."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .publication_store import (
    FormalStoreCursorError,
    MirroredFormalStore,
    PUBLICATION_SERIALIZATION_LOCK,
    content_addressed_digest,
    safe_relative_path,
)
from .publication_models import PublicationError


@dataclass(frozen=True)
class FormalGcResult:
    """Observable result of one bounded formal-resource GC cycle."""

    deleted: tuple[str, ...] = ()
    skipped: int = 0
    warning: str | None = None
    next_cursor: str | None = None
    inspected: int = 0


_MIRROR_CURSOR_PREFIX = "v1."
_OPAQUE_FORMAL_CURSOR_PREFIXES = ("v1l.", "v1w.")


def _is_opaque_formal_cursor(cursor: str | None) -> bool:
    return isinstance(cursor, str) and cursor.startswith(_OPAQUE_FORMAL_CURSOR_PREFIXES)


def _snapshot_references(snapshot_db: str | Path) -> tuple[set[str], set[str], str | None]:
    path = Path(snapshot_db)
    if not path.exists():
        return set(), set(), "Snapshot reference database is unavailable"
    references: set[str] = set()
    receipt_jobs: set[str] = set()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        return set(), set(), "Snapshot reference database is unreadable"
    try:
        # These are deliberately fixed, quoted queries.  A partially migrated
        # Snapshot must never be treated as an empty reference set.
        paper_rows = connection.execute(
            'SELECT "pdf_content_hash","source_md_content_hash" FROM "paper"'
        ).fetchall()
        summary_rows = connection.execute(
            'SELECT "paper_id","template_tag","resource_path","content_hash" '
            'FROM "paper_summary"'
        ).fetchall()
        translation_rows = connection.execute(
            'SELECT "paper_id","lang","md_content_hash" FROM "paper_translation"'
        ).fetchall()
        receipt_rows = connection.execute(
            'SELECT "job_id" FROM "pipeline_publication_receipt"'
        ).fetchall()
        for row in paper_rows:
            _add_hash_reference(references, "pdf", str(row["pdf_content_hash"] or ""), ".pdf")
            _add_hash_reference(
                references, "md", str(row["source_md_content_hash"] or ""), ".md"
            )
        for row in summary_rows:
            resource_path = row["resource_path"]
            if isinstance(resource_path, str) and resource_path:
                _add_reference(references, resource_path)
            elif row["content_hash"]:
                paper_id = str(row["paper_id"] or "")
                template_tag = str(row["template_tag"] or "")
                digest = str(row["content_hash"])
                if (
                    re.fullmatch(r"[A-Za-z0-9._-]+", paper_id)
                    and re.fullmatch(r"[A-Za-z0-9._-]+", template_tag)
                ):
                    _add_hash_reference(
                        references,
                        f"summary/{paper_id}/{template_tag}",
                        digest,
                        ".json",
                    )
        for row in translation_rows:
            language = str(row["lang"] or "").strip().lower()
            if re.fullmatch(r"[A-Za-z0-9._-]+", language):
                _add_hash_reference(
                    references,
                    f"md_translate/{language}",
                    str(row["md_content_hash"] or ""),
                    ".md",
                )
        receipt_jobs = {str(row["job_id"]) for row in receipt_rows if row["job_id"]}
    except sqlite3.Error:
        return set(), set(), "Snapshot reference database is incomplete or unreadable"
    finally:
        connection.close()
    return references, receipt_jobs, None


def _add_reference(references: set[str], value: str) -> None:
    try:
        references.add(safe_relative_path(value))
    except ValueError:
        # Snapshot columns may contain legacy URLs or plain labels.  They are
        # not candidates for this GC and therefore cannot authorize deletion.
        return


def _add_hash_reference(
    references: set[str], prefix: str, value: str, suffix: str
) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value):
        _add_reference(references, f"{prefix}/{value}{suffix}")


def _manifest_references(
    manifests: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, set[str]], set[str], str | None]:
    references: dict[str, set[str]] = {}
    invalid_jobs: set[str] = set()
    for manifest in manifests:
        if not isinstance(manifest, Mapping):
            return {}, set(), "publication manifest is invalid"
        job_id = str(manifest.get("job_id") or "")
        if not job_id:
            return {}, set(), "publication manifest identity is invalid"
        version = manifest.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            invalid_jobs.add(job_id)
            continue
        bundle_digest = str(manifest.get("bundle_digest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", bundle_digest):
            invalid_jobs.add(job_id)
            continue
        if job_id in references:
            invalid_jobs.add(job_id)
            references.pop(job_id, None)
            continue
        job_references: set[str] = set()
        records = manifest.get("resources")
        if not isinstance(records, list) or not records:
            invalid_jobs.add(job_id)
            continue
        invalid = False
        for record in records:
            if not isinstance(record, Mapping):
                invalid = True
                break
            try:
                relative = safe_relative_path(str(record.get("path") or ""))
            except ValueError:
                invalid = True
                break
            digest = str(record.get("digest") or "")
            size = record.get("size")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or isinstance(size, bool) or not isinstance(size, int) or size < 0:
                invalid = True
                break
            job_references.add(relative)
        if invalid:
            invalid_jobs.add(job_id)
            continue
        raw_refs = manifest.get("references")
        if isinstance(raw_refs, Mapping):
            for value in raw_refs.values():
                try:
                    job_references.add(safe_relative_path(str(value)))
                except ValueError:
                    invalid = True
                    break
        if invalid:
            invalid_jobs.add(job_id)
            continue
        references[job_id] = job_references
    return references, invalid_jobs, None


def _candidate_name(relative: str) -> str | None:
    return content_addressed_digest(relative)


def _decode_mirror_cursor(
    cursor: str | None,
) -> tuple[str | None, str | None, str, str | None]:
    if cursor is None:
        return None, None, "primary", None
    if not cursor.startswith(_MIRROR_CURSOR_PREFIX):
        # Cursors written by round4 were one path shared by both stores.
        return cursor, cursor, "primary", None
    try:
        encoded = cursor[len(_MIRROR_CURSOR_PREFIX) :]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        primary = payload.get("p")
        cache = payload.get("c")
        turn = payload.get("t")
        if (primary is not None and not isinstance(primary, str)) or (
            cache is not None and not isinstance(cache, str)
        ):
            raise ValueError
        if turn not in {"primary", "cache"}:
            raise ValueError
        return primary, cache, turn, None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return None, None, "primary", "formal GC cursor is invalid"


def _encode_mirror_cursor(
    primary: str | None, cache: str | None, turn: str
) -> str | None:
    if primary is None and cache is None:
        return None
    payload = {"p": primary, "c": cache, "t": turn}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return _MIRROR_CURSOR_PREFIX + encoded


def _store_candidates(
    store: Any,
    *,
    candidate_limit: int,
    inspection_limit: int,
    cursor: str | None = None,
) -> tuple[list[str], str | None, int]:
    page_listing = getattr(store, "list_content_addressed_page", None)
    if callable(page_listing):
        kwargs: dict[str, Any] = {
            "max_items": candidate_limit,
            "content_addressed_only": True,
            "inspection_limit": inspection_limit,
        }
        if cursor is not None:
            kwargs["after"] = cursor
        page = page_listing(**kwargs)
        values = getattr(page, "items", ())
        next_cursor = getattr(page, "next_cursor", None)
        inspected = int(getattr(page, "inspected", 0))
    else:
        listing = getattr(store, "list_content_addressed_files", None)
        if not callable(listing):
            raise RuntimeError("formal store does not support safe reference-based listing")
        result: list[str] = []
        page_size = min(max(candidate_limit + 1, 1), max(inspection_limit, 1))
        kwargs: dict[str, Any] = {
            "max_items": page_size,
            "content_addressed_only": True,
        }
        if cursor is not None:
            kwargs["after"] = cursor
        try:
            values = listing(**kwargs)
        except TypeError:
            fallback_kwargs: dict[str, Any] = {"max_items": page_size}
            if cursor is not None:
                fallback_kwargs["after"] = cursor
            try:
                values = listing(**fallback_kwargs)
            except TypeError:
                values = listing()
        for value in values:
            relative = safe_relative_path(str(value))
            if cursor is not None and not _is_opaque_formal_cursor(cursor) and relative <= cursor:
                continue
            if _candidate_name(relative) is not None:
                result.append(relative)
        ordered = sorted(set(result))
        page = ordered[:candidate_limit]
        next_cursor = page[-1] if len(ordered) > candidate_limit and page else None
        return page, next_cursor, min(len(ordered), inspection_limit)
    result = []
    for value in values:
        relative = safe_relative_path(str(value))
        if cursor is not None and not _is_opaque_formal_cursor(cursor) and relative <= cursor:
            continue
        if _candidate_name(relative) is not None:
            result.append(relative)
    return sorted(set(result))[:candidate_limit], next_cursor, inspected


def _collect_one(
    store: Any,
    references: set[str],
    *,
    limit: int,
    now: datetime,
    grace_seconds: int,
    inspection_limit: int,
    cursor: str | None,
) -> FormalGcResult:
    try:
        candidates, next_cursor, inspected = _store_candidates(
            store,
            candidate_limit=limit,
            inspection_limit=inspection_limit,
            cursor=cursor,
        )
    except FormalStoreCursorError as exc:
        return FormalGcResult(warning=str(exc), next_cursor=None)
    except PublicationError as exc:
        return FormalGcResult(warning=str(exc), next_cursor=cursor)
    except Exception:
        return FormalGcResult(warning="formal GC listing failed", next_cursor=cursor)
    read = getattr(store, "read", None)
    delete = getattr(store, "delete", None)
    if not callable(read) or not callable(delete):
        return FormalGcResult(
            warning="formal store does not support safe reference-based deletion",
            next_cursor=cursor,
        )
    deleted: list[str] = []
    skipped = 0
    unknown_age = False
    storage_errors = 0
    for relative in candidates:
        if len(deleted) >= limit:
            break
        if relative in references:
            skipped += 1
            continue
        digest = _candidate_name(relative)
        if digest is None:
            continue
        try:
            content = read(relative)
            if hashlib.sha256(content).hexdigest() != digest:
                skipped += 1
                continue
            mtime = getattr(store, "modified_at", lambda _path: None)(relative)
            if grace_seconds > 0 and mtime is None:
                skipped += 1
                unknown_age = True
                continue
            if mtime is not None and grace_seconds > 0:
                age = (now - mtime.astimezone(timezone.utc)).total_seconds()
                if age < grace_seconds:
                    skipped += 1
                    continue
            delete(relative)
        except Exception:
            skipped += 1
            storage_errors += 1
            continue
        deleted.append(relative)
    warning_parts = []
    if unknown_age:
        warning_parts.append("formal store does not expose safe object age")
    if storage_errors:
        warning_parts.append(f"formal GC skipped {storage_errors} object(s) due to storage errors")
    if inspected >= inspection_limit:
        warning_parts.append("formal GC inspection budget reached")
    warning = "; ".join(warning_parts) or None
    return FormalGcResult(tuple(deleted), skipped, warning, next_cursor, inspected)


def collect_unreferenced_formal_resources(
    store: Any,
    *,
    snapshot_db: str | Path,
    manifests: Iterable[Mapping[str, Any]] = (),
    limit: int = 100,
    now: datetime | None = None,
    grace_seconds: int = 86_400,
    cursor: str | None = None,
) -> FormalGcResult:
    """Delete at most ``limit`` unreferenced immutable objects.

    References come from current Snapshot rows and durable publication
    manifests.  Unknown/corrupt metadata causes a warning and no deletion;
    private work and preview roots are never traversed.
    """
    if limit <= 0:
        raise ValueError("formal GC limit must be positive")
    if grace_seconds < 0:
        raise ValueError("formal GC grace must not be negative")
    inspection_limit = max(limit * 4, limit)
    # Publication and GC share one process-wide serialization primitive.  The
    # publisher holds it from its first immutable put through Snapshot receipt
    # commit, so this cycle cannot delete a resource between those boundaries.
    with PUBLICATION_SERIALIZATION_LOCK:
        snapshot_refs, receipt_jobs, snapshot_warning = _snapshot_references(snapshot_db)
        if snapshot_warning is not None:
            return FormalGcResult(warning=snapshot_warning)
        manifest_refs, invalid_jobs, warning = _manifest_references(manifests)
        if warning is not None:
            return FormalGcResult(warning=warning)
        missing_receipts = receipt_jobs - set(manifest_refs) - invalid_jobs
        invalid_receipts = receipt_jobs & invalid_jobs
        if missing_receipts or invalid_receipts:
            return FormalGcResult(warning="publication receipt lacks durable manifest")
        references = snapshot_refs | set().union(
            *(manifest_refs[job_id] for job_id in receipt_jobs if job_id in manifest_refs)
        )
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if isinstance(store, MirroredFormalStore):
            primary_cursor, cache_cursor, turn, cursor_warning = _decode_mirror_cursor(cursor)
            if cursor_warning is not None:
                return FormalGcResult(warning=cursor_warning)
            first_is_primary = turn == "primary"
            first_store = store.primary if first_is_primary else store.cache
            first_cursor = primary_cursor if first_is_primary else cache_cursor
            first = _collect_one(
                first_store,
                references,
                limit=limit,
                now=current,
                grace_seconds=grace_seconds,
                inspection_limit=inspection_limit,
                cursor=first_cursor,
            )
            other_is_primary = not first_is_primary
            other_store = store.primary if other_is_primary else store.cache
            other_cursor = primary_cursor if other_is_primary else cache_cursor
            remaining = max(0, limit - len(first.deleted))
            remaining_inspection = max(0, inspection_limit - first.inspected)
            other = FormalGcResult(next_cursor=other_cursor)
            if remaining and remaining_inspection:
                other = _collect_one(
                    other_store,
                    references,
                    limit=remaining,
                    now=current,
                    grace_seconds=grace_seconds,
                    inspection_limit=remaining_inspection,
                    cursor=other_cursor,
                )
            if first_is_primary:
                next_primary = first.next_cursor
                next_cache = other.next_cursor
            else:
                next_primary = other.next_cursor
                next_cache = first.next_cursor
            if not remaining or not remaining_inspection:
                # A full first-side inspection or delete budget alternates to
                # the other side, preventing one unavailable/tree-heavy side
                # from starving its mirror.
                next_turn = "cache" if first_is_primary else "primary"
            else:
                # Both stores were inspected; start next cycle at first side
                # so the two sides receive alternating deletion budgets.
                next_turn = "primary" if first_is_primary else "cache"
            warnings = "; ".join(item for item in (first.warning, other.warning) if item)
            return FormalGcResult(
                tuple(first.deleted + other.deleted),
                first.skipped + other.skipped,
                warnings or None,
                _encode_mirror_cursor(next_primary, next_cache, next_turn),
                first.inspected + other.inspected,
            )
        return _collect_one(
            store,
            references,
            limit=limit,
            now=current,
            grace_seconds=grace_seconds,
            inspection_limit=inspection_limit,
            cursor=cursor,
        )


__all__ = ["FormalGcResult", "collect_unreferenced_formal_resources"]
