from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.schema import init_snapshot_db
from deepresearch_flow.paper.snapshot.supplement import SnapshotSupplementOptions, supplement_snapshot


class TestSupplementResolution(unittest.TestCase):
    def test_supplement_resolves_existing_paper_by_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "snapshot.db"
            static_dir = root / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            input_json = root / "deep_read_supplement.json"

            conn = sqlite3.connect(str(db_path))
            try:
                init_snapshot_db(conn)
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
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "meta:source-hash",
                        "meta",
                        "Resolved Paper",
                        "2025",
                        "01",
                        "2025-01-01",
                        "acl",
                        None,
                        "simple",
                        "preview",
                        1,
                        "source-hash-123",
                        "en",
                        "",
                        "",
                        "simple",
                        "",
                        None,
                        None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            payload = {
                "template_tag": "deep_read",
                "papers": [
                    {
                        "paper_title": "Resolved Paper",
                        "source_hash": "source-hash-123",
                        "prompt_template": "deep_read",
                        "module_a": "x",
                    }
                ],
            }
            input_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            supplement_snapshot(
                SnapshotSupplementOptions(
                    snapshot_db=db_path,
                    static_export_dir=static_dir,
                    input_paths=[input_json],
                    in_place=True,
                )
            )

            check_conn = sqlite3.connect(str(db_path))
            try:
                row = check_conn.execute(
                    """
                    SELECT 1 FROM paper_summary
                    WHERE paper_id = ? AND template_tag = ?
                    """,
                    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "deep_read"),
                ).fetchone()
                self.assertIsNotNone(row)
            finally:
                check_conn.close()

    def test_supplement_fanouts_to_all_papers_with_same_source_md_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "snapshot.db"
            static_dir = root / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            input_json = root / "deep_read_supplement.json"

            conn = sqlite3.connect(str(db_path))
            try:
                init_snapshot_db(conn)
                for idx, paper_id in enumerate(
                    ["cccccccccccccccccccccccccccccccc", "dddddddddddddddddddddddddddddddd"]
                ):
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
                            f"meta:same-content-{idx}",
                            "meta",
                            f"Same Content {idx}",
                            "2025",
                            "01",
                            "2025-01-01",
                            "acl",
                            None,
                            "simple",
                            "preview",
                            idx + 1,
                            f"source-hash-{idx}",
                            "en",
                            "",
                            "",
                            "simple",
                            "",
                            None,
                            "shared-content-hash",
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

            payload = {
                "template_tag": "deep_read",
                "papers": [
                    {
                        "paper_title": "Shared Source",
                        "source_hash": "shared-content-hash",
                        "prompt_template": "deep_read",
                        "module_a": "x",
                    }
                ],
            }
            input_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            supplement_snapshot(
                SnapshotSupplementOptions(
                    snapshot_db=db_path,
                    static_export_dir=static_dir,
                    input_paths=[input_json],
                    in_place=True,
                )
            )

            check_conn = sqlite3.connect(str(db_path))
            try:
                rows = check_conn.execute(
                    """
                    SELECT paper_id FROM paper_summary
                    WHERE template_tag = 'deep_read'
                    ORDER BY paper_id
                    """
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(
                    [row[0] for row in rows],
                    ["cccccccccccccccccccccccccccccccc", "dddddddddddddddddddddddddddddddd"],
                )
            finally:
                check_conn.close()

            summary_path_c = static_dir / "summary" / "cccccccccccccccccccccccccccccccc" / "deep_read.json"
            summary_path_d = static_dir / "summary" / "dddddddddddddddddddddddddddddddd" / "deep_read.json"
            self.assertTrue(summary_path_c.exists())
            self.assertTrue(summary_path_d.exists())

    def test_supplement_resolves_existing_paper_by_source_md_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "snapshot.db"
            static_dir = root / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            input_json = root / "deep_read_supplement.json"

            conn = sqlite3.connect(str(db_path))
            try:
                init_snapshot_db(conn)
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
                        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "meta:content-hash",
                        "meta",
                        "Resolved By Content Hash",
                        "2025",
                        "01",
                        "2025-01-01",
                        "acl",
                        None,
                        "simple",
                        "preview",
                        1,
                        "stable-path-hash",
                        "en",
                        "",
                        "",
                        "simple",
                        "",
                        None,
                        "content-hash-456",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            payload = {
                "template_tag": "deep_read",
                "papers": [
                    {
                        "paper_title": "Resolved By Content Hash",
                        "source_hash": "content-hash-456",
                        "prompt_template": "deep_read",
                        "module_a": "x",
                    }
                ],
            }
            input_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            supplement_snapshot(
                SnapshotSupplementOptions(
                    snapshot_db=db_path,
                    static_export_dir=static_dir,
                    input_paths=[input_json],
                    in_place=True,
                )
            )

            check_conn = sqlite3.connect(str(db_path))
            try:
                row = check_conn.execute(
                    """
                    SELECT 1 FROM paper_summary
                    WHERE paper_id = ? AND template_tag = ?
                    """,
                    ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "deep_read"),
                ).fetchone()
                self.assertIsNotNone(row)
            finally:
                check_conn.close()
