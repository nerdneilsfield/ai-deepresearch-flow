# python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.bibtex_match import (
    MatchResult,
    match_bibtex_entries,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


def _setup_db(tmpdir: Path) -> Path:
    """Create a snapshot DB with test papers."""
    db_path = tmpdir / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    try:
        init_snapshot_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
            ("snapshot_build_id", "build-test"),
        )
        # Paper 1: has DOI
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
                "paper-doi-1", "doi:10.1234/test.001", "doi", "10.1234/test.001",
                "Attention Is All You Need", "2017", "06", "2017-06-01",
                "NeurIPS", "deep_read", "preview", 1, "hash1",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 2: no DOI, unique title
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
                "paper-title-1", "arxiv:1234.5678", "arxiv", None,
                "BERT Pre-training of Deep Bidirectional Transformers", "2019", "05", "2019-05-01",
                "NAACL", "deep_read", "preview", 2, "hash2",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 3: similar title to paper 2 (for ambiguity test)
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
                "paper-title-2", "arxiv:1234.9999", "arxiv", None,
                "BERT Pre-training of Deep Bidirectional Transformers for NLU", "2019", "10", "2019-10-01",
                "EMNLP", "deep_read", "preview", 3, "hash3",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 4: similar title to paper 1 but different year
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
                "paper-year-diff", "meta:key4", "meta", None,
                "Attention Is All You Need Revisited", "2023", "01", "2023-01-01",
                "ICLR", "deep_read", "preview", 4, "hash4",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Authors for paper 1
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (1, 'Vaswani, Ashish', 1)")
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (2, 'Shazeer, Noam', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-doi-1', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-doi-1', 2)")
        # Authors for paper 2
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (3, 'Devlin, Jacob', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-title-1', 3)")
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestMatchBibtexEntries(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = _setup_db(Path(cls.tmpdir.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_doi_exact_match(self) -> None:
        bib = '@article{vaswani2017, title={Attention Is All You Need}, doi={10.1234/test.001}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-doi-1")
        self.assertEqual(result.matched[0].match_method, "doi")
        self.assertEqual(result.matched[0].bibtex_key, "vaswani2017")
        self.assertIn("Vaswani, Ashish", result.matched[0].authors)
        self.assertEqual(len(result.unmatched), 0)

    def test_doi_with_url_prefix(self) -> None:
        bib = '@article{key1, title={Test}, doi={https://doi.org/10.1234/test.001}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-doi-1")

    def test_title_fuzzy_match_unique(self) -> None:
        bib = '@article{key1, title={Attention Is All You Need Revisited}, year={2023}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-year-diff")
        self.assertEqual(result.matched[0].match_method, "title")

    def test_title_unique_match_with_large_gap(self) -> None:
        bib = '@article{key1, title={BERT Pre-training of Deep Bidirectional Transformers}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-title-1")

    def test_title_ambiguous_within_005_gap_returns_unmatched(self) -> None:
        bib = '@article{key1, title={BERT Pre-training of Deep Bidirectional Transformers for}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_title_year_mismatch_returns_unmatched(self) -> None:
        bib = '@article{key1, title={Attention Is All You Need}, year={2020}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_no_match(self) -> None:
        bib = '@article{key1, title={A Completely Unknown Paper Title}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)
        self.assertEqual(result.unmatched[0].bibtex_key, "key1")
        self.assertEqual(result.unmatched[0].search_query, "A Completely Unknown Paper Title")

    def test_malformed_entry_goes_to_unmatched(self) -> None:
        bib = '@article{broken_entry, this is not valid bibtex at all'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_multiple_entries_mixed(self) -> None:
        bib = (
            '@article{key_doi, title={Attention Is All You Need}, doi={10.1234/test.001}}\n'
            '@article{key_title, title={Attention Is All You Need Revisited}, year={2023}}\n'
            '@article{key_miss, title={Nonexistent Paper}}\n'
        )
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 2)
        self.assertEqual(len(result.unmatched), 1)
        matched_ids = {m.paper_id for m in result.matched}
        self.assertIn("paper-doi-1", matched_ids)
        self.assertIn("paper-year-diff", matched_ids)

    def test_empty_bibtex(self) -> None:
        result = match_bibtex_entries("", self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 0)
