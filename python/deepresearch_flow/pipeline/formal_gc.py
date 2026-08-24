"""Bounded reference-based collection for immutable formal resources."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .publication_store import MirroredFormalStore, safe_relative_path


@dataclass(frozen=True)
class FormalGcResult:
    """Observable result of one bounded formal-resource GC cycle."""

    deleted: tuple[str, ...] = ()
    skipped: int = 0
    warning: str | None = None


_DIGEST_NAME = re.compile(r"(?P<digest>[0-9a-f]{64})(?:\.[A-Za-z0-9_.-]+)?$")


def _snapshot_references(snapshot_db: str | Path) -> tuple[set[str], set[str], str | None]:
    path = Path(snapshot_db)
    if not path.exists():
        return set(), set(), None
    references: set[str] = set()
    receipt_jobs: set[str] = set()
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error:
        return set(), set(), "Snapshot reference database is unreadable"
    try:
        for table in ("paper", "paper_summary"):
            try:
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                values = dict(row)
                for key, value in values.items():
                    if value is None:
                        continue
                    if key in {"pdf_content_hash", "pdf_hash"}:
                        _add_hash_reference(references, "pdf", str(value), ".pdf")
                    elif key in {"source_md_content_hash", "source_hash", "source_md_hash"}:
                        _add_hash_reference(references, "md", str(value), ".md")
                    elif key in {"resource_path", "path", "static_path"} and isinstance(value, str):
                        _add_reference(references, value)
                    elif key in {"translations", "translation_hashes"} and isinstance(value, str):
                        try:
                            translations = json.loads(value)
                        except json.JSONDecodeError:
                            translations = None
                        if isinstance(translations, Mapping):
                            for language, digest in translations.items():
                                _add_hash_reference(
                                    references,
                                    f"md_translate/{str(language).strip().lower()}",
                                    str(digest),
                                    ".md",
                                )
        try:
            rows = connection.execute(
                "SELECT job_id FROM pipeline_publication_receipt"
            ).fetchall()
            receipt_jobs = {str(row["job_id"]) for row in rows}
        except sqlite3.Error:
            pass
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
) -> tuple[set[str], set[str], str | None]:
    references: set[str] = set()
    jobs: set[str] = set()
    for manifest in manifests:
        if not isinstance(manifest, Mapping):
            return set(), set(), "publication manifest is invalid"
        job_id = str(manifest.get("job_id") or "")
        version = manifest.get("version")
        if not job_id or isinstance(version, bool) or not isinstance(version, int) or version != 1:
            return set(), set(), "publication manifest identity is invalid"
        bundle_digest = str(manifest.get("bundle_digest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", bundle_digest):
            return set(), set(), "publication manifest digest is invalid"
        jobs.add(job_id)
        records = manifest.get("resources")
        if not isinstance(records, list) or not records:
            return set(), set(), "publication manifest has invalid resources"
        for record in records:
            if not isinstance(record, Mapping):
                return set(), set(), "publication manifest resource is invalid"
            try:
                relative = safe_relative_path(str(record.get("path") or ""))
            except ValueError:
                return set(), set(), "publication manifest contains unsafe resource path"
            digest = str(record.get("digest") or "")
            size = record.get("size")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or isinstance(size, bool) or not isinstance(size, int) or size < 0:
                return set(), set(), "publication manifest resource metadata is invalid"
            references.add(relative)
        raw_refs = manifest.get("references")
        if isinstance(raw_refs, Mapping):
            for value in raw_refs.values():
                try:
                    references.add(safe_relative_path(str(value)))
                except ValueError:
                    return set(), set(), "publication manifest contains unsafe reference"
    return references, jobs, None


def _candidate_name(relative: str) -> str | None:
    parts = Path(relative).parts
    if not parts:
        return None
    prefix = parts[0]
    if prefix in {"pdf", "md"} and len(parts) == 2:
        expected_suffix = ".pdf" if prefix == "pdf" else ".md"
        if not parts[1].endswith(expected_suffix):
            return None
        match = _DIGEST_NAME.fullmatch(parts[1])
    elif prefix == "md_translate" and len(parts) == 3:
        if not parts[2].endswith(".md"):
            return None
        match = _DIGEST_NAME.fullmatch(parts[2])
    elif prefix == "summary" and len(parts) in {2, 3, 4}:
        if not parts[-1].endswith(".json"):
            return None
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts[1:-1]):
            return None
        match = _DIGEST_NAME.fullmatch(parts[-1])
    elif prefix == "objects" and len(parts) == 2:
        match = _DIGEST_NAME.fullmatch(parts[1])
    else:
        return None
    return None if match is None else str(match.group("digest"))


def _store_candidates(store: Any) -> list[str]:
    listing = getattr(store, "list_content_addressed_files", None)
    if not callable(listing):
        raise RuntimeError("formal store does not support safe reference-based listing")
    result: list[str] = []
    for value in listing():
        relative = safe_relative_path(str(value))
        if _candidate_name(relative) is not None:
            result.append(relative)
    return sorted(set(result))


def _collect_one(
    store: Any,
    references: set[str],
    *,
    limit: int,
    now: datetime,
    grace_seconds: int,
) -> FormalGcResult:
    try:
        candidates = _store_candidates(store)
    except (OSError, RuntimeError, ValueError) as exc:
        return FormalGcResult(warning=str(exc))
    read = getattr(store, "read", None)
    delete = getattr(store, "delete", None)
    if not callable(read) or not callable(delete):
        return FormalGcResult(warning="formal store does not support safe reference-based deletion")
    deleted: list[str] = []
    skipped = 0
    unknown_age = False
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
        except (OSError, RuntimeError, ValueError):
            skipped += 1
            continue
        deleted.append(relative)
    warning = "formal store does not expose safe object age" if unknown_age else None
    return FormalGcResult(tuple(deleted), skipped, warning)


def collect_unreferenced_formal_resources(
    store: Any,
    *,
    snapshot_db: str | Path,
    manifests: Iterable[Mapping[str, Any]] = (),
    limit: int = 100,
    now: datetime | None = None,
    grace_seconds: int = 86_400,
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
    snapshot_refs, receipt_jobs, snapshot_warning = _snapshot_references(snapshot_db)
    if snapshot_warning is not None:
        return FormalGcResult(warning=snapshot_warning)
    manifest_refs, manifest_jobs, warning = _manifest_references(manifests)
    if warning is not None:
        return FormalGcResult(warning=warning)
    missing_receipts = receipt_jobs - manifest_jobs
    if missing_receipts:
        return FormalGcResult(warning="publication receipt lacks durable manifest")
    references = snapshot_refs | manifest_refs
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if isinstance(store, MirroredFormalStore):
        primary = _collect_one(
            store.primary,
            references,
            limit=limit,
            now=current,
            grace_seconds=grace_seconds,
        )
        cache = _collect_one(
            store.cache,
            references,
            limit=max(0, limit - len(primary.deleted)),
            now=current,
            grace_seconds=grace_seconds,
        ) if len(primary.deleted) < limit else FormalGcResult()
        warnings = "; ".join(item for item in (primary.warning, cache.warning) if item)
        return FormalGcResult(
            tuple(primary.deleted + cache.deleted),
            primary.skipped + cache.skipped,
            warnings or None,
        )
    return _collect_one(
        store,
        references,
        limit=limit,
        now=current,
        grace_seconds=grace_seconds,
    )


__all__ = ["FormalGcResult", "collect_unreferenced_formal_resources"]
