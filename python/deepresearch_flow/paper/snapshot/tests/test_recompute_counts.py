"""Black-box tests: recompute_paper_index / recompute_facet_counts vs the pre-change SQL."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.snapshot.schema import (
    init_snapshot_db,
    recompute_facet_counts,
    recompute_paper_index,
)

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

_COUNT_TABLES = (
    ("author", "author_id", "paper_author"),
    ("keyword", "keyword_id", "paper_keyword"),
    ("institution", "institution_id", "paper_institution"),
    ("tag", "tag_id", "paper_tag"),
    ("venue", "venue_id", "paper_venue"),
    ("facet_node", "node_id", "paper_facet"),
)


def _reference_recompute_paper_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        WITH ordered AS (
          SELECT paper_id, ROW_NUMBER() OVER (ORDER BY paper_id ASC) AS idx
          FROM paper
        )
        UPDATE paper
        SET paper_index = (SELECT idx FROM ordered WHERE ordered.paper_id = paper.paper_id);
        """
    )


def _reference_recompute_facet_counts(conn: sqlite3.Connection) -> None:
    for table, id_col, join_table in _COUNT_TABLES:
        conn.execute(
            f"UPDATE {table} SET paper_count = "
            f"(SELECT COUNT(*) FROM {join_table} WHERE {id_col} = {table}.{id_col});"
        )
    conn.execute("DELETE FROM year_count;")
    conn.execute(
        "INSERT INTO year_count(year, paper_count) "
        "SELECT year, COUNT(*) AS paper_count FROM paper GROUP BY year"
    )
    conn.execute("DELETE FROM month_count;")
    conn.execute(
        "INSERT INTO month_count(month, paper_count) "
        "SELECT month, COUNT(*) AS paper_count FROM paper GROUP BY month"
    )


def _paper(title: str, doi: str, date: str, keywords: list[str], authors: list[str]) -> dict:
    return {
        "paper_title": title,
        "doi": doi,
        "paper_authors": authors,
        "publication_date": date,
        "keywords": keywords,
        "ai_generated_tags": ["tag-" + title],
        "publication_venue": "Venue " + title,
        "source_hash": title,
        "templates": {"simple": {"paper_title": title, "summary": f"{title} summary"}},
    }


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [
        ("paper", "SELECT paper_id, paper_index FROM paper ORDER BY paper_id"),
        ("year_count", "SELECT year, paper_count FROM year_count ORDER BY year"),
        ("month_count", "SELECT month, paper_count FROM month_count ORDER BY month"),
    ]
    for table, id_col, _ in _COUNT_TABLES:
        tables.append(
            (table, f"SELECT {id_col}, paper_count FROM {table} ORDER BY {id_col}"),
        )
    return {name: conn.execute(sql).fetchall() for name, sql in tables}


def _both_agree(db_path: Path, tmp_path: Path, tag: str) -> sqlite3.Connection:
    """Run reference on a copy and new functions on the original; assert same content."""
    copy_path = tmp_path / f"reference-{tag}.db"
    shutil.copy(db_path, copy_path)
    reference = sqlite3.connect(str(copy_path))
    candidate = sqlite3.connect(str(db_path))
    try:
        _reference_recompute_paper_index(reference)
        _reference_recompute_facet_counts(reference)
        recompute_paper_index(candidate)
        recompute_facet_counts(candidate)
        candidate.commit()
        assert _snapshot(candidate) == _snapshot(reference)
    finally:
        reference.close()
    return candidate


