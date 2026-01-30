from __future__ import annotations

import unittest

from deepresearch_flow.paper.snapshot.identity import (
    canonicalize_arxiv,
    canonicalize_doi,
    meta_fingerprint_divergent,
    paper_id_for_key,
)
from deepresearch_flow.paper.snapshot.text import (
    insert_cjk_spaces,
    markdown_to_plain_text,
    merge_adjacent_markers,
    remove_cjk_spaces,
    rewrite_search_query,
)


class TestIdentity(unittest.TestCase):
    def test_canonicalize_doi_prefix_decode_and_case(self) -> None:
        self.assertEqual(
            canonicalize_doi("https://doi.org/10.1000%2FXYZ."),
            "10.1000/xyz",
        )

    def test_canonicalize_arxiv_strips_version(self) -> None:
        self.assertEqual(
            canonicalize_arxiv("https://arxiv.org/abs/2301.00001v3"),
            "2301.00001",
        )

    def test_paper_id_is_stable(self) -> None:
        key = "doi:10.1000/xyz"
        self.assertEqual(paper_id_for_key(key), paper_id_for_key(key))

    def test_meta_fingerprint_divergence_requires_both_signals(self) -> None:
        prev = '{"authors":["a","b"],"title":"deep learning","venue":"x","year":"2020"}'
        cur = '{"authors":["c"],"title":"completely different","venue":"y","year":"2020"}'
        self.assertTrue(
            meta_fingerprint_divergent(
                prev,
                cur,
                min_title_similarity=0.8,
                min_author_jaccard=0.5,
            )
        )
        cur_same_authors = '{"authors":["a","b"],"title":"completely different","venue":"y","year":"2020"}'
        self.assertFalse(
            meta_fingerprint_divergent(
                prev,
                cur_same_authors,
                min_title_similarity=0.8,
                min_author_jaccard=0.5,
            )
        )


class TestSearchText(unittest.TestCase):
    def test_rewrite_search_query_cjk_phrase(self) -> None:
        self.assertEqual(rewrite_search_query("深度学习"), "\"深 度 学 习\"")

    def test_rewrite_search_query_mixed(self) -> None:
        self.assertEqual(rewrite_search_query("深度学习 transformer"), "\"深 度 学 习\" transformer")

    def test_rewrite_search_query_boolean(self) -> None:
        self.assertEqual(rewrite_search_query("lidar AND localization"), "lidar AND localization")

    def test_markdown_to_plain_text_strips_tables(self) -> None:
        md = "hello\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nworld"
        plain = markdown_to_plain_text(md)
        self.assertIn("hello", plain)
        self.assertIn("world", plain)
        self.assertNotIn("1", plain)
        self.assertNotIn("2", plain)

    def test_cjk_spacing_roundtrip(self) -> None:
        original = "深度学习"
        spaced = insert_cjk_spaces(original)
        self.assertEqual(spaced, "深 度 学 习")
        self.assertEqual(remove_cjk_spaces(spaced), original)

    def test_merge_adjacent_markers(self) -> None:
        self.assertEqual(
            merge_adjacent_markers("[[[深]]][[[度]]]"),
            "[[[深度]]]",
        )

    def test_markdown_monthly_facets_exist_after_build(self) -> None:
        # This is a lightweight schema sanity check (no full build here).
        # The snapshot DB is expected to include month support via schema tables.
        import sqlite3
        from deepresearch_flow.paper.snapshot.schema import init_snapshot_db

        conn = sqlite3.connect(":memory:")
        try:
            init_snapshot_db(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("month_count", tables)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(paper)")}
            self.assertIn("month", cols)
            self.assertIn("publication_date", cols)
        finally:
            conn.close()

    def test_extract_template_summaries(self) -> None:
        from deepresearch_flow.paper.snapshot.builder import _extract_template_summaries, _choose_preferred_summary_template

        paper = {
            "templates": {
                "simple": {"summary": "s1"},
                "deep_read": {"summary": "s2"},
            },
            "prompt_template": "deep_read",
        }
        summaries = _extract_template_summaries(paper)
        self.assertEqual(summaries["simple"], "s1")
        self.assertEqual(summaries["deep_read"], "s2")
        self.assertEqual(_choose_preferred_summary_template(paper, summaries), "deep_read")


if __name__ == "__main__":
    unittest.main()
