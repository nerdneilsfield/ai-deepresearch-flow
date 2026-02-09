from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestApiBibtexEndpoint(unittest.TestCase):
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
                    "paper-with-bib",
                    "doi:10.1145/example.doi",
                    "doi",
                    "10.1145/example.doi",
                    "Graph Neural Networks",
                    "2024",
                    "01",
                    "2024-01-01",
                    "ICLR",
                    "deep_read",
                    "preview",
                    1,
                    "sourcehash",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-01-01T00:00:00Z",
                    None,
                    None,
                ),
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
                    "paper-no-bib",
                    "meta:key",
                    "meta",
                    None,
                    "No Bib",
                    "2024",
                    "02",
                    "2024-02-01",
                    "NeurIPS",
                    "simple",
                    "preview",
                    2,
                    "sourcehash2",
                    "en",
                    "provider-x",
                    "model-y",
                    "simple",
                    "2025-01-01T00:00:00Z",
                    None,
                    None,
                ),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                ("paper-with-bib", "deep_read"),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                ("paper-no-bib", "simple"),
            )
            conn.execute(
                "INSERT INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type) VALUES (?, ?, ?, ?)",
                (
                    "paper-with-bib",
                    "@article{example, title={Graph Neural Networks}, doi={10.1145/example.doi}}",
                    "example",
                    "article",
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
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmpdir.cleanup()

    def test_detail_includes_doi(self) -> None:
        resp = self.client.get("/api/v1/papers/paper-with-bib")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["doi"], "10.1145/example.doi")

    def test_bibtex_endpoint_success(self) -> None:
        resp = self.client.get("/api/v1/papers/paper-with-bib/bibtex")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["paper_id"], "paper-with-bib")
        self.assertEqual(payload["doi"], "10.1145/example.doi")
        self.assertEqual(payload["bibtex_key"], "example")
        self.assertEqual(payload["entry_type"], "article")
        self.assertIn("@article{example", payload["bibtex_raw"])

    def test_bibtex_endpoint_paper_not_found(self) -> None:
        resp = self.client.get("/api/v1/papers/missing-paper/bibtex")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "paper_not_found")

    def test_bibtex_endpoint_bibtex_not_found(self) -> None:
        resp = self.client.get("/api/v1/papers/paper-no-bib/bibtex")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "bibtex_not_found")


class TestApiLegacyFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "legacy.db"

        conn = sqlite3.connect(str(cls.db_path))
        try:
            conn.executescript(
                """
                CREATE TABLE snapshot_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
                CREATE TABLE paper_translation (paper_id TEXT NOT NULL, lang TEXT NOT NULL, md_content_hash TEXT NOT NULL);
                CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE, paper_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE paper_author (paper_id TEXT NOT NULL, author_id INTEGER NOT NULL);
                CREATE TABLE keyword (keyword_id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE, paper_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE paper_keyword (paper_id TEXT NOT NULL, keyword_id INTEGER NOT NULL);
                CREATE TABLE institution (institution_id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE, paper_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE paper_institution (paper_id TEXT NOT NULL, institution_id INTEGER NOT NULL);
                CREATE TABLE tag (tag_id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE, paper_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE paper_tag (paper_id TEXT NOT NULL, tag_id INTEGER NOT NULL);
                """
            )
            conn.execute(
                "INSERT INTO snapshot_meta(key, value) VALUES (?, ?)",
                ("snapshot_build_id", "legacybuild"),
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

        app = create_app(
            snapshot_db=cls.db_path,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
        )
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmpdir.cleanup()

    def test_legacy_detail_returns_doi_null(self) -> None:
        resp = self.client.get("/api/v1/papers/legacy-paper")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("doi", payload)
        self.assertIsNone(payload["doi"])

    def test_legacy_bibtex_endpoint_returns_not_found(self) -> None:
        resp = self.client.get("/api/v1/papers/legacy-paper/bibtex")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "bibtex_not_found")


if __name__ == "__main__":
    unittest.main()
