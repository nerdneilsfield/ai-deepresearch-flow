"""Shared utilities for snapshot API and MCP server.

This module contains common types, configuration, and utilities used by both
the snapshot REST API and the MCP server to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ApiLimits:
    """API rate and size limits."""

    max_query_length: int = 500
    max_page_size: int = 100
    max_pagination_offset: int = 10_000


def _open_ro_conn(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection with Row factory.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection: A read-only connection with row_factory set to Row.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row[1]) == column_name for row in rows)
