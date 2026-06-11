from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.recognize.organize import fix_markdown_text
from deepresearch_flow.translator.fixers import (
    fix_html_table_math_spaces,
    fix_markdown,
    fix_math_delimiter_spaces,
    fix_nested_mailto,
    fix_non_math_in_delimiters,
)

from ._fuzz_markdown_cases import (
    FuzzCase,
    html_table_math_space_cases,
    markdown_fix_cases,
    markdown_text_fix_cases,
    math_delimiter_space_cases,
    nested_mailto_cases,
    non_math_delimiter_cases,
)


def _assert_exact_case(func, case: FuzzCase, *args):
    result = func(case.text, *args)
    assert result == case.expected
    assert func(case.text, *args) == result


def _assert_contract_case(func, case: FuzzCase, *args):
    result = func(case.text, *args)
    assert isinstance(result, str)
    assert func(case.text, *args) == result
    for fragment in case.must_contain:
        assert fragment in result
    for fragment in case.must_not_contain:
        assert fragment not in result


@pytest.mark.parametrize("case", nested_mailto_cases(), ids=lambda case: case.name)
def test_fix_nested_mailto_fuzz(case: FuzzCase):
    _assert_exact_case(fix_nested_mailto, case)


@pytest.mark.parametrize("case", non_math_delimiter_cases(), ids=lambda case: case.name)
def test_fix_non_math_in_delimiters_fuzz(case: FuzzCase):
    _assert_exact_case(fix_non_math_in_delimiters, case)


@pytest.mark.parametrize("case", math_delimiter_space_cases(), ids=lambda case: case.name)
def test_fix_math_delimiter_spaces_fuzz(case: FuzzCase):
    _assert_exact_case(fix_math_delimiter_spaces, case)


@pytest.mark.parametrize("case", html_table_math_space_cases(), ids=lambda case: case.name)
def test_fix_html_table_math_spaces_fuzz(case: FuzzCase):
    _assert_exact_case(fix_html_table_math_spaces, case)


@pytest.mark.parametrize("case", markdown_fix_cases(), ids=lambda case: case.name)
def test_fix_markdown_fuzz(case: FuzzCase):
    _assert_contract_case(fix_markdown, case, case.level or "normal")


@pytest.mark.parametrize("case", markdown_text_fix_cases(), ids=lambda case: case.name)
def test_fix_markdown_text_fuzz(case: FuzzCase):
    result = asyncio.run(
        fix_markdown_text(
            case.text,
            case.fix_level or "normal",
            case.format_enabled if case.format_enabled is not None else False,
        )
    )
    assert isinstance(result, str)
    assert (
        asyncio.run(
            fix_markdown_text(
                case.text,
                case.fix_level or "normal",
                case.format_enabled if case.format_enabled is not None else False,
            )
        )
        == result
    )
    for fragment in case.must_contain:
        assert fragment in result
    for fragment in case.must_not_contain:
        assert fragment not in result
