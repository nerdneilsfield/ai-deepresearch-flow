from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestApiSearchEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "snapshot.db"

        conn = sqlite3.connect(str(cls.db_path))
        try:
            init_snapshot_db(conn)
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
                ("snapshot_build_id", "build123"),
            )
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
                  venue, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "paper-end-to-end",
                    "doi:10.1145/end-to-end",
                    "doi",
                    "10.1145/end-to-end",
                    "End-to-end Retrieval for Multimodal Search",
                    "2026",
                    "04",
                    "2026-04-01",
                    "ICLR",
                    "deep_read",
                    "preview",
                    1,
                    "sourcehash",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2026-04-01T00:00:00Z",
                    None,
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "paper-end-to-end",
                    "End-to-end Retrieval for Multimodal Search",
                    "Study of long search phrases in retrieval systems.",
                    "",
                    "",
                    "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        app = create_app(
            snapshot_db=cls.db_path,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
        )
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmpdir.cleanup()

    def test_search_handles_hyphenated_long_phrase_without_500(self) -> None:
        response = self.client.get(
            "/api/v1/search",
            params={"q": "end-to-end retrieval"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["paper_id"], "paper-end-to-end")


if __name__ == "__main__":
    unittest.main()
