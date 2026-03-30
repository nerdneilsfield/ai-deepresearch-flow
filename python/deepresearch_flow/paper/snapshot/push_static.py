"""Push static export files to a remote storage backend."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from deepresearch_flow.storage.base import RemoteStorage, StorageAuthError

logger = logging.getLogger(__name__)


@dataclass
class PushStaticStats:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    failed_files: list[dict[str, str]] = field(default_factory=list)
    per_directory: dict[str, dict[str, int]] = field(default_factory=dict)


def _discover_files(root: Path) -> list[str]:
    """Recursively discover all files under root, returning sorted relative paths."""
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
    )


def _top_dir(rel_path: str) -> str:
    """Extract the top-level directory (e.g. 'pdf/abc.pdf' -> 'pdf/')."""
    parts = rel_path.split("/", 1)
    return f"{parts[0]}/" if len(parts) > 1 else "(root)"


def _ensure_parents(storage: RemoteStorage, rel_path: str) -> None:
    """Call storage.mkdir() for each parent directory component."""
    parts = rel_path.split("/")[:-1]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        storage.mkdir(current)


def _record(stats: PushStaticStats, rel_path: str, kind: str, error: str = "") -> None:
    """Record a result for stats and per-directory breakdown."""
    top = _top_dir(rel_path)
    stats.per_directory.setdefault(top, {"uploaded": 0, "skipped": 0, "failed": 0})
    if kind == "uploaded":
        stats.uploaded += 1
        stats.per_directory[top]["uploaded"] += 1
    elif kind == "skipped":
        stats.skipped += 1
        stats.per_directory[top]["skipped"] += 1
    elif kind == "failed":
        stats.failed += 1
        stats.failed_files.append({"path": rel_path, "error": error})
        stats.per_directory[top]["failed"] += 1
        logger.warning("Failed to push %s: %s", rel_path, error)


def push_static_files(
    static_export_dir: Path,
    storage: RemoteStorage,
    *,
    only_files: list[str] | None = None,
    on_file_result: Callable[[str, str, str], None] | None = None,
) -> PushStaticStats:
    """Push static files to a remote storage backend.

    StorageAuthError propagates immediately (abort all).
    Other exceptions are caught per-file and recorded as failures.
    """
    stats = PushStaticStats()

    all_files = only_files if only_files is not None else _discover_files(static_export_dir)
    if not all_files:
        return stats

    for rel_path in all_files:
        # Check existence — StorageAuthError propagates immediately.
        if storage.exists(rel_path):
            _record(stats, rel_path, "skipped")
            if on_file_result:
                on_file_result(rel_path, "skipped", "")
            continue

        # Ensure parent directories.
        try:
            _ensure_parents(storage, rel_path)
        except StorageAuthError:
            raise
        except Exception as exc:
            _record(stats, rel_path, "failed", str(exc))
            if on_file_result:
                on_file_result(rel_path, "failed", str(exc))
            continue

        # Upload file bytes.
        file_path = static_export_dir / rel_path
        try:
            data = file_path.read_bytes()
            storage.upload(rel_path, data)
            _record(stats, rel_path, "uploaded")
            if on_file_result:
                on_file_result(rel_path, "uploaded", "")
        except StorageAuthError:
            raise
        except Exception as exc:
            _record(stats, rel_path, "failed", str(exc))
            if on_file_result:
                on_file_result(rel_path, "failed", str(exc))

    return stats


# ---------------------------------------------------------------------------
# Error report I/O
# ---------------------------------------------------------------------------

def write_error_report(failed_files: list[dict[str, str]], path: Path) -> None:
    """Write failed file list to JSON."""
    path.write_text(json.dumps(failed_files, indent=2, ensure_ascii=False), encoding="utf-8")


def load_retry_files(path: Path) -> list[str]:
    """Load relative file paths from a push-static-errors.json report."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [entry["path"] for entry in data]
