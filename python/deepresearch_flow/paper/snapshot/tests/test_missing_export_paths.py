from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.missing import ExportMissingOptions, export_missing
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestMissingExportPaths(unittest.TestCase):
    def _insert_paper(self, conn: sqlite3.Connection, *, paper_id: str, title: str, md_hash: str) -> None:
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
                f"meta:{paper_id}",
                "meta",
                title,
                "2025",
                "01",
                "2025-01-01",
                "acl",
                None,
                "simple",
                "preview",
                1,
                f"src-{paper_id}",
                "en",
                "",
                "",
                "simple",
                "",
                None,
                md_hash,
            ),
        )

    def test_export_paths_resolve_from_static_export_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "snapshot.db"
            static_dir = root / "static"
            md_file = static_dir / "md" / "abc123.md"
            md_file.parent.mkdir(parents=True, exist_ok=True)
            md_file.write_text("# source", encoding="utf-8")
            output_paths = root / "paths.txt"

            conn = sqlite3.connect(str(db_path))
            try:
                init_snapshot_db(conn)
                self._insert_paper(conn, paper_id="p1", title="Paper One", md_hash="abc123")
                conn.commit()
            finally:
                conn.close()

            export_missing(
                ExportMissingOptions(
                    snapshot_db=db_path,
                    missing_type="template",
                    template="deep_read",
                    output_paths=output_paths,
                    static_export_dir=static_dir,
                )
            )

            lines = [line for line in output_paths.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(lines, [str(md_file.resolve())])

    def test_export_paths_fallback_to_hash_when_static_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "snapshot.db"
            output_paths = root / "paths.txt"

            conn = sqlite3.connect(str(db_path))
            try:
                init_snapshot_db(conn)
                self._insert_paper(conn, paper_id="p2", title="Paper Two", md_hash="missinghash")
                conn.commit()
            finally:
                conn.close()

            export_missing(
                ExportMissingOptions(
                    snapshot_db=db_path,
                    missing_type="template",
                    template="deep_read",
                    output_paths=output_paths,
                    static_export_dir=root / "not-exist-static",
                )
            )

            lines = [line for line in output_paths.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(lines, ["missinghash.md"])
