from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.mcp_server import (
    McpSnapshotConfig,
    McpToolError,
    configure,
    get_paper_metadata,
    get_paper_source,
    get_paper_summary,
    resource_translation,
    search_papers,
    search_papers_by_keyword,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestMcpServerSchemaCompat(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "snapshot.db"
        cls.static_dir = root / "static"
        cls.paper_id = "eb87c02de5b908dff9f91edda47364a5"

        (cls.static_dir / "summary" / cls.paper_id).mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "md").mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "md_translate" / "zh").mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "summary" / f"{cls.paper_id}.json").write_text(
            '{"template_tag":"deep_read","summary":"default summary"}',
            encoding="utf-8",
        )
        (cls.static_dir / "summary" / cls.paper_id / "deep_read.json").write_text(
            '{"template_tag":"deep_read","summary":"deep summary"}',
            encoding="utf-8",
        )
        (cls.static_dir / "summary" / cls.paper_id / "simple.json").write_text(
            '{"template_tag":"simple","summary":"simple summary"}',
            encoding="utf-8",
        )
        (cls.static_dir / "md" / "sourcehash.md").write_text(
            "# source body",
            encoding="utf-8",
        )
        (cls.static_dir / "md_translate" / "zh" / "trhash.md").write_text(
            "# 翻译内容",
            encoding="utf-8",
        )

        conn = sqlite3.connect(str(cls.db_path))
        try:
            init_snapshot_db(conn)
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                  venue, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.paper_id,
                    "meta:key",
                    "meta",
                    "Graph Neural Networks",
                    "2024",
                    "01",
                    "2024-01-01",
                    "ICLR",
                    "deep_read",
                    "Graph methods preview",
                    1,
                    "sourcekey",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-01-01T00:00:00Z",
                    "pdfhash",
                    "sourcehash",
                ),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (cls.paper_id, "deep_read"),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (cls.paper_id, "simple"),
            )
            conn.execute(
                "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                (cls.paper_id, "zh", "trhash"),
            )
            conn.execute("INSERT INTO keyword(value) VALUES (?)", ("machine learning",))
            keyword_row = conn.execute(
                "SELECT keyword_id FROM keyword WHERE value = ?",
                ("machine learning",),
            ).fetchone()
            conn.execute(
                "INSERT INTO paper_keyword(paper_id, keyword_id) VALUES (?, ?)",
                (cls.paper_id, int(keyword_row[0])),
            )
            conn.execute(
                """
                INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.paper_id,
                    "graph neural networks",
                    "graph representation learning",
                    "source text",
                    "translated text",
                    "machine learning iclr",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        configure(
            McpSnapshotConfig(
                snapshot_db=cls.db_path,
                static_base_url="",
                static_export_dir=cls.static_dir,
                limits=ApiLimits(),
                origin_allowlist=["*"],
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_get_paper_metadata_with_new_schema(self) -> None:
        payload = get_paper_metadata(self.paper_id)
        self.assertEqual(payload["paper_id"], self.paper_id)
        self.assertEqual(payload["preferred_summary_template"], "deep_read")
        self.assertEqual(payload["available_summary_templates"], ["deep_read", "simple"])
        self.assertIsNone(payload["doi"])
        self.assertIsNone(payload["arxiv_id"])
        self.assertIsNone(payload["openreview_id"])
        self.assertIsNone(payload["paper_pw_url"])

    def test_get_paper_summary_default_and_template(self) -> None:
        default_summary = get_paper_summary(self.paper_id)
        deep_read_summary = get_paper_summary(self.paper_id, template="deep_read")
        self.assertIn("default summary", default_summary)
        self.assertIn("deep summary", deep_read_summary)

    def test_get_paper_summary_template_not_available(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_summary(self.paper_id, template="unknown")
        self.assertEqual(ctx.exception.code, "template_not_available")
        self.assertEqual(
            ctx.exception.details["available_summary_templates"],
            ["deep_read", "simple"],
        )

    def test_source_and_translation_loading(self) -> None:
        source = get_paper_source(self.paper_id)
        translated = resource_translation(self.paper_id, "zh")
        self.assertIn("source body", source)
        self.assertIn("翻译内容", translated)

    def test_search_tools_use_current_schema(self) -> None:
        fts_hits = search_papers("graph", limit=5)
        facet_hits = search_papers_by_keyword("machine", limit=5)
        self.assertGreaterEqual(len(fts_hits), 1)
        self.assertGreaterEqual(len(facet_hits), 1)
        self.assertEqual(fts_hits[0]["paper_id"], self.paper_id)
        self.assertEqual(facet_hits[0]["paper_id"], self.paper_id)


if __name__ == "__main__":
    unittest.main()
