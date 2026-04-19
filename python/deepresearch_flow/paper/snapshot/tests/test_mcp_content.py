from __future__ import annotations

import json
import unittest
from textwrap import dedent

from deepresearch_flow.paper.snapshot.mcp_content import (
    MarkdownContentError,
    SummaryContentError,
    get_markdown_line_range,
    get_markdown_outline,
    get_summary_key,
    get_summary_keys,
)


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
