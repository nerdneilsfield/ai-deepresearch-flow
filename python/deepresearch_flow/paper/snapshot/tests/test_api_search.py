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
                    "paper-split-phrase",
                    "doi:10.1145/split-phrase",
                    "doi",
                    "10.1145/split-phrase",
                    "End-to-end Methods",
                    "2025",
                    "04",
                    "2025-04-01",
                    "ICLR",
                    "deep_read",
                    "preview",
                    2,
                    "sourcehash-split",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-04-01T00:00:00Z",
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
                    "paper-split-phrase",
                    "End-to-end Methods",
                    "Retrieval methods improve quality.",
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
            mcp_access_token="test-mcp-token",
        )
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmpdir.cleanup()

    def test_search_handles_hyphenated_long_phrase_without_500(self) -> None:
        response = self.client.get(
            "/api/v1/search",
            params={"q": "end-to-end multimodal"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["paper_id"], "paper-end-to-end")

    def test_search_keeps_user_quoted_phrase_together(self) -> None:
        response = self.client.get(
            "/api/v1/search",
            params={"q": '"end-to-end retrieval"'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["paper_id"], "paper-end-to-end")

    def test_search_requires_multiple_terms_in_one_field(self) -> None:
        response = self.client.get(
            "/api/v1/search",
            params={"q": "end-to-end retrieval"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["paper_id"], "paper-end-to-end")


class TestApiSearchExpressions(unittest.TestCase):
    # (id, title, summary, year, venue, authors, keywords, tags, institutions)
    PAPERS = [
        (
            "p1",
            "Attention Is All You Need",
            "transformer model",
            "2017",
            "NeurIPS",
            ["Ashish Vaswani", "Noam Shazeer"],
            ["attention"],
            ["nlp"],
            ["Google Brain"],
        ),
        (
            "p2",
            "Deep Learning",
            "survey of neural networks",
            "2015",
            "Nature",
            ["Yann LeCun", "Geoffrey Hinton"],
            ["deep learning"],
            ["survey"],
            ["NYU"],
        ),
        (
            "p3",
            "ImageNet Classification",
            "transformer free convnet",
            "2012",
            "NeurIPS",
            ["Geoffrey Hinton", "Alex Krizhevsky"],
            ["cnn"],
            ["vision"],
            ["Toronto"],
        ),
        (
            "p4",
            "BERT Pretraining",
            "transformer pretraining",
            "2019",
            "NAACL",
            ["Jacob Devlin"],
            ["pretraining"],
            ["nlp"],
            ["Google"],
        ),
        (
            "p5",
            "Scaling Transformers",
            "transformer scaling laws",
            "2023",
            "ICLR",
            ["Jared Kaplan"],
            ["100%_scale"],
            ["nlp"],
            ["OpenAI"],
        ),
        ("p6", "Undated Notes", "transformer notes", "n/a", "", [], [], [], []),
    ]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmpdir.name) / "snapshot.db"
        conn = sqlite3.connect(str(db_path))
        try:
            init_snapshot_db(conn)
            for idx, (pid, title, summary, year, venue, *facets) in enumerate(cls.PAPERS, 1):
                conn.execute(
                    """
                    INSERT INTO paper(
                      paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                      venue, preferred_summary_template, summary_preview, paper_index,
                      source_hash, output_language, provider, model, prompt_template,
                      extracted_at
                    ) VALUES (?, ?, 'doi', ?, ?, '01', '', ?, 'deep_read', '', ?, ?, 'en',
                              'p', 'm', 'deep_read', '2026-01-01T00:00:00Z')
                    """,
                    (pid, f"k-{pid}", title, year, venue, idx, f"h-{pid}"),
                )
                conn.execute(
                    "INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)"
                    " VALUES (?, ?, ?, '', '', '')",
                    (pid, title, summary),
                )
                for table, values in zip(
                    ("author", "keyword", "tag", "institution"), facets, strict=True
                ):
                    for value in values:
                        conn.execute(f"INSERT OR IGNORE INTO {table}(value) VALUES (?)", (value,))
                        conn.execute(
                            f"INSERT INTO paper_{table}(paper_id, {table}_id) "
                            f"SELECT ?, {table}_id FROM {table} WHERE value = ?",
                            (pid, value),
                        )
                if venue:
                    conn.execute("INSERT OR IGNORE INTO venue(value) VALUES (?)", (venue,))
                    conn.execute(
                        "INSERT INTO paper_venue(paper_id, venue_id) "
                        "SELECT ?, venue_id FROM venue WHERE value = ?",
                        (pid, venue),
                    )
            conn.commit()
        finally:
            conn.close()
        app = create_app(
            snapshot_db=db_path,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token="test-mcp-token",
        )
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmpdir.cleanup()

    def ids(self, q: str, **params: str) -> list[str]:
        response = self.client.get("/api/v1/search", params={"q": q, **params})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"] >= len(payload["items"]), True)
        return [item["paper_id"] for item in payload["items"]]

    def test_author_filter_is_case_insensitive_substring(self) -> None:
        self.assertEqual(sorted(self.ids("author:HINTON")), ["p2", "p3"])

    def test_author_quoted_value_with_space(self) -> None:
        self.assertEqual(self.ids('author:"geoffrey hinton" year:2012'), ["p3"])

    def test_venue_filter(self) -> None:
        self.assertEqual(sorted(self.ids("venue:neurips")), ["p1", "p3"])

    def test_keyword_tag_institution_filters(self) -> None:
        self.assertEqual(self.ids("keyword:attention"), ["p1"])
        self.assertEqual(sorted(self.ids("tag:nlp")), ["p1", "p4", "p5"])
        self.assertEqual(self.ids("institution:toronto"), ["p3"])

    def test_like_wildcards_are_literal(self) -> None:
        self.assertEqual(self.ids("keyword:100%_scale"), ["p5"])
        self.assertEqual(self.ids("keyword:%"), ["p5"])
        self.assertEqual(self.ids("keyword:_"), ["p5"])
        self.assertEqual(self.ids("author:%"), [])

    def test_title_field_only_matches_title(self) -> None:
        self.assertEqual(self.ids("title:transformers"), ["p5"])
        self.assertEqual(self.ids("title:transformer"), [])
        self.assertEqual(self.ids('title:"attention is"'), ["p1"])

    def test_year_exact_and_ranges(self) -> None:
        self.assertEqual(self.ids("year:2019"), ["p4"])
        self.assertEqual(sorted(self.ids("year:2015..2019")), ["p1", "p2", "p4"])
        self.assertEqual(sorted(self.ids("year:2019..")), ["p4", "p5"])
        self.assertEqual(sorted(self.ids("year:..2015")), ["p2", "p3"])

    def test_invalid_year_returns_400(self) -> None:
        for bad in ("year:abc", "year:20", "year:2023..2019", "year:..", "year:2019..x"):
            response = self.client.get("/api/v1/search", params={"q": bad})
            self.assertEqual(response.status_code, 400, bad)
            payload = response.json()
            self.assertEqual(payload["error"], "invalid_query")
            self.assertTrue(payload["detail"])

    def test_exclusions(self) -> None:
        self.assertEqual(sorted(self.ids("transformer -scaling")), ["p1", "p3", "p4", "p6"])
        self.assertEqual(sorted(self.ids('transformer -"pretraining"')), ["p1", "p3", "p5", "p6"])
        self.assertEqual(sorted(self.ids("transformer -author:vaswani")), ["p3", "p4", "p5", "p6"])
        self.assertEqual(self.ids("-year:2015..2019 venue:neurips"), ["p3"])

    def test_filter_only_returns_year_desc_with_pagination(self) -> None:
        response = self.client.get(
            "/api/v1/search", params={"q": "tag:nlp", "page_size": "2", "page": "1"}
        )
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        self.assertTrue(payload["has_more"])
        self.assertEqual([i["paper_id"] for i in payload["items"]], ["p5", "p4"])
        self.assertNotIn("snippet_markdown", payload["items"][0])
        page2 = self.client.get(
            "/api/v1/search", params={"q": "tag:nlp", "page_size": "2", "page": "2"}
        ).json()
        self.assertEqual([i["paper_id"] for i in page2["items"]], ["p1"])
        self.assertFalse(page2["has_more"])

    def test_combined_text_and_filters_with_snippet(self) -> None:
        response = self.client.get(
            "/api/v1/search", params={"q": "transformer author:hinton year:2012.."}
        )
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["paper_id"], "p3")
        self.assertIn("[[[", payload["items"][0]["snippet_markdown"])

    def test_filter_total_counts_across_pages(self) -> None:
        payload = self.client.get(
            "/api/v1/search", params={"q": "transformer venue:neurips", "page_size": "1"}
        ).json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["items"]), 1)

    def test_unknown_field_is_plain_text(self) -> None:
        response = self.client.get("/api/v1/search", params={"q": "foo:bar"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)
        self.assertEqual(self.ids("transformer:"), self.ids("transformer"))

    def test_boolean_operators_still_work_around_filters(self) -> None:
        self.assertEqual(sorted(self.ids("scaling OR pretraining AND tag:nlp")), ["p4", "p5"])

    def test_sql_injection_attempt_is_inert(self) -> None:
        self.assertEqual(self.ids('author:"x\' OR 1=1 --"'), [])
        self.assertEqual(self.ids("venue:'; DROP TABLE paper; --"), [])
        self.assertEqual(sorted(self.ids("tag:nlp")), ["p1", "p4", "p5"])


if __name__ == "__main__":
    unittest.main()
