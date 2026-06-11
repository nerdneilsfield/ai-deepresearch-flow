"""Tests for PaddleOCR-compatible fix rules in fixers.py."""

from __future__ import annotations

import pytest

from deepresearch_flow.translator.fixers import (
    LinkProcessor,
    ReferenceProcessor,
    fix_markdown,
    preserve_heading_levels,
    fix_html_table_math_spaces,
    fix_math_delimiter_spaces,
    fix_nested_mailto,
    fix_non_math_in_delimiters,
)


# ---------------------------------------------------------------------------
# fix_nested_mailto
# ---------------------------------------------------------------------------


class TestFixNestedMailto:
    def test_triple_nested(self):
        text = "<mailto:<mailto:<mailto:tobi@ini.uzh.ch>>>"
        assert fix_nested_mailto(text) == "<mailto:tobi@ini.uzh.ch>"

    def test_double_nested(self):
        text = "<mailto:<mailto:foo@bar.com>>"
        assert fix_nested_mailto(text) == "<mailto:foo@bar.com>"

    def test_single_unchanged(self):
        text = "<mailto:foo@bar.com>"
        assert fix_nested_mailto(text) == "<mailto:foo@bar.com>"

    def test_no_mailto_unchanged(self):
        text = "hello world foo@bar.com"
        assert fix_nested_mailto(text) == text

    def test_idempotent(self):
        text = "<mailto:<mailto:<mailto:a@b.c>>>"
        result = fix_nested_mailto(text)
        assert fix_nested_mailto(result) == result


# ---------------------------------------------------------------------------
# pre-protect safety
# ---------------------------------------------------------------------------


class TestPreProtectSafety:
    def test_reference_markers_inside_math_and_code_unchanged(self):
        text = "Math \\(see [1]\\) and code ` [2] `\n```\n[3]\n```"
        assert ReferenceProcessor().fix_references(text) == text

    def test_fix_markdown_expands_split_reference_ranges(self):
        text = "See [2]-[7] for details."
        result = fix_markdown(text, "normal")
        assert result == "See [^2] [^3] [^4] [^5] [^6] [^7] for details."

    def test_fix_markdown_expands_split_reference_ranges_with_spaces(self):
        text = "See [2] - [4] for details."
        result = fix_markdown(text, "normal")
        assert result == "See [^2] [^3] [^4] for details."

    def test_urls_emails_phones_inside_protected_looking_content_unchanged(self):
        text = (
            "Math \\(see https://example.com, foo@bar.com, 555-123-4567\\)\n"
            "```\n"
            "https://example.com foo@bar.com 555-123-4567\n"
            "```"
        )
        assert LinkProcessor().fix_links(text) == text

    def test_nested_mailto_inside_code_unchanged(self):
        text = "```\n<mailto:<mailto:foo@bar.com>>\n```\n`<mailto:<mailto:bar@baz.com>>`"
        assert fix_nested_mailto(text) == text

    def test_fix_markdown_does_not_mutate_paren_math_before_protection(self):
        text = r"Before \(see [1] https://example.com foo@bar.com 555-123-4567\) after"
        assert fix_markdown(text, "normal") == text

    def test_fix_markdown_preserves_footnote_state_across_code_fence(self):
        text = "# Notes\n1) first\n```\ncode\n```\n2) second"
        result = fix_markdown(text, "normal")
        assert "[^1]: first" in result
        assert "[^2]: second" in result
        assert "```\ncode\n```" in result

    def test_fix_markdown_keeps_footnote_entries_on_separate_lines(self):
        text = "# References\n1) first\n2) second\n"
        result = fix_markdown(text, "normal")
        assert "[^1]: first\n[^2]: second" in result

    def test_fix_markdown_resets_notes_state_after_next_heading(self):
        text = "# References\n1) first\n## Appendix\n2) second\n"
        result = fix_markdown(text, "normal")
        assert "[^1]: first\n" in result
        assert "## Appendix\n2) second" in result

    def test_fix_markdown_resets_plain_notes_state_after_non_note_paragraph(self):
        text = "References\n1) first\nAppendix\n2) second\n"
        result = fix_markdown(text, "normal")
        assert "[^1]: first\n" in result
        assert "Appendix\n2) second" in result

    def test_fix_markdown_does_not_rewrite_reference_like_brackets_inside_urls(self):
        text = "See https://api.example.com/v1[2]/data for details."
        result = fix_markdown(text, "normal")
        assert "<https://api.example.com/v1[2]/data>" in result
        assert "[^2]" not in result

    def test_link_processor_preserves_balanced_parentheses_in_urls(self):
        text = "See https://en.wikipedia.org/wiki/Function_(mathematics)."
        result = LinkProcessor().fix_links(text)
        assert result == "See <https://en.wikipedia.org/wiki/Function_(mathematics)>."

    def test_aggressive_title_cleanup_skips_fenced_code_blocks(self):
        text = "# 1. Intro\n```\n# 2. Inside code\n```\n# 3. Outro"
        result = fix_markdown(text, "aggressive")
        assert "## 1. Intro" in result
        assert "```\n# 2. Inside code\n```" in result

    def test_fix_markdown_does_not_wrap_algorithm_parameters_paragraph_as_pseudocode(self):
        text = "Algorithm parameters are tuned on Traverse 1, Part 1 and the same values are used elsewhere.\n"
        result = fix_markdown(text, "moderate")
        assert "```pseudo" not in result
        assert "Algorithm parameters are tuned" in result

    def test_fix_markdown_still_wraps_numbered_algorithm_blocks(self):
        text = "Algorithm 1 Main loop\nInput: x\n"
        result = fix_markdown(text, "moderate")
        assert "```pseudo" in result
        assert "// Algorithm 1: Main loop" in result