def test_recompute_matches_reference_after_insert_overwrite_delete_and_empty_dates(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    init_snapshot_db(conn)
    conn.commit()
    conn.close()
    client = TestClient(create_admin_app(snapshot_db=db_path, admin_token=TOKEN))

    papers = [
        _paper("One", "10.1/one", "2020-01", ["kw-a", "kw-b"], ["Alice", "Bob"]),
        _paper("Two", "10.1/two", "2021-05", ["kw-a"], ["Alice"]),
        _paper("Three", "10.1/three", "", ["kw-c"], ["Carol"]),
        _paper("Four", "10.1/four", "2020-01", ["kw-b"], ["Bob"]),
    ]
    resp = client.post("/papers", json={"papers": papers}, headers=HEADERS)
    assert resp.json()["added"] == 4
    ids = resp.json()["paper_ids"]
    _both_agree(db_path, tmp_path, "insert").close()

    replacement = _paper("Two", "10.1/two", "", ["kw-z"], ["Dave"])
    resp = client.post(
        "/papers", json={"papers": [replacement], "overwrite": True}, headers=HEADERS
    )
    assert resp.json()["updated"] == 1
    _both_agree(db_path, tmp_path, "overwrite").close()

    for index, paper_id in enumerate((ids[0], ids[2])):
        assert client.delete(f"/papers/{paper_id}", headers=HEADERS).status_code == 200
        _both_agree(db_path, tmp_path, f"delete-{index}").close()

    # Orphaned dimension rows keep existing and must read 0 in both implementations.
    check = sqlite3.connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM author WHERE paper_count = 0").fetchone()[0] > 0
    finally:
        check.close()


def test_recompute_on_unchanged_database_writes_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    init_snapshot_db(conn)
    conn.commit()
    conn.close()
    client = TestClient(create_admin_app(snapshot_db=db_path, admin_token=TOKEN))
    papers = [
        _paper("One", "10.1/one", "2020-01", ["kw-a"], ["Alice"]),
        _paper("Two", "10.1/two", "", ["kw-a"], ["Bob"]),
    ]
    assert client.post("/papers", json={"papers": papers}, headers=HEADERS).json()["added"] == 2
    ids = client.get("/papers/manifest", headers=HEADERS).json()["papers"]
    assert client.delete(f"/papers/{sorted(ids)[0]}", headers=HEADERS).status_code == 200

    conn = sqlite3.connect(str(db_path))
    try:
        recompute_paper_index(conn)
        recompute_facet_counts(conn)
        conn.commit()

        before = conn.total_changes
        recompute_paper_index(conn)
        assert conn.total_changes - before == 0
        before = conn.total_changes
        recompute_facet_counts(conn)
        assert conn.total_changes - before == 0
    finally:
        conn.close()


def test_recompute_facet_counts_treats_null_counts_like_the_reference(tmp_path: Path) -> None:
    """Legacy tables without NOT NULL may hold NULL counts; both versions must repair them."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    statements = ["CREATE TABLE paper (paper_id TEXT PRIMARY KEY, year TEXT, month TEXT)"]
    for table, id_col, join_table in _COUNT_TABLES:
        statements.append(
            f"CREATE TABLE {table} ({id_col} INTEGER PRIMARY KEY, paper_count INTEGER)"
        )
        statements.append(f"CREATE TABLE {join_table} (paper_id TEXT, {id_col} INTEGER)")
    statements.append("CREATE TABLE year_count (year TEXT PRIMARY KEY, paper_count INTEGER)")
    statements.append("CREATE TABLE month_count (month TEXT PRIMARY KEY, paper_count INTEGER)")
    for statement in statements:
        conn.execute(statement)
    conn.execute("INSERT INTO paper VALUES ('p1', '2020', '2020-01'), ('p2', '2020', '2020-02')")
    for table, id_col, join_table in _COUNT_TABLES:
        # id 1: used twice, NULL count; id 2: used once, correct; id 3: unused, NULL count
        conn.execute(f"INSERT INTO {table} VALUES (1, NULL), (2, 1), (3, NULL)")
        conn.execute(f"INSERT INTO {join_table} VALUES ('p1', 1), ('p2', 1), ('p1', 2)")
    conn.execute("INSERT INTO year_count VALUES ('2020', NULL), ('1999', NULL)")
    conn.execute("INSERT INTO month_count VALUES ('2020-01', NULL), ('2020-02', 2)")
    conn.commit()
    conn.close()
    copy_path = tmp_path / "reference.db"
    shutil.copy(db_path, copy_path)

    reference = sqlite3.connect(str(copy_path))
    candidate = sqlite3.connect(str(db_path))
    try:
        _reference_recompute_facet_counts(reference)
        recompute_facet_counts(candidate)
        candidate.commit()
        expected = {
            name: reference.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in ["year_count", "month_count"] + [t[0] for t in _COUNT_TABLES]
        }
        actual = {
            name: candidate.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in expected
        }
    finally:
        reference.close()
        candidate.close()

    assert actual == expected
    assert expected["author"] == [(1, 2), (2, 1), (3, 0)]
    assert expected["year_count"] == [("2020", 2)]
