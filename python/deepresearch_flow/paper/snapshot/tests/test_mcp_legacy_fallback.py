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
    get_paper_bibtex,
    get_paper_metadata,
)


class TestMcpLegacyFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "legacy_mcp.db"

        conn = sqlite3.connect(str(cls.db_path))
        try:
            conn.executescript(
                """
                CREATE TABLE paper (
                  paper_id TEXT PRIMARY KEY,
                  paper_key TEXT NOT NULL,
                  paper_key_type TEXT NOT NULL,
                  title TEXT NOT NULL,
                  year TEXT NOT NULL,
                  month TEXT NOT NULL,
                  publication_date TEXT NOT NULL,
                  venue TEXT NOT NULL,
                  preferred_summary_template TEXT NOT NULL,
                  summary_preview TEXT NOT NULL,
                  paper_index INTEGER NOT NULL DEFAULT 0,
                  source_hash TEXT,
                  output_language TEXT,
                  provider TEXT,
                  model TEXT,
                  prompt_template TEXT,
                  extracted_at TEXT,
                  pdf_content_hash TEXT,
                  source_md_content_hash TEXT
                );
                CREATE TABLE paper_summary (paper_id TEXT NOT NULL, template_tag TEXT NOT NULL);
                """
            )
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
                    "legacy-paper",
                    "meta:key",
                    "meta",
                    "Legacy Paper",
                    "2023",
                    "03",
                    "2023-03-01",
                    "ICML",
                    "simple",
                    "legacy preview",
                    1,
                    "legacyhash",
                    "en",
                    "provider-x",
                    "model-y",
                    "simple",
                    "2024-01-01T00:00:00Z",
                    None,
                    None,
                ),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                ("legacy-paper", "simple"),
            )
            conn.commit()
        finally:
            conn.close()

        configure(
            McpSnapshotConfig(
                snapshot_db=cls.db_path,
                static_base_url="",
                static_export_dir=None,
                limits=ApiLimits(),
                origin_allowlist=["*"],
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_metadata_fallback(self) -> None:
        payload = get_paper_metadata("legacy-paper")
        self.assertIsNone(payload["doi"])
        self.assertFalse(payload["has_bibtex"])

    def test_get_paper_bibtex_missing_on_legacy_schema(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_bibtex("legacy-paper")
        self.assertEqual(ctx.exception.code, "bibtex_not_found")


if __name__ == "__main__":
    unittest.main()
