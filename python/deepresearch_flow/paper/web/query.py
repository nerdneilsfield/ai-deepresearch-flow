from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QueryTerm:
    field: str | None
    value: str
    negated: bool


@dataclass(frozen=True)
class Query:
    # OR over groups; each group is AND over terms
    groups: list[list[QueryTerm]]


_FIELD_RE = re.compile(r"^(title|author|tag|venue|year|month):(.+)$", re.IGNORECASE)


def parse_query(text: str) -> Query:
    text = (text or "").strip()
    if not text:
        return Query(groups=[[]])

    tokens = _tokenize(text)
    groups: list[list[QueryTerm]] = [[]]

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.upper() == "OR":
            if groups[-1]:
                groups.append([])
            idx += 1
            continue

        negated = token.startswith("-")
        if negated:
            token = token[1:].strip()
            if not token:
                idx += 1
                continue

        field = None
        value = token
        match = _FIELD_RE.match(token)
        if match:
            field = match.group(1).lower()
            value = match.group(2).strip()

        if value:
            groups[-1].append(QueryTerm(field=field, value=value, negated=negated))
        idx += 1

    return Query(groups=[g for g in groups if g] or [[]])


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    in_quote = False

    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == '"':
            in_quote = not in_quote
            idx += 1
            continue

        if not in_quote and ch.isspace():
            token = "".join(buf).strip()
            if token:
                out.append(token)
            buf = []
            idx += 1
            continue

        buf.append(ch)
        idx += 1

    token = "".join(buf).strip()
    if token:
        out.append(token)

    return out

