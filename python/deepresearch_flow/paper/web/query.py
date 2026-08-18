from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QueryTerm:
    field: str | None
    value: str
    negated: bool
    quoted: bool = False


@dataclass(frozen=True)
class Query:
    # OR over groups; each group is AND over terms
    groups: list[list[QueryTerm]]


_FIELD_RE = re.compile(r"^(title|author|tag|venue|year|month):(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class _QueryToken:
    value: str
    quoted: bool


def parse_query(text: str) -> Query:
    text = (text or "").strip()
    if not text:
        return Query(groups=[[]])

    tokens = _tokenize(text)
    groups: list[list[QueryTerm]] = [[]]

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if not token.quoted and token.value.upper() == "OR":
            if groups[-1]:
                groups.append([])
            idx += 1
            continue

        raw_value = token.value
        negated = raw_value.startswith("-")
        if negated:
            raw_value = raw_value[1:].strip()
            if not raw_value:
                idx += 1
                continue

        field = None
        value = raw_value
        match = _FIELD_RE.match(raw_value)
        if match:
            field = match.group(1).lower()
            value = match.group(2).strip()

        if value:
            groups[-1].append(
                QueryTerm(field=field, value=value, negated=negated, quoted=token.quoted)
            )
        idx += 1

    return Query(groups=[g for g in groups if g] or [[]])


def _tokenize(text: str) -> list[_QueryToken]:
    out: list[_QueryToken] = []
    buf: list[str] = []
    in_quote = False
    quoted = False

    def flush() -> None:
        nonlocal buf, quoted
        token = "".join(buf).strip()
        if token:
            out.append(_QueryToken(value=token, quoted=quoted))
        buf = []
        quoted = False

    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == '"':
            in_quote = not in_quote
            quoted = True
            idx += 1
            continue

        if not in_quote and ch.isspace():
            flush()
            idx += 1
            continue

        buf.append(ch)
        idx += 1

    flush()

    return out
