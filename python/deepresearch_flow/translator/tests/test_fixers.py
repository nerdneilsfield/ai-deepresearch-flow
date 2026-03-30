"""Tests for PaddleOCR-compatible fix rules in fixers.py."""

from __future__ import annotations

import pytest

from deepresearch_flow.translator.fixers import (
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
        text = "<td>Input  $ W \\times H $</td>"
        assert fix_html_table_math_spaces(text) == "<td>Input $W \\times H$</td>"

    def test_td_plus_minus(self):
        text = "<td>28.88 ( $ \\pm $7.32)</td>"
        assert fix_html_table_math_spaces(text) == "<td>28.88 ( $\\pm$7.32)</td>"

    def test_td_already_compact(self):
        text = "<td>$\\mu$m</td>"
        assert fix_html_table_math_spaces(text) == text

    def test_td_with_style(self):
        text = "<td style='text-align: center;'>05 - 200  $ \\mu $s</td>"
        expected = "<td style='text-align: center;'>05 - 200 $\\mu$s</td>"
        assert fix_html_table_math_spaces(text) == expected

    def test_multiple_tds(self):
        text = "<td> $ a $ </td><td> $ b $ </td>"
        result = fix_html_table_math_spaces(text)
        assert "$a$" in result
        assert "$b$" in result

    def test_non_td_unchanged(self):
        text = "<p>text  $ x $</p>"
        assert fix_html_table_math_spaces(text) == text

    def test_idempotent(self):
        text = "<td>Input  $ W \\times H $</td>"
        result = fix_html_table_math_spaces(text)
        assert fix_html_table_math_spaces(result) == result
