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
    get_paper_source_lines,
    get_paper_source_outline,
    get_paper_summary_key,
    get_paper_summary_keys,
    get_paper_source,
    get_paper_summary,
    get_paper_translation_lines,
    get_paper_translation_outline,
    resource_translation,
    search_papers,
    search_papers_by_keyword,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestMcpServerSchemaCompat(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "snapshot.db"
        cls.static_dir = root / "static"
        cls.paper_id = "eb87c02de5b908dff9f91edda47364a5"
        cls.no_bib_paper_id = "11111111111111111111111111111111"
        cls.long_summary_paper_id = "22222222222222222222222222222222"
        cls.outline_paper_id = "33333333333333333333333333333333"

        (cls.static_dir / "summary" / cls.paper_id).mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "md").mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "md_translate" / "zh").mkdir(parents=True, exist_ok=True)
        (cls.static_dir / "summary" / f"{cls.paper_id}.json").write_text(
            (
                '{"template_tag":"deep_read",'
                '"summary":"default summary",'
                '"contributions":["first contribution","second contribution"],'
                '"experiments":{"main_result":"default summary result"}}'
            ),
            encoding="utf-8",
        )
        (cls.static_dir / "summary" / cls.paper_id / "deep_read.json").write_text(
            (
                '{"template_tag":"deep_read",'
                '"summary":"deep summary",'
                '"contributions":["deep first","deep second"],'
                '"experiments":{"main_result":"deep summary result"}}'
            ),
            encoding="utf-8",
        )
        (cls.static_dir / "summary" / cls.paper_id / "simple.json").write_text(
            '{"template_tag":"simple","summary":"simple summary"}',
            encoding="utf-8",
        )
        (cls.static_dir / "md" / "sourcehash.md").write_text(
            "# source body",
            encoding="utf-8",
        )
        (cls.static_dir / "md" / "longsourcehash.md").write_text(
            "S" * 9001,
            encoding="utf-8",
        )
        (cls.static_dir / "md_translate" / "zh" / "trhash.md").write_text(
            "# 翻译内容",
            encoding="utf-8",
        )
        (cls.static_dir / "md_translate" / "zh" / "longtrhash.md").write_text(
            "T" * 9001,
            encoding="utf-8",
        )
        (cls.static_dir / "md" / "outlinehash.md").write_text(
            "\n".join(
                [
                    "#    ",
                    "intro paragraph",
                    "## Introduction",
                    "body A",
                    "##  A/B  Test: 概览!  ",
                    "tail",
                ]
            ),
            encoding="utf-8",
        )
        (cls.static_dir / "md_translate" / "zh" / "outlinetrhash.md").write_text(
            "\n".join(
                [
                    "# 翻译标题",
                    "说明",
                    "## 方法 总览",
                    "正文一",
                    "## 结果",
                    "正文二",
                ]
            ),
            encoding="utf-8",
        )
        (cls.static_dir / "summary" / f"{cls.long_summary_paper_id}.json").write_text(
            (
                '{"template_tag":"deep_read",'
                '"headline":"'
                + ("x" * 9001)
                + '"}'
            ),
            encoding="utf-8",
        )

        conn = sqlite3.connect(str(cls.db_path))
        try:
            init_snapshot_db(conn)
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                  venue, doi, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.paper_id,
                    "meta:key",
                    "meta",
                    "Graph Neural Networks",
                    "2024",
                    "01",
                    "2024-01-01",
                    "ICLR",
                    "10.1145/example.doi",
                    "deep_read",
                    "Graph methods preview",
                    1,
                    "sourcekey",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-01-01T00:00:00Z",
                    "pdfhash",
                    "sourcehash",
                ),
            )
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                  venue, doi, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.no_bib_paper_id,
                    "meta:key:2",
                    "meta",
                    "No BibTeX Paper",
                    "2024",
                    "02",
                    "2024-02-01",
                    "NeurIPS",
                    None,
                    "simple",
                    "No bib preview",
                    2,
                    "sourcekey2",
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
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                  venue, doi, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.long_summary_paper_id,
                    "meta:key:3",
                    "meta",
                    "Long Summary Paper",
                    "2024",
                    "03",
                    "2024-03-01",
                    "ICML",
                    None,
                    "deep_read",
                    "Long summary preview",
                    3,
                    "sourcekey3",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-01-01T00:00:00Z",
                    None,
                    "longsourcehash",
                ),
            )
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, title, year, month, publication_date,
                  venue, doi, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.outline_paper_id,
                    "meta:key:4",
                    "meta",
                    "Outline Paper",
                    "2024",
                    "04",
                    "2024-04-01",
                    "ACL",
                    None,
                    "deep_read",
                    "Outline preview",
                    4,
                    "outlinekey",
                    "en",
                    "provider-x",
                    "model-y",
                    "deep_read",
                    "2025-01-01T00:00:00Z",
                    None,
                    "outlinehash",
                ),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (cls.paper_id, "deep_read"),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (cls.paper_id, "simple"),
            )
            conn.execute(
                "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                (cls.paper_id, "zh", "trhash"),
            )
            conn.execute(
                "INSERT INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type) VALUES (?, ?, ?, ?)",
                (
                    cls.paper_id,
                    "@article{example, title={Graph Neural Networks}, doi={10.1145/example.doi}}",
                    "example",
                    "article",
                ),
            )
            conn.execute(
                "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                (cls.long_summary_paper_id, "deep_read"),
            )
            conn.execute(
                "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                (cls.long_summary_paper_id, "zh", "longtrhash"),
            )
            conn.execute(
                "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                (cls.outline_paper_id, "zh", "outlinetrhash"),
            )
            conn.execute("INSERT INTO keyword(value) VALUES (?)", ("machine learning",))
            keyword_row = conn.execute(
                "SELECT keyword_id FROM keyword WHERE value = ?",
                ("machine learning",),
            ).fetchone()
            conn.execute(
                "INSERT INTO paper_keyword(paper_id, keyword_id) VALUES (?, ?)",
                (cls.paper_id, int(keyword_row[0])),
            )
            conn.execute(
                """
                INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cls.paper_id,
                    "graph neural networks",
                    "graph representation learning",
                    "source text",
                    "translated text",
                    "machine learning iclr",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        configure(
            McpSnapshotConfig(
                snapshot_db=cls.db_path,
                static_base_url="",
                static_export_dir=cls.static_dir,
                limits=ApiLimits(),
                origin_allowlist=["*"],
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_get_paper_metadata_with_new_schema(self) -> None:
        payload = get_paper_metadata(self.paper_id)
        self.assertEqual(payload["paper_id"], self.paper_id)
        self.assertEqual(payload["preferred_summary_template"], "deep_read")
        self.assertEqual(payload["available_summary_templates"], ["deep_read", "simple"])
        self.assertEqual(payload["doi"], "10.1145/example.doi")
        self.assertTrue(payload["has_bibtex"])
        self.assertIsNone(payload["arxiv_id"])
        self.assertIsNone(payload["openreview_id"])
        self.assertIsNone(payload["paper_pw_url"])

    def test_get_paper_bibtex(self) -> None:
        payload = get_paper_bibtex(self.paper_id)
        self.assertEqual(payload["paper_id"], self.paper_id)
        self.assertEqual(payload["doi"], "10.1145/example.doi")
        self.assertEqual(payload["bibtex_key"], "example")
        self.assertEqual(payload["entry_type"], "article")
        self.assertIn("@article{example", payload["bibtex_raw"])

    def test_get_paper_metadata_has_bibtex_false(self) -> None:
        payload = get_paper_metadata(self.no_bib_paper_id)
        self.assertIsNone(payload["doi"])
        self.assertFalse(payload["has_bibtex"])

    def test_get_paper_bibtex_missing(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_bibtex(self.no_bib_paper_id)
        self.assertEqual(ctx.exception.code, "bibtex_not_found")

    def test_get_paper_bibtex_not_found(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_bibtex("missing-paper-id")
        self.assertEqual(ctx.exception.code, "paper_not_found")

    def test_get_paper_summary_default_and_template(self) -> None:
        default_summary = get_paper_summary(self.paper_id)
        deep_read_summary = get_paper_summary(self.paper_id, template="deep_read")
        self.assertIn("default summary", default_summary)
        self.assertIn("deep summary", deep_read_summary)

    def test_get_paper_summary_keys_and_key(self) -> None:
        keys = get_paper_summary_keys(self.paper_id)
        self.assertEqual(keys["paper_id"], self.paper_id)
        self.assertEqual(keys["root_type"], "object")
        self.assertEqual(
            [item["key"] for item in keys["paths"]],
            [
                "template_tag",
                "summary",
                "contributions",
                "contributions[0]",
                "contributions[1]",
                "experiments",
                "experiments.main_result",
            ],
        )

        nested = get_paper_summary_key(self.paper_id, "experiments.main_result")
        self.assertEqual(nested["paper_id"], self.paper_id)
        self.assertEqual(nested["key"], "experiments.main_result")
        self.assertEqual(nested["value_type"], "string")
        self.assertEqual(nested["content_format"], "text/plain")
        self.assertEqual(nested["content"], "default summary result")
        self.assertFalse(nested["truncated"])

    def test_get_paper_summary_and_summary_key_default_to_8000_char_ceiling(self) -> None:
        summary = get_paper_summary(self.long_summary_paper_id)
        headline = get_paper_summary_key(self.long_summary_paper_id, "headline")

        self.assertIn("[truncated:", summary)
        self.assertEqual(headline["content_format"], "text/plain")
        self.assertEqual(len(headline["content"]), 8000)
        self.assertTrue(headline["truncated"])

    def test_omitted_max_chars_uses_shared_8000_default_even_if_config_raises_default(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=self.static_dir,
                limits=ApiLimits(),
                origin_allowlist=["*"],
                max_chars_default=20_000,
            )
        )
        try:
            summary = get_paper_summary(self.long_summary_paper_id)
            source = get_paper_source(self.long_summary_paper_id)
            translated = resource_translation(self.long_summary_paper_id, "zh")
            headline = get_paper_summary_key(self.long_summary_paper_id, "headline")

            self.assertIn("[truncated:", summary)
            self.assertIn("[truncated:", source)
            self.assertIn("[truncated:", translated)
            self.assertEqual(len(headline["content"]), 8000)
        finally:
            configure(
                McpSnapshotConfig(
                    snapshot_db=self.db_path,
                    static_base_url="",
                    static_export_dir=self.static_dir,
                    limits=ApiLimits(),
                    origin_allowlist=["*"],
                )
            )

    def test_explicit_max_chars_override_still_applies(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=self.static_dir,
                limits=ApiLimits(),
                origin_allowlist=["*"],
                max_chars_default=20_000,
            )
        )
        try:
            summary = get_paper_summary(self.long_summary_paper_id, max_chars=9001)
            headline = get_paper_summary_key(self.long_summary_paper_id, "headline", max_chars=9001)

            self.assertGreater(len(summary), 8000)
            self.assertGreater(len(headline["content"]), 8000)
        finally:
            configure(
                McpSnapshotConfig(
                    snapshot_db=self.db_path,
                    static_base_url="",
                    static_export_dir=self.static_dir,
                    limits=ApiLimits(),
                    origin_allowlist=["*"],
                )
            )

    def test_non_positive_max_chars_is_rejected_for_content_tools(self) -> None:
        for fn, kwargs in (
            (get_paper_summary, {"paper_id": self.paper_id, "max_chars": 0}),
            (get_paper_source, {"paper_id": self.paper_id, "max_chars": -1}),
            (
                get_paper_summary_key,
                {"paper_id": self.paper_id, "key": "summary", "max_chars": 0},
            ),
        ):
            with self.subTest(function=fn.__name__, kwargs=kwargs):
                with self.assertRaises(McpToolError) as ctx:
                    fn(**kwargs)
                self.assertEqual(ctx.exception.code, "invalid_max_chars")

    def test_get_paper_summary_template_not_available(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_summary(self.paper_id, template="unknown")
        self.assertEqual(ctx.exception.code, "template_not_available")
        self.assertEqual(
            ctx.exception.details["available_summary_templates"],
            ["deep_read", "simple"],
        )

    def test_source_and_translation_loading(self) -> None:
        source = get_paper_source(self.paper_id)
        translated = resource_translation(self.paper_id, "zh")
        self.assertIn("source body", source)
        self.assertIn("翻译内容", translated)

    def test_get_paper_source_outline_and_lines(self) -> None:
        outline = get_paper_source_outline(self.outline_paper_id)
        lines = get_paper_source_lines(self.outline_paper_id, 2, 99)

        self.assertEqual(outline["paper_id"], self.outline_paper_id)
        self.assertEqual(outline["total_lines"], 6)
        self.assertEqual(
            outline["sections"],
            [
                {
                    "id": "section-1",
                    "title": "",
                    "level": 1,
                    "start_line": 1,
                    "end_line": 2,
                },
                {
                    "id": "introduction",
                    "title": "Introduction",
                    "level": 2,
                    "start_line": 3,
                    "end_line": 4,
                },
                {
                    "id": "ab-test-概览",
                    "title": "A/B  Test: 概览!",
                    "level": 2,
                    "start_line": 5,
                    "end_line": 6,
                },
            ],
        )
        self.assertEqual(lines["paper_id"], self.outline_paper_id)
        self.assertEqual(lines["start_line"], 2)
        self.assertEqual(lines["end_line"], 99)
        self.assertEqual(lines["actual_start_line"], 2)
        self.assertEqual(lines["actual_end_line"], 6)
        self.assertEqual(lines["total_lines"], 6)
        self.assertEqual(
            lines["content"],
            "intro paragraph\n## Introduction\nbody A\n##  A/B  Test: 概览!  \ntail",
        )

    def test_get_paper_source_lines_rejects_invalid_ranges(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            get_paper_source_lines(self.outline_paper_id, 4, 3)
        self.assertEqual(ctx.exception.code, "invalid_line_range")

    def test_get_paper_translation_outline_and_lines(self) -> None:
        outline = get_paper_translation_outline(self.outline_paper_id, "zh")
        lines = get_paper_translation_lines(self.outline_paper_id, "zh", 2, 5)

        self.assertEqual(outline["paper_id"], self.outline_paper_id)
        self.assertEqual(outline["lang"], "zh")
        self.assertEqual(outline["total_lines"], 6)
        self.assertEqual(
            outline["sections"],
            [
                {
                    "id": "翻译标题",
                    "title": "翻译标题",
                    "level": 1,
                    "start_line": 1,
                    "end_line": 2,
                },
                {
                    "id": "方法-总览",
                    "title": "方法 总览",
                    "level": 2,
                    "start_line": 3,
                    "end_line": 4,
                },
                {
                    "id": "结果",
                    "title": "结果",
                    "level": 2,
                    "start_line": 5,
                    "end_line": 6,
                },
            ],
        )
        self.assertEqual(lines["paper_id"], self.outline_paper_id)
        self.assertEqual(lines["lang"], "zh")
        self.assertEqual(lines["start_line"], 2)
        self.assertEqual(lines["end_line"], 5)
        self.assertEqual(lines["actual_start_line"], 2)
        self.assertEqual(lines["actual_end_line"], 5)
        self.assertEqual(lines["total_lines"], 6)
        self.assertEqual(lines["content"], "说明\n## 方法 总览\n正文一\n## 结果")

    def test_search_tools_use_current_schema(self) -> None:
        fts_hits = search_papers("graph", limit=5)
        facet_hits = search_papers_by_keyword("machine", limit=5)
        self.assertGreaterEqual(len(fts_hits), 1)
        self.assertGreaterEqual(len(facet_hits), 1)
        self.assertEqual(fts_hits[0]["paper_id"], self.paper_id)
        self.assertEqual(facet_hits[0]["paper_id"], self.paper_id)


if __name__ == "__main__":
    unittest.main()
