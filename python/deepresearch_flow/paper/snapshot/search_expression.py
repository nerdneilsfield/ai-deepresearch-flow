"""Search expression parser for ``/api/v1/search``.

Syntax: whitespace-separated tokens, ``"..."`` for phrases, ``field:value`` (value may be
quoted), ``-token`` for exclusion. Unknown field names stay ordinary free text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.text import rewrite_search_query, same_field_search_query

# LIKE is ASCII case-insensitive in SQLite; non-ASCII case is not folded.
# field -> (join table, facet table, facet id column); names are fixed, never user input.
FACET_FIELDS = {
    "author": ("paper_author", "author", "author_id"),
    "venue": ("paper_venue", "venue", "venue_id"),
    "keyword": ("paper_keyword", "keyword", "keyword_id"),
    "tag": ("paper_tag", "tag", "tag_id"),
    "institution": ("paper_institution", "institution", "institution_id"),
}
_FIELDS = {*FACET_FIELDS, "title", "year"}
_FIELD_RE = re.compile(r"([A-Za-z]+):")
_YEAR_RE = re.compile(r"[0-9]{4}")


class SearchExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    field: str  # one of _FIELDS, or "text" for an excluded plain term
    value: str
    negated: bool
    year_start: int | None = None
    year_end: int | None = None


@dataclass(frozen=True)
class SearchExpression:
    free_text: str
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class SearchPlan:
    match: str  # FTS MATCH expression, "" when the query has no FTS condition
    filters: tuple[str, ...]  # SQL clauses over alias ``p`` with ``?`` placeholders
    params: tuple[Any, ...]  # bound values for ``filters``, in order

    @property
    def is_empty(self) -> bool:
        return not self.match and not self.filters


def _scan(q: str) -> list[tuple[bool, str | None, str, bool]]:
    """Return (negated, field, value, quoted) per token."""
    out: list[tuple[bool, str | None, str, bool]] = []
    i, n = 0, len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue
        negated = q[i] == "-" and i + 1 < n and not q[i + 1].isspace()
        if negated:
            i += 1
        field = None
        m = _FIELD_RE.match(q, i)
        if m and m.group(1).lower() in _FIELDS:
            field = m.group(1).lower()
            i = m.end()
        if i < n and q[i] == '"':
            end = q.find('"', i + 1)
            end = n if end < 0 else end
            out.append((negated, field, q[i + 1 : end].strip(), True))
            i = end + 1
        else:
            j = i
            while j < n and not q[j].isspace() and q[j] != '"':
                j += 1
            out.append((negated, field, q[i:j], False))
            i = j
    return out


def _parse_year(value: str) -> tuple[int | None, int | None]:
    start_raw, sep, end_raw = value.partition("..")
    parts = (start_raw, end_raw) if sep else (value, value)
    if sep and not (start_raw or end_raw):
        raise SearchExpressionError("year range needs at least one bound")
    bounds: list[int | None] = []
    for part in parts:
        if part and not _YEAR_RE.fullmatch(part):
            raise SearchExpressionError(f"invalid year {part!r}: expected 4 digits")
        bounds.append(int(part) if part else None)
    start, end = bounds
    if start is not None and end is not None and start > end:
        raise SearchExpressionError(f"invalid year range {value!r}: start is after end")
    return start, end


def parse_search_expression(q: str) -> SearchExpression:
    free: list[str] = []
    conditions: list[Condition] = []
    for negated, field, value, quoted in _scan(q):
        if not value:
            continue
        if field == "year":
            start, end = _parse_year(value)
            conditions.append(Condition("year", value, negated, start, end))
        elif field:
            conditions.append(Condition(field, value, negated))
        elif negated:
            conditions.append(Condition("text", value, True))
        else:
            is_op = not quoted and value.upper() in {"AND", "OR"}
            if is_op and free and free[-1].upper() in {"AND", "OR"}:
                free.pop()  # a removed filter token left two operators side by side
            free.append(f'"{value}"' if quoted else value)
    while free and free[-1].upper() in {"AND", "OR"}:
        free.pop()
    while free and free[0].upper() in {"AND", "OR"}:
        free.pop(0)
    return SearchExpression(" ".join(free), tuple(conditions))


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _phrase(value: str) -> str:
    return rewrite_search_query(f'"{value}"')


def compile_search_expression(expr: SearchExpression) -> SearchPlan:
    positive: list[str] = []
    free_match = same_field_search_query(rewrite_search_query(expr.free_text))
    if free_match:
        positive.append(f"({free_match})")
    filters: list[str] = []
    params: list[Any] = []

    for cond in expr.conditions:
        not_sql = "NOT " if cond.negated else ""
        if cond.field == "year":
            clause = "p.year GLOB '[0-9][0-9][0-9][0-9]'"
            if cond.year_start is not None:
                clause += " AND CAST(p.year AS INT) >= ?"
                params.append(cond.year_start)
            if cond.year_end is not None:
                clause += " AND CAST(p.year AS INT) <= ?"
                params.append(cond.year_end)
            filters.append(f"{not_sql}COALESCE({clause}, 0)")
        elif cond.field in FACET_FIELDS:
            join_table, facet_table, facet_id = FACET_FIELDS[cond.field]
            filters.append(
                f"{not_sql}EXISTS (SELECT 1 FROM {join_table} j "
                f"JOIN {facet_table} f ON f.{facet_id} = j.{facet_id} "
                "WHERE j.paper_id = p.paper_id AND f.value LIKE ? ESCAPE '\\')"
            )
            params.append(_like_pattern(cond.value))
        else:
            phrase = _phrase(cond.value)
            if not phrase:
                continue
            match = f"title: ({phrase})" if cond.field == "title" else phrase
            if cond.negated:
                filters.append(
                    "p.paper_id NOT IN (SELECT paper_id FROM paper_fts WHERE paper_fts MATCH ?)"
                )
                params.append(match)
            else:
                positive.append(f"({match})")

    return SearchPlan(" AND ".join(positive), tuple(filters), tuple(params))
