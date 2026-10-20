"""Shared utilities for snapshot API and MCP server.

This module contains common types, configuration, and utilities used by both
the snapshot REST API and the MCP server to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import sqlite3


_LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
# Explicit ranges only: ipaddress.is_reserved/is_private also match public NAT64 (64:ff9b::/96) etc.
_PRIVATE_HTTP_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def _is_private_http_host(host: str) -> bool:
    normalized = host.removeprefix("[").removesuffix("]").lower().rstrip(".")
    if normalized in _LOCAL_HTTP_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(
        address in network
        for network in _PRIVATE_HTTP_NETWORKS
        if network.version == address.version
    )


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


def _open_rw_conn(db_path: Path) -> sqlite3.Connection:
    """Open a read-write SQLite connection with Row factory and WAL mode."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA journal_mode=WAL;")
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
