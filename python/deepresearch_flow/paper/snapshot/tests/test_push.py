"""Tests for the push module (extract from DB + push to remote)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.snapshot.push import (
    RemoteConfig,
    extract_papers_from_db,
    load_remote_config,
    push_papers,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db, recompute_facet_counts

ADMIN_TOKEN = "test-push-token"


def _make_source_db(tmp: Path) -> Path:
    """Create a source snapshot DB with sample papers."""
    db_path = tmp / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        init_snapshot_db(conn)

        # Insert two papers
        for i, (pid, title, year) in enumerate([
            ("paper-alpha", "Alpha Paper on Transformers", "2023"),
            ("paper-beta", "Beta Paper on Diffusion", "2024"),
        ]):
            conn.execute(
                """
                INSERT INTO paper(
                    paper_id, paper_key, paper_key_type, doi, title, year, month,
                    publication_date, venue, preferred_summary_template, summary_preview,
                    source_hash, output_language, provider, model, prompt_template,
                    extracted_at, pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid, f"doi:10.1234/{pid}", "doi", f"10.1234/{pid}",
                    title, year, "06", f"{year}-06-01",
                    "NeurIPS", "simple", f"Preview of {title}",
                    f"hash_{pid}", "en", "openai", "gpt-4", "simple",
                    "2025-01-01T00:00:00Z", f"pdf_{pid}", f"md_{pid}",
                ),
            )

            # Authors
            conn.execute("INSERT OR IGNORE INTO author(value) VALUES (?)", ("alice",))
            conn.execute("INSERT OR IGNORE INTO author(value) VALUES (?)", ("bob",))
            alice_id = conn.execute("SELECT author_id FROM author WHERE value = 'alice'").fetchone()["author_id"]
            bob_id = conn.execute("SELECT author_id FROM author WHERE value = 'bob'").fetchone()["author_id"]
            conn.execute("INSERT OR IGNORE INTO paper_author(paper_id, author_id) VALUES (?, ?)", (pid, alice_id))
            if i == 0:
                conn.execute("INSERT OR IGNORE INTO paper_author(paper_id, author_id) VALUES (?, ?)", (pid, bob_id))

            # Keywords
            conn.execute("INSERT OR IGNORE INTO keyword(value) VALUES (?)", ("deep learning",))
            kw_id = conn.execute("SELECT keyword_id FROM keyword WHERE value = 'deep learning'").fetchone()["keyword_id"]
            conn.execute("INSERT OR IGNORE INTO paper_keyword(paper_id, keyword_id) VALUES (?, ?)", (pid, kw_id))

            # Tags
            conn.execute("INSERT OR IGNORE INTO tag(value) VALUES (?)", ("ml",))
            tag_id = conn.execute("SELECT tag_id FROM tag WHERE value = 'ml'").fetchone()["tag_id"]
            conn.execute("INSERT OR IGNORE INTO paper_tag(paper_id, tag_id) VALUES (?, ?)", (pid, tag_id))

            # Summary template
            conn.execute(
                "INSERT OR IGNORE INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (pid, "simple"),
            )

            # BibTeX (only for first paper)
            if i == 0:
                conn.execute(
                    "INSERT INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type) VALUES (?, ?, ?, ?)",
                    (pid, "@article{alpha2023, ...}", "alpha2023", "article"),
                )

            # Translation (only for second paper)
            if i == 1:
                conn.execute(
                    "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                    (pid, "zh", "trans_hash_beta"),
                )

        recompute_facet_counts(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestExtractPapersFromDb(TestCase):
    """Test extracting papers from a snapshot DB."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.db_path = _make_source_db(self.tmp)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_extracts_all_papers(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        assert len(papers) == 2

    def test_paper_fields(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert alpha["paper_title"] == "Alpha Paper on Transformers"
        assert alpha["publication_venue"] == "NeurIPS"
        assert alpha["output_language"] == "en"
        assert alpha["doi"] == "10.1234/paper-alpha"
        assert alpha["pdf_content_hash"] == "pdf_paper-alpha"

    def test_facet_extraction(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert "alice" in alpha["paper_authors"]
        assert "bob" in alpha["paper_authors"]
        assert "deep learning" in alpha["keywords"]
        assert "ml" in alpha["ai_generated_tags"]

        beta = next(p for p in papers if p["paper_id"] == "paper-beta")
        assert "alice" in beta["paper_authors"]
        assert "bob" not in beta["paper_authors"]

    def test_bibtex_extraction(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert alpha["bibtex"]["key"] == "alpha2023"
        assert alpha["bibtex"]["type"] == "article"

        beta = next(p for p in papers if p["paper_id"] == "paper-beta")
        assert "bibtex" not in beta

    def test_translation_extraction(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert "translations" not in alpha

        beta = next(p for p in papers if p["paper_id"] == "paper-beta")
        assert beta["translations"] == {"zh": "trans_hash_beta"}

    def test_summary_payloads_from_static_dir(self) -> None:
        # Create summary JSON files
        summary_dir = self.tmp / "static" / "summary" / "paper-alpha"
        summary_dir.mkdir(parents=True)
        payload = {"paper_title": "Alpha", "summary": "A summary."}
        (summary_dir / "simple.json").write_text(json.dumps(payload), encoding="utf-8")

        papers = extract_papers_from_db(
            self.db_path,
            static_export_dir=self.tmp / "static",
        )
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert alpha["templates"]["simple"]["summary"] == "A summary."

    def test_no_templates_without_static_dir(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        # Without static dir, templates should not be included (avoids FTS pollution)
        assert "templates" not in alpha

    def test_summary_preview_carried(self) -> None:
        papers = extract_papers_from_db(self.db_path)
        alpha = next(p for p in papers if p["paper_id"] == "paper-alpha")
        assert alpha["summary_preview"] == "Preview of Alpha Paper on Transformers"


class TestLoadRemoteConfig(TestCase):
    """Test loading remote.toml."""

    def test_load_basic_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://api.example.com"\nadmin_token = "my-token"\n')
            f.flush()
            cfg = load_remote_config(Path(f.name))
        assert cfg.api_base_url == "https://api.example.com"
        assert cfg.admin_token == "my-token"
        assert cfg.batch_size == 100

    def test_load_env_token(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://api.example.com"\nadmin_token = "env:TEST_PUSH_TOKEN"\n')
            f.flush()
            with mock.patch.dict("os.environ", {"TEST_PUSH_TOKEN": "resolved-secret"}):
                cfg = load_remote_config(Path(f.name))
        assert cfg.admin_token == "resolved-secret"

    def test_missing_url_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\nadmin_token = "tok"\n')
            f.flush()
            with self.assertRaises(ValueError):
                load_remote_config(Path(f.name))

    def test_missing_token_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://api.example.com"\n')
            f.flush()
            with self.assertRaises(ValueError):
                load_remote_config(Path(f.name))

    def test_custom_batch_size(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://x.com"\nadmin_token = "t"\nbatch_size = 50\n')
            f.flush()
            cfg = load_remote_config(Path(f.name))
        assert cfg.batch_size == 50

    def test_batch_size_zero_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://x.com"\nadmin_token = "t"\nbatch_size = 0\n')
            f.flush()
            with self.assertRaises(ValueError):
                load_remote_config(Path(f.name))

    def test_batch_size_over_limit_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('[remote]\napi_base_url = "https://x.com"\nadmin_token = "t"\nbatch_size = 300\n')
            f.flush()
            with self.assertRaises(ValueError):
                load_remote_config(Path(f.name))


class TestPushPapers(TestCase):
    """Integration test: extract from local DB, push to a real admin API."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.source_db = _make_source_db(self.tmp)

        # Create a separate target DB for the admin API
        self.target_db = self.tmp / "target.db"
        conn = sqlite3.connect(str(self.target_db))
        try:
            init_snapshot_db(conn)
            conn.commit()
        finally:
            conn.close()

        self.admin_app = create_admin_app(snapshot_db=self.target_db, admin_token=ADMIN_TOKEN)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_full_push_flow(self) -> None:
        papers = extract_papers_from_db(self.source_db)
        assert len(papers) == 2

        # Use TestClient as a transport for httpx
        client = TestClient(self.admin_app, raise_server_exceptions=False)

        # Mock httpx.Client to use Starlette TestClient
        batches_received: list[dict] = []

        def mock_post(url: str, json: dict, headers: dict) -> mock.MagicMock:
            resp = client.post("/papers", json=json, headers=headers)
            result = mock.MagicMock()
            result.status_code = resp.status_code
            result.json.return_value = resp.json()
            result.raise_for_status = mock.MagicMock()
            if resp.status_code >= 400:
                result.raise_for_status.side_effect = Exception(f"HTTP {resp.status_code}")
            batches_received.append(resp.json())
            return result

        config = RemoteConfig(
            api_base_url="http://testserver",
            admin_token=ADMIN_TOKEN,
            batch_size=10,
        )

        with mock.patch("deepresearch_flow.paper.snapshot.push.httpx.Client") as mock_client_cls:
            mock_client_instance = mock.MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__enter__ = mock.MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = mock.MagicMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            stats = push_papers(papers, config)

        assert stats.added == 2
        assert stats.skipped == 0
        assert not stats.errors
        assert len(stats.paper_ids) == 2
        assert stats.batches_sent == 1

        # Verify target DB has the papers
        conn = sqlite3.connect(str(self.target_db))
        conn.row_factory = sqlite3.Row
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM paper").fetchone()["c"]
            assert count == 2

            # Check authors were linked
            authors = conn.execute(
                "SELECT a.value FROM author a JOIN paper_author pa ON a.author_id = pa.author_id WHERE pa.paper_id = 'paper-alpha'"
            ).fetchall()
            author_names = {row["value"] for row in authors}
            assert "alice" in author_names
        finally:
            conn.close()

    def test_push_idempotent(self) -> None:
        """Pushing the same papers twice should skip on second push."""
        papers = extract_papers_from_db(self.source_db)
        client = TestClient(self.admin_app, raise_server_exceptions=False)

        def mock_post(url: str, json: dict, headers: dict) -> mock.MagicMock:
            resp = client.post("/papers", json=json, headers=headers)
            result = mock.MagicMock()
            result.status_code = resp.status_code
            result.json.return_value = resp.json()
            result.raise_for_status = mock.MagicMock()
            return result

        config = RemoteConfig(api_base_url="http://testserver", admin_token=ADMIN_TOKEN, batch_size=10)

        with mock.patch("deepresearch_flow.paper.snapshot.push.httpx.Client") as mock_cls:
            inst = mock.MagicMock()
            inst.post = mock_post
            inst.__enter__ = mock.MagicMock(return_value=inst)
            inst.__exit__ = mock.MagicMock(return_value=False)
            mock_cls.return_value = inst

            stats1 = push_papers(papers, config)
            stats2 = push_papers(papers, config)

        assert stats1.added == 2
        assert stats2.added == 0
        assert stats2.skipped == 2

    def test_batch_splitting(self) -> None:
        """Papers should be split across multiple batches."""
        papers = extract_papers_from_db(self.source_db)
        client = TestClient(self.admin_app, raise_server_exceptions=False)
        batch_calls: list[int] = []

        def mock_post(url: str, json: dict, headers: dict) -> mock.MagicMock:
            batch_calls.append(len(json.get("papers", [])))
            resp = client.post("/papers", json=json, headers=headers)
            result = mock.MagicMock()
            result.status_code = resp.status_code
            result.json.return_value = resp.json()
            result.raise_for_status = mock.MagicMock()
            return result

        config = RemoteConfig(api_base_url="http://testserver", admin_token=ADMIN_TOKEN, batch_size=1)

        with mock.patch("deepresearch_flow.paper.snapshot.push.httpx.Client") as mock_cls:
            inst = mock.MagicMock()
            inst.post = mock_post
            inst.__enter__ = mock.MagicMock(return_value=inst)
            inst.__exit__ = mock.MagicMock(return_value=False)
            mock_cls.return_value = inst

            stats = push_papers(papers, config)

        assert stats.batches_sent == 2
        assert batch_calls == [1, 1]
        assert stats.added == 2
