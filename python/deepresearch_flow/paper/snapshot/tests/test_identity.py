from __future__ import annotations

import unittest

from deepresearch_flow.paper.snapshot.identity import (
    canonicalize_arxiv,
    canonicalize_doi,
    meta_fingerprint_divergent,
    paper_id_for_key,
)
from deepresearch_flow.paper.snapshot.text import (
    markdown_to_plain_text,
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
        self.assertEqual(rewrite_search_query("深度学习"), '"深 度 学 习"')

    def test_rewrite_search_query_mixed(self) -> None:
        self.assertEqual(rewrite_search_query("深度学习 transformer"), '"深 度 学 习" transformer')

    def test_rewrite_search_query_splits_mixed_latin_cjk_tokens(self) -> None:
        self.assertEqual(rewrite_search_query("abc深度def"), 'abc "深 度" def')

    def test_rewrite_search_query_boolean(self) -> None:
        self.assertEqual(rewrite_search_query("lidar AND localization"), "lidar AND localization")

    def test_rewrite_search_query_empty_after_cleanup(self) -> None:
        self.assertEqual(rewrite_search_query("  ，。！？  "), "")

    def test_markdown_to_plain_text_strips_tables(self) -> None:
        md = "hello\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nworld"
        plain = markdown_to_plain_text(md)
        self.assertIn("hello", plain)
        self.assertIn("world", plain)
        self.assertNotIn("1", plain)
        self.assertNotIn("2", plain)

    def test_markdown_to_plain_text_handles_empty_and_breaks_and_images(self) -> None:
        self.assertEqual(markdown_to_plain_text(""), "")
        md = "hello  \nworld\n![Alt Text](https://example.com/a.png)\n![](https://example.com/b.png)"
        plain = markdown_to_plain_text(md)
        self.assertIn("hello", plain)
        self.assertIn("world", plain)
        self.assertIn("Alt Text", plain)


if __name__ == "__main__":
    unittest.main()
