from __future__ import annotations

import json
import unittest
from textwrap import dedent

from deepresearch_flow.paper.snapshot import mcp_content
from deepresearch_flow.paper.snapshot.mcp_content import (
    MarkdownContentError,
    SummaryContentError,
    get_markdown_line_range,
    get_markdown_outline,
    get_summary_key,
    get_summary_keys,
    get_summary_value,
)


class TestContentWindowHelper(unittest.TestCase):
    def test_head_and_tail_clamp_short_documents(self) -> None:
        markdown = "\n".join(["one", "two", "three"])

        head = mcp_content.compute_content_window(markdown, mode="head", line_count=10)
        tail = mcp_content.compute_content_window(markdown, mode="tail", line_count=10)

        self.assertEqual(head["total_lines"], 3)
        self.assertEqual(
            head["ranges"],
            [{"start_line": 1, "end_line": 3, "content": markdown, "truncated_by_chars": False}],
        )
        self.assertFalse(head["truncated"])
        self.assertEqual(
            tail["ranges"],
            [{"start_line": 1, "end_line": 3, "content": markdown, "truncated_by_chars": False}],
        )
        self.assertFalse(tail["truncated"])

    def test_head_tail_merges_overlap_and_reports_document_coverage(self) -> None:
        markdown = "\n".join(f"line {i}" for i in range(1, 7))

        result = mcp_content.compute_content_window(
            markdown, mode="head_tail", head_lines=3, tail_lines=4
        )

        self.assertEqual(len(result["ranges"]), 1)
        self.assertEqual(result["ranges"][0]["start_line"], 1)
        self.assertEqual(result["ranges"][0]["end_line"], 6)
        self.assertFalse(result["truncated"])

    def test_head_tail_enforces_total_character_budget_across_ranges(self) -> None:
        markdown = "\n".join(["a" * 10, "middle", "b" * 10])

        result = mcp_content.compute_content_window(
            markdown,
            mode="head_tail",
            head_lines=1,
            tail_lines=1,
            max_chars_per_range=100,
            max_chars_total=15,
        )

        self.assertEqual([r["content"] for r in result["ranges"]], ["a" * 10, "b" * 5])
        self.assertTrue(result["ranges"][1]["truncated_by_chars"])
        self.assertTrue(result["truncated_by_chars"])

    def test_explicit_range_rejects_out_of_bounds_and_oversized_windows(self) -> None:
        markdown = "\n".join(f"line {i}" for i in range(1, 6))

        for start_line, end_line in [(0, 2), (2, 9), (4, 3)]:
            with self.subTest(start_line=start_line, end_line=end_line):
                with self.assertRaises(MarkdownContentError) as ctx:
                    mcp_content.compute_content_window(
                        markdown, mode="range", start_line=start_line, end_line=end_line
                    )
                self.assertEqual(ctx.exception.code, "invalid_line_range")

        with self.assertRaises(MarkdownContentError) as ctx:
            mcp_content.compute_content_window(
                markdown, mode="range", start_line=1, end_line=5, max_window_lines_per_range=4
            )
        self.assertEqual(ctx.exception.code, "window_too_large")

    def test_around_includes_center_and_clamps_computed_bounds(self) -> None:
        markdown = "\n".join(f"line {i}" for i in range(1, 6))

        first = mcp_content.compute_content_window(
            markdown, mode="around", center_line=1, before_lines=3, after_lines=1
        )
        single = mcp_content.compute_content_window(
            markdown, mode="around", center_line=3, before_lines=0, after_lines=0
        )

        self.assertEqual(first["ranges"][0]["start_line"], 1)
        self.assertEqual(first["ranges"][0]["end_line"], 2)
        self.assertEqual(single["ranges"][0]["content"], "line 3")

        with self.assertRaises(MarkdownContentError) as ctx:
            mcp_content.compute_content_window(
                markdown, mode="around", center_line=6, before_lines=1, after_lines=1
            )
        self.assertEqual(ctx.exception.code, "invalid_line_range")

    def test_rejects_invalid_modes_counts_irrelevant_params_and_empty_text(self) -> None:
        markdown = "\n".join(["one", "two"])

        with self.assertRaises(MarkdownContentError) as ctx:
            mcp_content.compute_content_window(markdown, mode="middle")
        self.assertEqual(ctx.exception.code, "invalid_window_mode")

        for invalid_count in (True, "2", 1.5, 0, -1):
            with self.subTest(invalid_count=invalid_count):
                with self.assertRaises(MarkdownContentError) as ctx:
                    mcp_content.compute_content_window(
                        markdown, mode="head", line_count=invalid_count
                    )
                self.assertEqual(ctx.exception.code, "invalid_line_count")

        with self.assertRaises(MarkdownContentError) as ctx:
            mcp_content.compute_content_window(markdown, mode="head", line_count=1, start_line=1)
        self.assertEqual(ctx.exception.code, "invalid_line_range")

        with self.assertRaises(MarkdownContentError) as ctx:
            mcp_content.compute_content_window("", mode="head")
        self.assertEqual(ctx.exception.code, "invalid_line_range")

    def test_huge_single_line_is_truncated_by_character_budget(self) -> None:
        result = mcp_content.compute_content_window(
            "x" * 20, mode="range", start_line=1, end_line=1, max_chars_per_range=8
        )

        self.assertEqual(result["ranges"][0]["content"], "x" * 8)
        self.assertTrue(result["ranges"][0]["truncated_by_chars"])
        self.assertTrue(result["truncated_by_chars"])

    def test_content_window_marks_truncated_when_full_document_is_char_truncated(self) -> None:
        result = mcp_content.compute_content_window(
            "abcdef",
            mode="range",
            start_line=1,
            end_line=1,
            max_chars_per_range=3,
            max_chars_total=3,
        )

        self.assertTrue(result["truncated"])
        self.assertTrue(result["truncated_by_chars"])
        self.assertEqual(result["ranges"][0]["content"], "abc")


