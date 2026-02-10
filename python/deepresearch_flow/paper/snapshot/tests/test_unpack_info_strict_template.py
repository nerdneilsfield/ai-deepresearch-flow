from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.schema import init_snapshot_db
from deepresearch_flow.paper.snapshot.unpacker import SnapshotUnpackInfoOptions, unpack_info


class TestUnpackInfoStrictTemplate(unittest.TestCase):
    def _insert_paper(self, conn: sqlite3.Connection, *, paper_id: str, title: str, idx: int) -> None:
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
                "deep_read",
                "preview",
                idx,
                f"src-{paper_id}",
                "en",
                "",
                "",
                "deep_read",
                "",
                None,
                None,
            ),
        )

    def _setup_snapshot(self, root: Path) -> tuple[Path, Path]:
        db_path = root / "snapshot.db"
        static_dir = root / "static"
        (static_dir / "summary" / "p1").mkdir(parents=True, exist_ok=True)

        # p1 has requested deep_read template summary
        (static_dir / "summary" / "p1" / "deep_read.json").write_text(
            json.dumps({"summary": "deep summary p1"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # p2 only has fallback summary
        (static_dir / "summary" / "p2.json").write_text(
            json.dumps({"summary": "fallback summary p2"}, ensure_ascii=False),
            encoding="utf-8",
        )

        conn = sqlite3.connect(str(db_path))
        try:
            init_snapshot_db(conn)
            self._insert_paper(conn, paper_id="p1", title="Paper One", idx=1)
            self._insert_paper(conn, paper_id="p2", title="Paper Two", idx=2)
            conn.commit()
        finally:
            conn.close()

        return db_path, static_dir

    def test_unpack_info_uses_fallback_when_not_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path, static_dir = self._setup_snapshot(root)
            output_json = root / "out.json"

            unpack_info(
                SnapshotUnpackInfoOptions(
                    snapshot_db=db_path,
                    static_export_dir=static_dir,
                    pdf_roots=[],
                    template="deep_read",
                    output_json=output_json,
                    strict_template=False,
                )
            )

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload["template_tag"], "deep_read")
            papers = payload["papers"]
            self.assertEqual(len(papers), 2)
            by_id = {item["paper_id"]: item for item in papers}
            self.assertEqual(by_id["p1"]["summary"], "deep summary p1")
            self.assertEqual(by_id["p2"]["summary"], "fallback summary p2")

    def test_unpack_info_skips_fallback_when_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path, static_dir = self._setup_snapshot(root)
            output_json = root / "out_strict.json"

            unpack_info(
                SnapshotUnpackInfoOptions(
                    snapshot_db=db_path,
                    static_export_dir=static_dir,
                    pdf_roots=[],
                    template="deep_read",
                    output_json=output_json,
                    strict_template=True,
                )
            )

            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload["template_tag"], "deep_read")
            papers = payload["papers"]
            self.assertEqual(len(papers), 1)
            self.assertEqual(papers[0]["paper_id"], "p1")
            self.assertEqual(papers[0]["summary"], "deep summary p1")


if __name__ == "__main__":
    unittest.main()
