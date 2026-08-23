"""Public Snapshot preparation seam shared by CLI and pipeline publisher."""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from typing import Any

from .transaction import open_snapshot_connection, snapshot_transaction


@dataclass
class InsertStats:
    """Black-box result counters for metadata preparation."""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    paper_ids: list[str] = field(default_factory=list)


def insert_paper_metadata(
    conn: sqlite3.Connection,
    paper: dict[str, Any],
    index: int,
    stats: InsertStats,
    *,
    overwrite: bool = False,
) -> None:
    """Prepare one paper's metadata in caller-owned Snapshot transaction."""
    # Route module owns legacy metadata implementation; this public seam keeps
    # CLI and publication callers on one stable API without exposing internals.
    from .admin import _insert_paper_metadata_impl as _insert

    _insert(conn, paper, index, stats, overwrite=overwrite)


__all__ = [
    "InsertStats",
    "insert_paper_metadata",
    "open_snapshot_connection",
    "snapshot_transaction",
]
