from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.builder import SnapshotBuildOptions, build_snapshot
from deepresearch_flow.paper.snapshot.identity import PaperKeyCandidate, build_paper_key_candidates
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


def _meta_candidate(paper: dict[str, object]) -> PaperKeyCandidate:
    candidates = build_paper_key_candidates(paper)
    for candidate in candidates:
        if candidate.key_type == "meta":
            return candidate
    raise AssertionError("meta candidate missing")


def _insert_previous_paper(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    candidate: PaperKeyCandidate,
    title: str,
    year: str,
    venue: str,
    doi: str | None,
    bibtex_raw: str | None,
    bibtex_key: str | None,
    entry_type: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO paper(
          paper_id, paper_key, paper_key_type, title, year, month, publication_date,
          venue, doi, preferred_summary_template, summary_preview, paper_index, source_hash,
          output_language, provider, model, prompt_template, extracted_at,
          pdf_content_hash, source_md_content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            candidate.paper_key,
            candidate.key_type,
            title,
            year,
            "01",
            f"{year}-01-01",
            venue,
            doi,
            "simple",
            "summary preview",
            0,
            f"source-{paper_id}",
            "en",
            "",
            "",
            "simple",
            "",
            None,
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO paper_key_alias(paper_key, paper_id, paper_key_type, meta_fingerprint)
        VALUES (?, ?, ?, ?)
        """,
        (
            candidate.paper_key,
            paper_id,
            candidate.key_type,
            candidate.meta_fingerprint if candidate.key_type == "meta" else None,
        ),
    )
    if bibtex_raw:
        conn.execute(
            """
            INSERT INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type)
            VALUES (?, ?, ?, ?)
            """,
            (paper_id, bibtex_raw, bibtex_key, entry_type),
        )


class TestBuilderMetadataInheritance(unittest.TestCase):
    def test_rebuild_inherits_and_overrides_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_db = root / "previous.db"
            output_db = root / "output.db"
            static_dir = root / "static"
            input_path = root / "papers.json"

            inherit_paper: dict[str, object] = {
                "paper_title": "Inherited Metadata Paper",
                "paper_authors": ["Alice Example"],
                "publication_date": "2021",
                "publication_venue": "ACL",
                "summary": "inherit summary",
            }
            override_paper: dict[str, object] = {
                "paper_title": "Override Metadata Paper",
                "paper_authors": ["Bob Example"],
                "publication_date": "2022",
                "publication_venue": "EMNLP",
                "summary": "override summary",
                "doi": "10.2000/current-override",
                "bibtex": {
                    "key": "override2022",
                    "type": "article",
                    "fields": {
                        "title": "Override Metadata Paper",
                        "year": "2022",
                        "doi": "10.2000/current-override",
                    },
                    "raw_entry": (
                        "@article{override2022, title={Override Metadata Paper}, "
                        "year={2022}, doi={10.2000/current-override}}"
                    ),
                },
            }

            payload = {"template_tag": "simple", "papers": [inherit_paper, override_paper]}
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            conn = sqlite3.connect(str(previous_db))
            try:
                init_snapshot_db(conn)
                _insert_previous_paper(
                    conn,
                    paper_id="11111111111111111111111111111111",
                    candidate=_meta_candidate(inherit_paper),
                    title="Inherited Metadata Paper",
                    year="2021",
                    venue="acl",
                    doi="10.1000/previous-inherit",
                    bibtex_raw=(
                        "@article{oldinherit, title={Inherited Metadata Paper}, "
                        "year={2021}, doi={10.1000/previous-inherit}}"
                    ),
                    bibtex_key="oldinherit",
                    entry_type="article",
                )
                _insert_previous_paper(
                    conn,
                    paper_id="22222222222222222222222222222222",
                    candidate=_meta_candidate(override_paper),
                    title="Override Metadata Paper",
                    year="2022",
                    venue="emnlp",
                    doi="10.2000/previous-override",
                    bibtex_raw=(
                        "@article{oldoverride, title={Override Metadata Paper}, "
                        "year={2022}, doi={10.2000/previous-override}}"
                    ),
                    bibtex_key="oldoverride",
                    entry_type="article",
                )
                conn.commit()
            finally:
                conn.close()

            build_snapshot(
                SnapshotBuildOptions(
                    input_paths=[input_path],
                    bibtex_path=None,
                    md_roots=[],
                    md_translated_roots=[],
                    pdf_roots=[],
                    output_db=output_db,
                    static_export_dir=static_dir,
                    previous_snapshot_db=previous_db,
                )
            )

            output_conn = sqlite3.connect(str(output_db))
            output_conn.row_factory = sqlite3.Row
            try:
                inherit_row = output_conn.execute(
                    """
                    SELECT p.paper_id, p.doi, b.bibtex_key, b.bibtex_raw
                    FROM paper p
                    LEFT JOIN paper_bibtex b ON b.paper_id = p.paper_id
                    WHERE p.title = ?
                    """,
                    ("Inherited Metadata Paper",),
                ).fetchone()
                self.assertIsNotNone(inherit_row)
                self.assertEqual(inherit_row["paper_id"], "11111111111111111111111111111111")
                self.assertEqual(inherit_row["doi"], "10.1000/previous-inherit")
                self.assertEqual(inherit_row["bibtex_key"], "oldinherit")
                self.assertIn("10.1000/previous-inherit", inherit_row["bibtex_raw"])

                override_row = output_conn.execute(
                    """
                    SELECT p.paper_id, p.doi, b.bibtex_key, b.bibtex_raw
                    FROM paper p
                    LEFT JOIN paper_bibtex b ON b.paper_id = p.paper_id
                    WHERE p.title = ?
                    """,
                    ("Override Metadata Paper",),
                ).fetchone()
                self.assertIsNotNone(override_row)
                self.assertEqual(override_row["paper_id"], "22222222222222222222222222222222")
                self.assertEqual(override_row["doi"], "10.2000/current-override")
                self.assertEqual(override_row["bibtex_key"], "override2022")
                self.assertIn("10.2000/current-override", override_row["bibtex_raw"])
            finally:
                output_conn.close()

    def test_builder_indexes_doi_in_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_db = root / "output.db"
            static_dir = root / "static"
            input_path = root / "papers.json"

            payload = {
                "template_tag": "simple",
                "papers": [
                    {
                        "paper_title": "FTS DOI Coverage Paper",
                        "paper_authors": ["Carol Example"],
                        "publication_date": "2023",
                        "publication_venue": "ICLR",
                        "summary": "fts summary",
                        "doi": "10.5555/doitestalpha",
                    }
                ],
            }
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            build_snapshot(
                SnapshotBuildOptions(
                    input_paths=[input_path],
                    bibtex_path=None,
                    md_roots=[],
                    md_translated_roots=[],
                    pdf_roots=[],
                    output_db=output_db,
                    static_export_dir=static_dir,
                    previous_snapshot_db=None,
                )
            )

            conn = sqlite3.connect(str(output_db))
            try:
                hits = conn.execute(
                    """
                    SELECT p.title
                    FROM paper_fts
                    JOIN paper p ON p.paper_id = paper_fts.paper_id
                    WHERE paper_fts MATCH ?
                    """,
                    ("doitestalpha",),
                ).fetchall()
                self.assertEqual(len(hits), 1)
                self.assertEqual(str(hits[0][0]), "FTS DOI Coverage Paper")
            finally:
                conn.close()
