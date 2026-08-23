"""Reusable Snapshot connection and transaction boundaries.

CLI update, HTTP admin writes, and pipeline publication all use these public
seams.  Schema setup happens before the write transaction so SQLite WAL
initialization never runs while a caller already holds ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from .common import _open_rw_conn
from .schema import init_snapshot_db


def open_snapshot_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a configured Snapshot connection and ensure compatible schema."""
    connection = _open_rw_conn(Path(db_path))
    init_snapshot_db(connection)
    return connection


@contextmanager
def snapshot_transaction(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Yield one globally serialized SQLite write transaction."""
    connection = open_snapshot_connection(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["open_snapshot_connection", "snapshot_transaction"]
