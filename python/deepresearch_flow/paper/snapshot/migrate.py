"""Schema migration utilities for upgrading legacy snapshot databases."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.db_ops import enrich_with_bibtex
from deepresearch_flow.paper.snapshot.common import _column_exists, _table_exists

console = Console()


def migrate_schema(conn: sqlite3.Connection) -> tuple[bool, list[tuple[str, str, str]]]:
    """
    Migrate legacy schema to latest version.

    Returns:
        (changed, rows): Whether any changes were made, and list of (component, action, status) tuples.
    """
    changed = False
    rows: list[tuple[str, str, str]] = []

    # Check and add DOI column if missing
    if not _column_exists(conn, "paper", "doi"):
        conn.execute("ALTER TABLE paper ADD COLUMN doi TEXT")
        changed = True
        rows.append(("paper.doi column", "Added", "✓"))
    else:
        rows.append(("paper.doi column", "Already exists", "○"))

    # Check and create paper_bibtex table if missing
    if not _table_exists(conn, "paper_bibtex"):
        conn.execute(
            """
            CREATE TABLE paper_bibtex (
              paper_id TEXT PRIMARY KEY,
              bibtex_raw TEXT NOT NULL,
              bibtex_key TEXT,
              entry_type TEXT,
              FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE
            )
            """
        )
        changed = True
        rows.append(("paper_bibtex table", "Created", "✓"))
    else:
        rows.append(("paper_bibtex table", "Already exists", "○"))

    return changed, rows


def enrich_db_with_bibtex(
    conn: sqlite3.Connection,
    bibtex_path: Path,
) -> tuple[int, int, int]:
    """
    Enrich existing database papers with BibTeX data.

    Returns:
        (matched_count, doi_count, total_count): Number of papers matched, with DOI, and total papers.
    """
    # Load all papers from database
    rows = conn.execute(
        """
        SELECT paper_id, title, year, publication_date, venue
        FROM paper
        ORDER BY paper_id
        """
    ).fetchall()

    # Convert to paper dict format for enrich_with_bibtex
    papers: list[dict[str, Any]] = []
    for row in rows:
        paper_id, title, year, pub_date, venue = row
        papers.append({
            "paper_id": paper_id,
            "paper_title": title,
            "publication_date": pub_date or year,
            "publication_venue": venue,
        })

    # Match with BibTeX
    enrich_with_bibtex(papers, bibtex_path)

    # Update database
    matched_count = 0
    doi_count = 0
    for paper in papers:
        bib = paper.get("bibtex")
        if not isinstance(bib, dict):
            continue

        paper_id = paper["paper_id"]

        # Extract DOI from bibtex fields
        fields = bib.get("fields", {})
        doi = fields.get("doi", "")
        if doi:
            conn.execute(
                "UPDATE paper SET doi = ? WHERE paper_id = ?",
                (doi, paper_id),
            )
            doi_count += 1

        # Insert into paper_bibtex table
        raw_entry = bib.get("raw_entry", "")
        bibtex_key = bib.get("key", "")
        entry_type = bib.get("type", "")

        if raw_entry:
            conn.execute(
                """
                INSERT OR REPLACE INTO paper_bibtex (paper_id, bibtex_raw, bibtex_key, entry_type)
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, raw_entry, bibtex_key, entry_type),
            )
            matched_count += 1

    return matched_count, doi_count, len(papers)


def update_static_export_index(
    static_export_dir: Path,
    conn: sqlite3.Connection,
) -> int:
    """
    Update paper_index.json in static export directory with DOI/BibTeX data.

    Returns:
        Number of papers updated.
    """
    index_path = static_export_dir / "paper_index.json"
    if not index_path.exists():
        return 0

    # Load existing index
    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    # Load DOI/BibTeX data from database
    rows = conn.execute(
        """
        SELECT p.paper_id, p.doi, b.bibtex_key
        FROM paper p
        LEFT JOIN paper_bibtex b ON p.paper_id = b.paper_id
        """
    ).fetchall()

    doi_map = {paper_id: doi for paper_id, doi, _ in rows if doi}
    bibtex_key_map = {paper_id: key for paper_id, _, key in rows if key}

    # Update index items
    updated_count = 0
    for item in index_data.get("items", []):
        paper_id = item.get("paper_id")
        if not paper_id:
            continue

        if paper_id in doi_map:
            item["doi"] = doi_map[paper_id]
            updated_count += 1

        if paper_id in bibtex_key_map:
            item["bibtex_key"] = bibtex_key_map[paper_id]

    # Write back
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    return updated_count


def create_timestamped_backup(db_path: Path) -> Path:
    """Create a timestamped backup of the database file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.name}.bak_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path