class TestHeadingLevelPreservation:
    def test_preserve_heading_levels_restores_original_hash_depth(self):
        original = "# Title\n### I. INTRODUCTION\n### II. RELATED WORK\n### A. Detail\n"
        formatted = "# Title\n## I. INTRODUCTION\n### II. RELATED WORK\n### A. Detail\n"
        restored = preserve_heading_levels(original, formatted)
        assert restored == original


# ---------------------------------------------------------------------------
# fix_non_math_in_delimiters
# ---------------------------------------------------------------------------


class TestFixNonMathInDelimiters:
    def test_refs_in_dollars(self):
        # "$ [^2] [^36] [^22] $" → "[^2] [^36] [^22]", surrounding spaces stay
        text = "text $ [^2] [^36] [^22] $ more"
        result = fix_non_math_in_delimiters(text)
        assert "[^2] [^36] [^22]" in result
        assert "$" not in result

    def test_bare_refs_in_dollars(self):
        text = "text $ [2] [36] $ more"
        result = fix_non_math_in_delimiters(text)
        assert "[2] [36]" in result
        assert "$" not in result

    def test_dot_in_dollars(self):
        text = "end $. $ next"
        result = fix_non_math_in_delimiters(text)
        assert "$" not in result
        assert "." in result

    def test_comma_where_in_dollars(self):
        text = "value $, where $ other"
        result = fix_non_math_in_delimiters(text)
        assert "$" not in result
        assert ", where" in result

    def test_empty_dollars_removed(self):
        text = "before$ $after"
        assert fix_non_math_in_delimiters(text) == "beforeafter"

    def test_real_math_unchanged(self):
        text = r"the value $x^{2}$ is"
        assert fix_non_math_in_delimiters(text) == text

    def test_latex_command_unchanged(self):
        text = r"unit $ \mu $s"
        assert fix_non_math_in_delimiters(text) == text

    def test_display_math_unchanged(self):
        text = "$$ [^1] [^2] $$"
        assert fix_non_math_in_delimiters(text) == text

    def test_code_block_unchanged(self):
        text = "```\n$ [^1] $\n```"
        assert fix_non_math_in_delimiters(text) == text

    def test_idempotent(self):
        text = "text $ [^2] $ more"
        result = fix_non_math_in_delimiters(text)
        assert fix_non_math_in_delimiters(result) == result


# ---------------------------------------------------------------------------
# fix_math_delimiter_spaces
# ---------------------------------------------------------------------------


