from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestApiMatchBibtex(unittest.TestCase):
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
                ("snapshot_build_id", "build-test"),
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
                    "paper-1", "doi:10.1234/test", "doi", "10.1234/test",
                    "Graph Neural Networks for NLP", "2023", "01", "2023-01-01",
                    "ACL", "deep_read", "preview", 1, "hash1",
                    "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
                ),
            )
            conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (1, 'Smith, Alice', 1)")
            conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-1', 1)")
            conn.commit()
        finally:
            conn.close()

        app = create_app(
            snapshot_db=cls.db_path,
            static_base_url="http://localhost/static",
            limits=ApiLimits(),
        )
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_match_by_doi(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Test}, doi={10.1234/test}}'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matched"]), 1)
        self.assertEqual(data["matched"][0]["paper_id"], "paper-1")
        self.assertEqual(data["matched"][0]["match_method"], "doi")
        self.assertEqual(data["matched"][0]["authors"], ["Smith, Alice"])
        self.assertEqual(data["stats"]["total"], 1)
        self.assertEqual(data["stats"]["matched"], 1)
        self.assertEqual(data["stats"]["unmatched"], 0)

    def test_unmatched_entry(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Nonexistent Paper}}'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matched"]), 0)
        self.assertEqual(len(data["unmatched"]), 1)
        self.assertEqual(data["unmatched"][0]["search_query"], "Nonexistent Paper")

    def test_missing_bibtex_raw_returns_400(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_bibtex_returns_empty(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": ""},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stats"]["total"], 0)

    def test_response_includes_year_venue_authors(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Test}, doi={10.1234/test}}'},
        )
        m = resp.json()["matched"][0]
        self.assertEqual(m["year"], "2023")
        self.assertEqual(m["venue"], "ACL")
        self.assertIsInstance(m["authors"], list)

    def test_malformed_bibtex_returns_unmatched_not_500(self) -> None:
        """Malformed entries should appear as unmatched, not crash the endpoint."""
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": "@article{broken, this is not valid bibtex"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stats"]["matched"], 0)
        self.assertGreaterEqual(data["stats"]["unmatched"], 1)

    def test_mixed_valid_and_malformed_in_same_batch(self) -> None:
        """A malformed entry must not prevent valid siblings from matching."""
        bib = (
            '@article{good_entry, title={Graph Neural Networks for NLP}, doi={10.1234/test}}\n'
            '@article{bad_entry, this is broken bibtex with no closing brace\n'
        )
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": bib},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stats"]["matched"], 1)
        self.assertEqual(data["matched"][0]["paper_id"], "paper-1")
        self.assertGreaterEqual(data["stats"]["unmatched"], 1)
