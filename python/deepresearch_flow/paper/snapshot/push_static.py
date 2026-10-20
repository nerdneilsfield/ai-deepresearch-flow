"""Push static export files to a remote storage backend."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable

import httpx

from deepresearch_flow.storage.base import RemoteStorage, StorageAuthError

logger = logging.getLogger(__name__)


@dataclass
class PushStaticStats:
    uploaded: int = 0
    overwritten: int = 0
    skipped: int = 0
    failed: int = 0
    failed_files: list[dict[str, str]] = field(default_factory=list)
    per_directory: dict[str, dict[str, int]] = field(default_factory=dict)


def _discover_files(root: Path) -> list[str]:
    """Recursively discover all files under root, returning sorted relative paths."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def discover_static_files(
    static_export_dir: Path, *, only_files: list[str] | None = None
) -> list[str]:
    """Return the relative files that would be processed for a static push."""
    return only_files if only_files is not None else _discover_files(static_export_dir)


def _top_dir(rel_path: str) -> str:
    """Extract the top-level directory (e.g. 'pdf/abc.pdf' -> 'pdf/')."""
    parts = rel_path.split("/", 1)
    return f"{parts[0]}/" if len(parts) > 1 else "(root)"


def _remote_files_in(storage: RemoteStorage, directory: str) -> set[str]:
    """Files directly under a remote directory; a missing directory (404) is empty."""
    try:
        entries = storage.list(directory)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return set()
        raise
    return {entry for entry in entries if not entry.endswith("/")}


def _ensure_parents(
    storage: RemoteStorage, rel_path: str, ensured_dirs: set[str] | None = None
) -> None:
    """Call storage.mkdir() for each parent directory component."""
    parts = rel_path.split("/")[:-1]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        if ensured_dirs is not None and current in ensured_dirs:
            continue
        storage.mkdir(current)
        if ensured_dirs is not None:
            ensured_dirs.add(current)


def _record(stats: PushStaticStats, rel_path: str, kind: str, error: str = "") -> None:
    """Record a result for stats and per-directory breakdown."""
    top = _top_dir(rel_path)
    stats.per_directory.setdefault(
        top,
        {"uploaded": 0, "overwritten": 0, "skipped": 0, "failed": 0},
    )
    if kind == "uploaded":
        stats.uploaded += 1
        stats.per_directory[top]["uploaded"] += 1
    elif kind == "overwritten":
        stats.overwritten += 1
        stats.per_directory[top]["overwritten"] += 1
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
    concurrency: int = 1,
    overwrite: bool = False,
) -> PushStaticStats:
    """Push static files to a remote storage backend.

    StorageAuthError propagates immediately (abort all).
    Other exceptions are caught per-file and recorded as failures.
    """
    stats = PushStaticStats()
    ensured_dirs: set[str] = set()
    ensured_dirs_lock = Lock()
    stats_lock = Lock()

    all_files = discover_static_files(static_export_dir, only_files=only_files)
    if not all_files:
        return stats
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    # One listing per remote directory instead of one HEAD per file.
    # StorageAuthError propagates immediately. A directory whose listing is untrustworthy
    # (RuntimeError) fails all its files instead of being treated as empty, which would
    # overwrite remote files.
    directories = sorted({rel_path.rpartition("/")[0] for rel_path in all_files})

    def list_directory(directory: str) -> tuple[set[str], str]:
        try:
            return _remote_files_in(storage, directory), ""
        except StorageAuthError:
            raise
        except RuntimeError as exc:
            return set(), str(exc)

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="storage-list") as executor:
        listings = list(executor.map(list_directory, directories))
    remote_files: set[str] = set().union(*(files for files, _ in listings))
    failed_directories = {
        directory: error for directory, (_, error) in zip(directories, listings) if error
    }

    def process_file(rel_path: str) -> None:
        list_error = failed_directories.get(rel_path.rpartition("/")[0])
        if list_error:
            with stats_lock:
                _record(stats, rel_path, "failed", f"remote listing failed: {list_error}")
            if on_file_result:
                on_file_result(rel_path, "failed", list_error)
            return
        exists = rel_path in remote_files
        if exists and not overwrite:
            with stats_lock:
                _record(stats, rel_path, "skipped")
            if on_file_result:
                on_file_result(rel_path, "skipped", "")
            return

        # An existing file already has its parent directories.  Avoid creating
        # them again when replacing it because some backends reject duplicate
        # mkdir requests.
        if not exists:
            try:
                with ensured_dirs_lock:
                    _ensure_parents(storage, rel_path, ensured_dirs)
            except StorageAuthError:
                raise
            except Exception as exc:
                with stats_lock:
                    _record(stats, rel_path, "failed", str(exc))
                if on_file_result:
                    on_file_result(rel_path, "failed", str(exc))
                return

        # Upload file bytes.
        file_path = static_export_dir / rel_path
        try:
            data = file_path.read_bytes()
            storage.upload(rel_path, data)
            result_kind = "overwritten" if exists else "uploaded"
            with stats_lock:
                _record(stats, rel_path, result_kind)
            if on_file_result:
                on_file_result(rel_path, result_kind, "")
        except StorageAuthError:
            raise
        except Exception as exc:
            with stats_lock:
                _record(stats, rel_path, "failed", str(exc))
            if on_file_result:
                on_file_result(rel_path, "failed", str(exc))

    if concurrency == 1:
        for rel_path in all_files:
            process_file(rel_path)
        return stats

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="storage-push") as executor:
        futures = [executor.submit(process_file, rel_path) for rel_path in all_files]
        for future in futures:
            future.result()

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
