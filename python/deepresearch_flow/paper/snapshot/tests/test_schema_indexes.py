from __future__ import annotations

import sqlite3

from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


def test_snapshot_schema_creates_paper_lookup_indexes() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        init_snapshot_db(conn)
        index_names = {row[1] for row in conn.execute("PRAGMA index_list('paper')").fetchall()}
    finally:
        conn.close()

    assert {
        "idx_paper_key",
        "idx_paper_year",
        "idx_paper_month",
        "idx_paper_venue",
        "idx_paper_index",
        "idx_paper_source_hash",
        "idx_paper_preferred_summary_template",
    } <= index_names


def test_snapshot_schema_migrates_legacy_summary_rows() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE paper_summary (paper_id TEXT NOT NULL, template_tag TEXT NOT NULL)"
        )
        init_snapshot_db(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_summary)").fetchall()
        }
    finally:
        conn.close()

    assert {"resource_path", "content_hash"} <= columns
