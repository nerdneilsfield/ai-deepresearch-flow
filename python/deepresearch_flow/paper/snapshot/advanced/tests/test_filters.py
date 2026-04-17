from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.errors import InvalidFilterError
from deepresearch_flow.paper.snapshot.advanced.filters import ParsedFilters, parse_filters


def test_empty_filters() -> None:
    out = parse_filters({})
    assert isinstance(out, ParsedFilters)
    assert out.year is None
    assert out.venues == ()
    assert out.authors == ()
    assert out.sql_where == ""
    assert out.lance_where == ""
    assert out.applied == {}


def test_year_single() -> None:
    out = parse_filters({"filters.year": ["2023"]})
    assert out.year is not None
    assert out.year.min == 2023 and out.year.max == 2023
    assert out.applied["year"] == {"min": 2023, "max": 2023}


def test_year_range() -> None:
    out = parse_filters({"filters.year": ["2020..2023"]})
    assert out.year is not None
    assert out.year.min == 2020 and out.year.max == 2023
    assert "year" in out.sql_where.lower() or "year" in out.lance_where.lower()


def test_year_invalid_raises() -> None:
    with pytest.raises(InvalidFilterError):
        parse_filters({"filters.year": ["abc"]})


def test_venue_validated() -> None:
    out = parse_filters({"filters.venue": ["NeurIPS"]})
    assert out.venues == ("NeurIPS",)


def test_venue_invalid_raises() -> None:
    with pytest.raises(InvalidFilterError):
        parse_filters({"filters.venue": ["drop; table"]})


def test_authors_normalized_and_tuple() -> None:
    out = parse_filters({"filters.authors": ["Hinton G.", "LeCun Y."]})
    assert out.authors == ("hinton g.", "lecun y.")


def test_applied_echoes_only_present_filters() -> None:
    out = parse_filters({"filters.tags": ["nlp"], "filters.lang": ["en"]})
    assert "tags" in out.applied
    assert "lang" in out.applied
    assert "authors" not in out.applied


def test_is_frozen() -> None:
    out = parse_filters({})
    try:
        out.venues = ("x",)  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ParsedFilters should be frozen")