class TestContentOutlineCapsAndLanguage(unittest.TestCase):
    def test_outline_caps_default_and_explicit_max_sections_in_document_order(self) -> None:
        markdown = "\n".join(f"# Section {i}" for i in range(1, 206))

        default = get_markdown_outline(markdown)
        limited = get_markdown_outline(markdown, max_sections=3)

        self.assertEqual(default["total_sections"], 205)
        self.assertEqual(default["returned_sections"], 200)
        self.assertTrue(default["truncated"])
        self.assertEqual(
            [section["title"] for section in limited["sections"]],
            ["Section 1", "Section 2", "Section 3"],
        )
        self.assertEqual(limited["total_sections"], 205)
        self.assertEqual(limited["returned_sections"], 3)
        self.assertTrue(limited["truncated"])

    def test_outline_rejects_invalid_section_counts(self) -> None:
        for max_sections in (True, "2", 1.5, 0, -1, 501):
            with self.subTest(max_sections=max_sections):
                with self.assertRaises(MarkdownContentError) as ctx:
                    get_markdown_outline("# A", max_sections=max_sections)
                self.assertEqual(ctx.exception.code, "invalid_section_count")

    def test_content_language_validation_normalizes_and_rejects_invalid_requests(self) -> None:
        self.assertEqual(
            mcp_content.resolve_content_language("translation", "ZH-CN", ["zh-cn"]),
            {"content_type": "translation", "lang": "zh-cn"},
        )
        self.assertEqual(
            mcp_content.resolve_content_language("source", None, ["zh"]),
            {"content_type": "source", "lang": None},
        )

        cases = [
            ("source", "zh", ["zh"], "invalid_lang_for_source"),
            ("translation", None, ["zh"], "missing_lang"),
            ("translation", "zh_cn", ["zh-cn"], "invalid_lang"),
            ("translation", "zh cn", ["zh-cn"], "invalid_lang"),
            ("translation", "../zh", ["zh-cn"], "invalid_lang"),
            ("translation", "en-us", ["zh-cn"], "translation_not_available"),
            ("translation", "zh", ["ZH", "zh"], "translation_not_available"),
        ]
        for content_type, lang, available, code in cases:
            with self.subTest(content_type=content_type, lang=lang, code=code):
                with self.assertRaises(MarkdownContentError) as ctx:
                    mcp_content.resolve_content_language(content_type, lang, available)
                self.assertEqual(ctx.exception.code, code)
                if available == ["ZH", "zh"]:
                    self.assertIn(
                        "duplicate_normalized_lang", ctx.exception.details.get("diagnostic", "")
                    )


