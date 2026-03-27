"""Tests for the admin API endpoints."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db

ADMIN_TOKEN = "test-secret-token"


def _make_db(tmp: Path) -> Path:
    db_path = tmp / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    try:
        init_snapshot_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
            ("snapshot_build_id", "build-test"),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _sample_paper(**overrides: object) -> dict:
    base = {
        "paper_title": "Attention Is All You Need",
        "paper_authors": ["Ashish Vaswani", "Noam Shazeer"],
        "publication_date": "2017-06",
        "publication_venue": "NeurIPS",
        "keywords": ["transformer", "attention"],
        "ai_generated_tags": ["deep-learning"],
        "paper_institutions": ["Google Brain"],
        "source_hash": "abc123",
        "output_language": "en",
        "provider": "openai",
        "model": "gpt-4",
        "templates": {
            "simple": {
                "paper_title": "Attention Is All You Need",
                "summary": "A paper about transformers.",
            }
        },
    }
    base.update(overrides)
    return base


class TestAdminAuth(unittest.TestCase):
    """Test bearer token authentication."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = _make_db(Path(cls.tmpdir.name))
        app = create_admin_app(snapshot_db=cls.db_path, admin_token=ADMIN_TOKEN)
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_missing_token(self) -> None:
        resp = self.client.post("/papers", json={"papers": []})
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_wrong_token(self) -> None:
        resp = self.client.post(
            "/papers",
            json={"papers": []},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_valid_token(self) -> None:
        resp = self.client.post(
            "/papers",
            json={"papers": []},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert resp.status_code == 200

    def test_no_token_configured(self) -> None:
        app = create_admin_app(snapshot_db=self.db_path, admin_token="")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/papers",
            json={"papers": []},
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"


class TestAdminAddPapers(unittest.TestCase):
    """Test POST /papers endpoint."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = _make_db(Path(self.tmpdir.name))
        app = create_admin_app(snapshot_db=self.db_path, admin_token=ADMIN_TOKEN)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_add_single_paper(self) -> None:
        resp = self.client.post(
            "/papers",
            json={"papers": [_sample_paper()]},
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 1
        assert data["skipped"] == 0
        assert len(data["paper_ids"]) == 1
        assert not data["errors"]

    def test_add_duplicate_is_skipped(self) -> None:
        paper = _sample_paper()
        self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 0
        assert data["skipped"] == 1

    def test_missing_title_is_error(self) -> None:
        paper = _sample_paper()
        del paper["paper_title"]
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 0
        assert len(data["errors"]) == 1
        assert "paper_title" in data["errors"][0]["error"]

    def test_invalid_json_body(self) -> None:
        resp = self.client.post(
            "/papers",
            content=b"not json",
            headers={**self.headers, "content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_wrong_body_shape(self) -> None:
        resp = self.client.post(
            "/papers",
            json={"not_papers": []},
            headers=self.headers,
        )
        assert resp.status_code == 400

    def test_batch_multiple_papers(self) -> None:
        papers = [
            _sample_paper(paper_title="Paper A", source_hash="hash_a"),
            _sample_paper(paper_title="Paper B", source_hash="hash_b"),
            _sample_paper(paper_title="Paper C", source_hash="hash_c"),
        ]
        resp = self.client.post("/papers", json={"papers": papers}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 3
        assert len(data["paper_ids"]) == 3

    def test_paper_data_persisted_correctly(self) -> None:
        paper = _sample_paper()
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        paper_id = resp.json()["paper_ids"][0]

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM paper WHERE paper_id = ?", (paper_id,)).fetchone()
            assert row is not None
            assert row["title"] == "Attention Is All You Need"
            assert row["year"] == "2017"
            assert row["venue"] == "NeurIPS"

            # Check facets
            authors = conn.execute(
                "SELECT a.value FROM author a JOIN paper_author pa ON a.author_id = pa.author_id WHERE pa.paper_id = ?",
                (paper_id,),
            ).fetchall()
            author_values = {row["value"] for row in authors}
            assert "ashish vaswani" in author_values
            assert "noam shazeer" in author_values

            # Check FTS
            fts = conn.execute(
                "SELECT * FROM paper_fts WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            assert fts is not None
            assert "attention" in fts["title"].lower()

            # Check summary template
            tmpl = conn.execute(
                "SELECT * FROM paper_summary WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            assert tmpl is not None
            assert tmpl["template_tag"] == "simple"
        finally:
            conn.close()

    def test_paper_with_bibtex(self) -> None:
        paper = _sample_paper(
            bibtex={
                "type": "inproceedings",
                "key": "vaswani2017attention",
                "fields": {
                    "title": "Attention Is All You Need",
                    "author": "Vaswani, Ashish and Shazeer, Noam",
                    "year": "2017",
                    "booktitle": "NeurIPS",
                },
                "raw": "@inproceedings{vaswani2017attention, ...}",
            }
        )
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 1

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            bib = conn.execute(
                "SELECT * FROM paper_bibtex WHERE paper_id = ?",
                (data["paper_ids"][0],),
            ).fetchone()
            assert bib is not None
            assert bib["entry_type"] == "inproceedings"
        finally:
            conn.close()


class TestAdminDeletePaper(unittest.TestCase):
    """Test DELETE /papers/{paper_id} endpoint."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = _make_db(Path(self.tmpdir.name))
        app = create_admin_app(snapshot_db=self.db_path, admin_token=ADMIN_TOKEN)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_delete_existing_paper(self) -> None:
        # Add a paper first
        resp = self.client.post(
            "/papers",
            json={"papers": [_sample_paper()]},
            headers=self.headers,
        )
        paper_id = resp.json()["paper_ids"][0]

        # Delete it
        resp = self.client.delete(f"/papers/{paper_id}", headers=self.headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["paper_id"] == paper_id

        # Verify it's gone
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)).fetchone()
            assert row is None

            # FTS should also be gone
            fts = conn.execute("SELECT 1 FROM paper_fts WHERE paper_id = ?", (paper_id,)).fetchone()
            assert fts is None

            # Cascading deletes: paper_author, paper_tag, etc.
            pa = conn.execute("SELECT 1 FROM paper_author WHERE paper_id = ?", (paper_id,)).fetchone()
            assert pa is None
        finally:
            conn.close()

    def test_delete_nonexistent_paper(self) -> None:
        resp = self.client.delete("/papers/nonexistent-id", headers=self.headers)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_delete_requires_auth(self) -> None:
        resp = self.client.delete("/papers/some-id")
        assert resp.status_code == 401


class TestAdminMountedInMainApp(unittest.TestCase):
    """Test that admin routes are accessible when mounted via create_app."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = _make_db(Path(cls.tmpdir.name))
        app = create_app(
            snapshot_db=cls.db_path,
            static_base_url="https://cdn.example.com",
            admin_token=ADMIN_TOKEN,
        )
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_admin_papers_reachable(self) -> None:
        resp = self.client.post(
            "/api/v1/admin/papers",
            json={"papers": [_sample_paper()]},
            headers=self.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["added"] == 1

    def test_admin_not_mounted_without_token(self) -> None:
        app = create_app(
            snapshot_db=self.db_path,
            static_base_url="https://cdn.example.com",
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/admin/papers",
            json={"papers": []},
            headers={"Authorization": "Bearer something"},
        )
        # Should 404 since admin routes are not mounted
        assert resp.status_code == 404


class TestDeleteFacetEdgeRecompute(unittest.TestCase):
    """Regression: facet_edge counts must be rebuilt after delete."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = _make_db(Path(self.tmpdir.name))
        app = create_admin_app(snapshot_db=self.db_path, admin_token=ADMIN_TOKEN)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_facet_edge_rebuilt_after_delete(self) -> None:
        # Add two papers sharing "deep-learning" tag
        p1 = _sample_paper(
            paper_title="Paper One",
            source_hash="h1",
            ai_generated_tags=["deep-learning"],
            keywords=["transformer"],
        )
        p2 = _sample_paper(
            paper_title="Paper Two",
            source_hash="h2",
            ai_generated_tags=["deep-learning"],
            keywords=["diffusion"],
        )
        resp = self.client.post("/papers", json={"papers": [p1, p2]}, headers=self.headers)
        ids = resp.json()["paper_ids"]
        assert len(ids) == 2

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            edges_before = conn.execute("SELECT COUNT(*) AS c FROM facet_edge").fetchone()["c"]
            assert edges_before > 0
        finally:
            conn.close()

        # Delete one paper
        self.client.delete(f"/papers/{ids[0]}", headers=self.headers)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            # facet_edge should have been rebuilt — no stale counts
            for row in conn.execute("SELECT * FROM facet_edge").fetchall():
                # Recount from paper_facet to verify
                actual = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM paper_facet a
                    JOIN paper_facet b ON a.paper_id = b.paper_id AND a.node_id = ? AND b.node_id = ?
                    """,
                    (row["node_id_a"], row["node_id_b"]),
                ).fetchone()["c"]
                assert row["paper_count"] == actual, (
                    f"facet_edge({row['node_id_a']},{row['node_id_b']}): "
                    f"stored={row['paper_count']} actual={actual}"
                )
        finally:
            conn.close()


class TestSummaryPreviewNotPolluted(unittest.TestCase):
    """Regression: summary_preview and FTS must not be '{}' when no templates."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = _make_db(Path(self.tmpdir.name))
        app = create_admin_app(snapshot_db=self.db_path, admin_token=ADMIN_TOKEN)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_preview_uses_fallback_when_no_templates(self) -> None:
        paper = {
            "paper_title": "A Paper Without Templates",
            "paper_authors": ["Alice"],
            "source_hash": "no_tpl_hash",
            "summary_preview": "This is the real preview from source DB",
        }
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 1
        paper_id = data["paper_ids"][0]

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT summary_preview FROM paper WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            assert row["summary_preview"] != "{}"
            assert row["summary_preview"] == "This is the real preview from source DB"

            fts = conn.execute(
                "SELECT summary FROM paper_fts WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            assert fts["summary"] != "{}"
        finally:
            conn.close()


    def test_no_templates_no_preview_yields_empty(self) -> None:
        """When neither templates nor summary_preview is provided,
        preview and FTS summary must be empty — not a JSON dump of the paper."""
        paper = {
            "paper_title": "Bare Paper",
            "paper_authors": ["Bob"],
            "source_hash": "bare_hash",
        }
        resp = self.client.post("/papers", json={"papers": [paper]}, headers=self.headers)
        data = resp.json()
        assert data["added"] == 1
        paper_id = data["paper_ids"][0]

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT summary_preview FROM paper WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            assert "{" not in row["summary_preview"]

            fts = conn.execute(
                "SELECT summary FROM paper_fts WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            assert "{" not in fts["summary"]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
