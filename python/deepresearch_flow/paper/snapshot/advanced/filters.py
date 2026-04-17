"""Filter parsing for the advanced search endpoint."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from deepresearch_flow.paper.search import validate_venue_filter
from deepresearch_flow.paper.snapshot.advanced.errors import InvalidFilterError

_YEAR_RANGE_RE = re.compile(r"^(\d{4})\.\.(\d{4})$")
_YEAR_SINGLE_RE = re.compile(r"^\d{4}$")
_IDENT_RE = re.compile(r"^[\w\s\-\.\+&/(),:]+$", re.UNICODE)


@dataclass(frozen=True)
class YearRange:
    min: int
    max: int


@dataclass(frozen=True)
class ParsedFilters:
    year: YearRange | None
    venues: tuple[str, ...]
    authors: tuple[str, ...]
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    lang: str | None
    sql_where: str
    lance_where: str
    applied: dict[str, object] = field(default_factory=dict)


def _multi(params: Mapping[str, list[str]], key: str) -> list[str]:
    values = params.get(key) or []
    return [v.strip() for v in values if v and v.strip()]


def _parse_year(raw: str) -> YearRange:
    value = raw.strip()
    match = _YEAR_RANGE_RE.match(value)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise InvalidFilterError(f"year range reversed: {raw}")
        return YearRange(min=lower, max=upper)
    if _YEAR_SINGLE_RE.match(value):
        year = int(value)
        return YearRange(min=year, max=year)
    raise InvalidFilterError(f"unparseable year filter: {raw}")


def _validate_ident(value: str, kind: str) -> str:
    if not _IDENT_RE.fullmatch(value):
        raise InvalidFilterError(f"invalid {kind} filter value: {value}")
    return value


def _sql_quote(value: str) -> str:
    return value.replace("'", "''")


def parse_filters(params: Mapping[str, list[str]]) -> ParsedFilters:
    year_raw = _multi(params, "filters.year")
    year = _parse_year(year_raw[0]) if year_raw else None

    venues: list[str] = []
    for venue in _multi(params, "filters.venue"):
        try:
            venues.append(validate_venue_filter(venue))
        except ValueError as exc:
            raise InvalidFilterError(f"venue filter rejected: {exc}") from exc

    authors = tuple(_validate_ident(v.lower(), "authors") for v in _multi(params, "filters.authors"))
    keywords = tuple(_validate_ident(v.lower(), "keywords") for v in _multi(params, "filters.keywords"))
    tags = tuple(_validate_ident(v.lower(), "tags") for v in _multi(params, "filters.tags"))
    lang_values = _multi(params, "filters.lang")
    lang = _validate_ident(lang_values[0], "lang") if lang_values else None

    sql_parts: list[str] = []
    lance_parts: list[str] = []
    applied: dict[str, object] = {}

    if year is not None:
        sql_parts.append(f"CAST(p.year AS INTEGER) BETWEEN {year.min} AND {year.max}")
        lance_parts.append(f"year >= {year.min} AND year <= {year.max}")
        applied["year"] = {"min": year.min, "max": year.max}

    if venues:
        quoted = ", ".join(f"'{_sql_quote(venue)}'" for venue in venues)
        sql_parts.append(f"p.venue IN ({quoted})")
        lance_parts.append(" OR ".join(
            f"venue = '{_sql_quote(venue)}'" for venue in venues
        ))
        applied["venues"] = list(venues)

    if lang is not None:
        sql_parts.append(f"p.output_language = '{_sql_quote(lang)}'")
        lance_parts.append(f"lang = '{_sql_quote(lang)}'")
        applied["lang"] = lang

    if authors:
        applied["authors"] = list(authors)
    if keywords:
        applied["keywords"] = list(keywords)
    if tags:
        applied["tags"] = list(tags)

    return ParsedFilters(
        year=year,
        venues=tuple(venues),
        authors=authors,
        keywords=keywords,
        tags=tags,
        lang=lang,
        sql_where=" AND ".join(sql_parts),
        lance_where=" AND ".join(lance_parts),
        applied=applied,
    )