class TestSummaryBoundedReads(unittest.TestCase):
    def test_summary_keys_reject_invalid_parameter_types_and_ranges(self) -> None:
        payload = json.dumps({"a": "b"}, ensure_ascii=False)

        cases = [
            {"max_depth": True},
            {"max_depth": "2"},
            {"max_depth": 1.5},
            {"max_depth": -1},
            {"max_depth": 5},
            {"include_preview": "true"},
            {"include_preview": 1},
            {"max_paths": True},
            {"max_paths": "2"},
            {"max_paths": 1.5},
            {"max_paths": 0},
            {"max_paths": 501},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SummaryContentError) as ctx:
                    get_summary_keys(payload, **kwargs)
                self.assertIn(
                    ctx.exception.code,
                    {"invalid_max_depth", "invalid_include_preview", "invalid_max_paths"},
                )

    def test_summary_keys_caps_paths_previews_and_reports_counts(self) -> None:
        payload = json.dumps({f"k{i}": "x" * 100 for i in range(60)}, ensure_ascii=False)

        result = get_summary_keys(payload, include_preview=True, max_paths=10)

        self.assertEqual(result["total_paths"], 60)
        self.assertEqual(result["returned_paths"], 10)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["paths_truncated"])
        self.assertLessEqual(sum(len(item["key"]) for item in result["paths"]), 16000)
        self.assertLessEqual(sum(len(item.get("preview", "")) for item in result["paths"]), 4000)

    def test_summary_value_returns_only_selected_scalar_content(self) -> None:
        payload = json.dumps({"target": "alpha", "sibling": "beta"}, ensure_ascii=False)

        result = get_summary_value(payload, "target")

        self.assertEqual(result["value_type"], "string")
        self.assertEqual(result["content"], "alpha")
        self.assertNotIn("beta", result["content"])
        self.assertEqual(result["content_format"], "text/plain")
        self.assertFalse(result["truncated"])

    def test_summary_value_object_default_returns_bounded_child_key_metadata(self) -> None:
        payload = json.dumps(
            {"section": {f"child_{i}": i for i in range(120)}, "other": "hidden"},
            ensure_ascii=False,
        )

        result = get_summary_value(payload, "section")

        self.assertEqual(result["value_type"], "object")
        self.assertIsNone(result["content"])
        self.assertIsNone(result["content_format"])
        self.assertEqual(result["child_count"], 120)
        self.assertEqual(result["returned_child_keys"], 100)
        self.assertTrue(result["children_truncated"])
        self.assertEqual(result["child_keys"][:2], ["child_0", "child_1"])
        self.assertLessEqual(sum(len(key) for key in result["child_keys"]), 8000)

    def test_summary_value_include_subtree_is_selected_capped_json_only(self) -> None:
        payload = json.dumps(
            {"section": {"keep": "a" * 100}, "sibling": "hidden"}, ensure_ascii=False
        )

        result = get_summary_value(payload, "section", include_subtree=True, max_chars=20)

        self.assertEqual(result["value_type"], "object")
        self.assertEqual(result["content_format"], "text/plain")
        self.assertFalse(result["content_is_valid_json"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["content"]), 20)
        self.assertNotIn("hidden", result["content"])

    def test_summary_value_rejects_root_selector_and_invalid_parameters(self) -> None:
        payload = json.dumps({"a": "b"}, ensure_ascii=False)

        with self.assertRaises(SummaryContentError) as ctx:
            get_summary_value(payload, "$")
        self.assertEqual(ctx.exception.code, "invalid_summary_key")

        cases = [
            {"include_subtree": "true"},
            {"include_subtree": 1},
            {"max_chars": True},
            {"max_chars": "2"},
            {"max_chars": 1.5},
            {"max_chars": 0},
            {"max_chars": 16001},
            {"max_child_keys": "2"},
            {"max_child_keys": 301},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SummaryContentError):
                    get_summary_value(payload, "a", **kwargs)

    def test_summary_value_not_found_uses_public_error_code(self) -> None:
        payload = json.dumps({"items": ["a"]}, ensure_ascii=False)

        for key in ("missing", "items[3]", "items.name"):
            with self.subTest(key=key):
                with self.assertRaises(SummaryContentError) as ctx:
                    get_summary_value(payload, key)
                self.assertEqual(ctx.exception.code, "summary_key_not_found")