class TestFixMathDelimiterSpaces:
    def test_spaces_trimmed(self):
        assert fix_math_delimiter_spaces("$ ^{1} $") == "$^{1}$"

    def test_mu_trimmed(self):
        assert fix_math_delimiter_spaces(r"$ \mu $s") == r"$\mu$s"

    def test_times_trimmed(self):
        assert fix_math_delimiter_spaces(r"$ \times $") == r"$\times$"

    def test_already_compact_unchanged(self):
        assert fix_math_delimiter_spaces(r"$\mu$") == r"$\mu$"

    def test_display_math_unchanged(self):
        text = "$$ x + y $$"
        assert fix_math_delimiter_spaces(text) == text

    def test_code_block_unchanged(self):
        text = "```\n$ x $\n```"
        assert fix_math_delimiter_spaces(text) == text

    def test_complex_formula(self):
        text = r"event $ \langle x, y, p, t \rangle $ is"
        assert fix_math_delimiter_spaces(text) == r"event $\langle x, y, p, t \rangle$ is"

    def test_multiple_formulas(self):
        text = r"coords $ (x, y) $, time $ t $"
        assert fix_math_delimiter_spaces(text) == r"coords $(x, y)$, time $t$"

    def test_idempotent(self):
        text = r"$ ^{1} $ and $ \mu $"
        result = fix_math_delimiter_spaces(text)
        assert fix_math_delimiter_spaces(result) == result


# ---------------------------------------------------------------------------
# fix_html_table_math_spaces
# ---------------------------------------------------------------------------


class TestFixHtmlTableMathSpaces:
    def test_td_math_spaces(self):
        text = "<table><tr><td>Input  $ W \\times H $</td></tr></table>"
        assert (
            fix_html_table_math_spaces(text)
            == "<table><tr><td>Input $W \\times H$</td></tr></table>"
        )

    def test_td_plus_minus(self):
        text = "<table><tr><td>28.88 ( $ \\pm $7.32)</td></tr></table>"
        assert (
            fix_html_table_math_spaces(text)
            == "<table><tr><td>28.88 ( $\\pm$7.32)</td></tr></table>"
        )

    def test_td_already_compact(self):
        text = "<table><tr><td>$\\mu$m</td></tr></table>"
        assert fix_html_table_math_spaces(text) == text

    def test_td_with_style(self):
        text = "<table><tr><td style='text-align: center;'>05 - 200  $ \\mu $s</td></tr></table>"
        expected = "<table><tr><td style='text-align: center;'>05 - 200 $\\mu$s</td></tr></table>"
        assert fix_html_table_math_spaces(text) == expected

    def test_multiple_tds(self):
        text = "<table><tr><td> $ a $ </td><td> $ b $ </td></tr></table>"
        result = fix_html_table_math_spaces(text)
        assert "$a$" in result
        assert "$b$" in result

    def test_non_td_unchanged(self):
        text = "<p>text  $ x $</p>"
        assert fix_html_table_math_spaces(text) == text

    def test_detached_td_fragment_unchanged_if_code_block(self):
        text = "```\n<td>Input  $ W \\times H $</td>\n```"
        assert fix_html_table_math_spaces(text) == text

    def test_detached_td_fragment_is_still_safe(self):
        text = "<td>Input  $ W \\times H $</td>"
        assert fix_html_table_math_spaces(text) == "<td>Input $W \\times H$</td>"

    def test_td_inside_fenced_code_unchanged(self):
        text = "```\n<table><tr><td>Input  $ W \\times H $</td></tr></table>\n```"
        assert fix_html_table_math_spaces(text) == text

    def test_td_preserves_mixed_protected_content(self):
        text = (
            "<td>before `code` and $ x $ and \\(y\\) and <code>$ z $</code> and "
            "```\n$ w $\n``` and after $ a $</td>"
        )
        result = fix_html_table_math_spaces(text)
        assert "`code`" in result
        assert r"\(y\)" in result
        assert "<code>$ z $</code>" in result
        assert "```\n$ w $\n```" in result
        assert "$x$" in result
        assert "$a$" in result

    def test_idempotent(self):
        text = "<table><tr><td>Input  $ W \\times H $</td></tr></table>"
        result = fix_html_table_math_spaces(text)
        assert fix_html_table_math_spaces(result) == result