class TestMcpContent(unittest.TestCase):
    def test_get_summary_keys_preserves_document_order(self) -> None:
        payload = json.dumps(
            {
                "summary": "alpha",
                "contributions": ["first", "second"],
                "experiments": {"main_result": "beta"},
            },
            ensure_ascii=False,
        )

        result = get_summary_keys(payload)

        self.assertEqual(result["root_type"], "object")
        self.assertEqual(
            [item["key"] for item in result["paths"]],
            [
                "summary",
                "contributions",
                "contributions[0]",
                "contributions[1]",
                "experiments",
                "experiments.main_result",
            ],
        )
        self.assertEqual(result["paths"][1]["type"], "array")
        self.assertEqual(result["paths"][1]["length"], 2)

    def test_get_summary_keys_limits_preview_to_80_code_points(self) -> None:
        payload = json.dumps({"summary": "🙂" * 81}, ensure_ascii=False)

        result = get_summary_keys(payload, include_preview=True)

        self.assertEqual(len(result["paths"][0]["preview"]), 80)
        self.assertEqual(result["paths"][0]["preview"], "🙂" * 80)

    def test_get_summary_keys_rejects_forbidden_field_name_syntax(self) -> None:
        payload = json.dumps({"bad.name": "alpha"}, ensure_ascii=False)

        with self.assertRaises(SummaryContentError) as ctx:
            get_summary_keys(payload)

        self.assertEqual(ctx.exception.code, "invalid_summary_key")

    def test_get_summary_key_serializes_arrays_as_compact_json(self) -> None:
        payload = json.dumps({"contributions": ["first", "second"]}, ensure_ascii=False)

        result = get_summary_key(payload, "contributions")

        self.assertEqual(result["value_type"], "array")
        self.assertEqual(result["content_format"], "application/json")
        self.assertEqual(result["content"], '["first","second"]')
        self.assertFalse(result["truncated"])

    def test_get_summary_key_supports_nested_array_indexes(self) -> None:
        payload = json.dumps({"matrix": [["a", "b"], ["c", "d"]]}, ensure_ascii=False)

        result = get_summary_key(payload, "matrix[0][1]")

        self.assertEqual(result["value_type"], "string")
        self.assertEqual(result["content"], "b")
        self.assertFalse(result["truncated"])

    def test_get_summary_key_rejects_non_positive_max_chars(self) -> None:
        payload = json.dumps({"summary": "alpha"}, ensure_ascii=False)

        for max_chars in (0, -1):
            with self.subTest(max_chars=max_chars):
                with self.assertRaises(SummaryContentError) as ctx:
                    get_summary_key(payload, "summary", max_chars=max_chars)
                self.assertEqual(ctx.exception.code, "invalid_max_chars")

    def test_get_markdown_outline_generates_stable_ids_and_ranges(self) -> None:
        markdown = dedent(
            """\
            #    
            intro paragraph
            ## Introduction
            body A
            ## Introduction
            body B
            ## Introduction
            body C
            ### 相关工作
            body D
            #### 版本 2024-Alpha
            body E
            ##  A/B  Test: 概览!  
            tail
            """
        )

        result = get_markdown_outline(markdown)

        self.assertEqual(result["total_lines"], 14)
        self.assertEqual(
            result["sections"],
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
                    "id": "introduction-2",
                    "title": "Introduction",
                    "level": 2,
                    "start_line": 5,
                    "end_line": 6,
                },
                {
                    "id": "introduction-3",
                    "title": "Introduction",
                    "level": 2,
                    "start_line": 7,
                    "end_line": 8,
                },
                {
                    "id": "相关工作",
                    "title": "相关工作",
                    "level": 3,
                    "start_line": 9,
                    "end_line": 10,
                },
                {
                    "id": "版本-2024-alpha",
                    "title": "版本 2024-Alpha",
                    "level": 4,
                    "start_line": 11,
                    "end_line": 12,
                },
                {
                    "id": "ab-test-概览",
                    "title": "A/B  Test: 概览!",
                    "level": 2,
                    "start_line": 13,
                    "end_line": 14,
                },
            ],
        )

    def test_get_markdown_outline_avoids_duplicate_slug_collisions(self) -> None:
        markdown = dedent(
            """\
            # Introduction
            body A
            # Introduction 2
            body B
            # Introduction
            body C
            """
        )

        result = get_markdown_outline(markdown)

        self.assertEqual(
            [section["id"] for section in result["sections"]],
            ["introduction", "introduction-2", "introduction-3"],
        )

    def test_get_markdown_outline_respects_long_fence_lengths(self) -> None:
        markdown = dedent(
            """\
            ~~~~
            ## hidden
            ~~~
            ## still hidden
            ~~~~
            ## visible
            body
            """
        )

        result = get_markdown_outline(markdown)

        self.assertEqual(
            result["sections"],
            [
                {
                    "id": "visible",
                    "title": "visible",
                    "level": 2,
                    "start_line": 6,
                    "end_line": 7,
                }
            ],
        )

    def test_get_markdown_line_range_returns_requested_slice_and_actual_bounds(self) -> None:
        markdown = "\n".join(["alpha", "beta", "gamma", "delta", "epsilon"])

        result = get_markdown_line_range(markdown, 2, 4)

        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 4)
        self.assertEqual(result["actual_start_line"], 2)
        self.assertEqual(result["actual_end_line"], 4)
        self.assertEqual(result["total_lines"], 5)
        self.assertEqual(result["content"], "beta\ngamma\ndelta")

    def test_get_markdown_line_range_clamps_out_of_bounds_ranges(self) -> None:
        markdown = "\n".join(["alpha", "beta", "gamma", "delta", "epsilon"])

        result = get_markdown_line_range(markdown, 4, 99)

        self.assertEqual(result["start_line"], 4)
        self.assertEqual(result["end_line"], 99)
        self.assertEqual(result["actual_start_line"], 4)
        self.assertEqual(result["actual_end_line"], 5)
        self.assertEqual(result["total_lines"], 5)
        self.assertEqual(result["content"], "delta\nepsilon")

    def test_get_markdown_line_range_rejects_invalid_ranges(self) -> None:
        markdown = "\n".join(["alpha", "beta", "gamma"])

        for start_line, end_line in [(0, 2), (3, 2)]:
            with self.subTest(start_line=start_line, end_line=end_line):
                with self.assertRaises(MarkdownContentError) as ctx:
                    get_markdown_line_range(markdown, start_line, end_line)
                self.assertEqual(ctx.exception.code, "invalid_line_range")

    def test_get_markdown_line_range_rejects_empty_markdown(self) -> None:
        with self.assertRaises(MarkdownContentError) as ctx:
            get_markdown_line_range("", 1, 1)

        self.assertEqual(ctx.exception.code, "invalid_line_range")


if __name__ == "__main__":
    unittest.main()
