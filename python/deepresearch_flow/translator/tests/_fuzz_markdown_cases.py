from __future__ import annotations

from dataclasses import dataclass
from random import Random
import re


@dataclass(frozen=True)
class FuzzCase:
    name: str
    text: str
    expected: str | None = None
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    level: str | None = None
    fix_level: str | None = None
    format_enabled: bool | None = None


_WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "theta",
    "kappa",
    "omega",
    "matrix",
    "vector",
    "sample",
    "term",
    "value",
    "nested",
    "plain",
    "result",
    "proof",
]

_DOMAINS = [
    "example.com",
    "mail.example.org",
    "research.university.edu",
    "data.test.net",
]

_MATH_SNIPPETS = [
    r"x^2+y^2=z^2",
    r"\frac{a}{b}",
    r"\langle x,y\rangle",
    r"\int_0^1 x\,dx",
    r"\alpha+\beta=\gamma",
    r"\mu_{i+1}",
]

_URL_PATHS = [
    "/path_(math)",
    "/docs/v1",
    "/q?a=1&b=2",
    "/wiki/Function_(mathematics)",
]

_PHONE_NUMBERS = [
    "555-123-4567",
    "+1 415 555 0199",
    "202-555-0148",
]


def _rng(seed: int) -> Random:
    return Random(seed)


def _pick(rng: Random, values: list[str]) -> str:
    return values[rng.randrange(len(values))]


def _word(rng: Random) -> str:
    base = _pick(rng, _WORDS)
    suffix = rng.randrange(100)
    return f"{base}{suffix}"


def _plain_word(rng: Random) -> str:
    return _pick(rng, _WORDS)


def _phrase(rng: Random, min_words: int = 2, max_words: int = 5) -> str:
    count = rng.randrange(min_words, max_words + 1)
    return " ".join(_word(rng) for _ in range(count))


def _plain_phrase(rng: Random, min_words: int = 2, max_words: int = 5) -> str:
    count = rng.randrange(min_words, max_words + 1)
    return " ".join(_plain_word(rng) for _ in range(count))


def _email(rng: Random) -> str:
    return f"{_word(rng)}@{_pick(rng, _DOMAINS)}"


def _url(rng: Random) -> str:
    return f"https://{_pick(rng, _DOMAINS)}{_pick(rng, _URL_PATHS)}"


def _phone(rng: Random) -> str:
    return _pick(rng, _PHONE_NUMBERS)


def _ref_range(rng: Random) -> str:
    start = rng.randrange(1, 20)
    end = start + rng.randrange(1, 4)
    return f"[{start}-{end}]"


def _ref_list(rng: Random) -> str:
    parts = [str(rng.randrange(1, 30)) for _ in range(rng.randrange(2, 5))]
    return "[" + ", ".join(parts) + "]"


def _nested_mailto(email: str, depth: int) -> str:
    return "<mailto:" * depth + email + ">" * depth


def _inline_code(content: str) -> str:
    return f"`{content}`"


def _fenced_code(content: str, lang: str = "python") -> str:
    return f"```{lang}\n{content}\n```"


def _math_inline(content: str, spaced: bool = False) -> str:
    if spaced:
        return f"$ {content} $"
    return f"${content}$"


def _math_paren(content: str) -> str:
    return rf"\({content}\)"


def _math_bracket(content: str) -> str:
    return rf"\[{content}\]"


def _table_cell(content: str, *, style: str | None = None) -> str:
    if style:
        return f"<td style='{style}'>{content}</td>"
    return f"<td>{content}</td>"


def _table(content: str) -> str:
    return f"<table><tr>{content}</tr></table>"


def _normalize_nonmath_dollar(span: str) -> str:
    inner = span[1:-1].strip()
    return inner


def _normalize_math_spaces(span: str) -> str:
    inner = span[1:-1]
    stripped = inner.strip()
    return f"${stripped}$"


def _normalize_table_content(content: str) -> str:
    joined = re.sub(r"\$(.*?)\$", lambda m: f"${m.group(1).strip()}$", content)
    joined = re.sub(r"\s{2,}", " ", joined)
    joined = re.sub(r"\s+\$", " $", joined)
    joined = re.sub(r"\$\s+", "$", joined)
    return joined


def nested_mailto_cases(seed: int = 101, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    for i in range(count):
        email = _email(rng)
        depth = 2 + (i % 4)
        nested = _nested_mailto(email, depth)
        kind = i % 4
        if kind == 0:
            text = f"prefix {nested} suffix"
            expected = f"prefix <mailto:{email}> suffix"
        elif kind == 1:
            text = f"{nested} and {nested}"
            expected = f"<mailto:{email}> and <mailto:{email}>"
        elif kind == 2:
            code = _fenced_code(nested)
            text = f"before {code} after"
            expected = text
        else:
            inline = _inline_code(nested)
            math = _math_paren(nested)
            text = f"{inline} {math} {nested}"
            expected = f"{inline} {math} <mailto:{email}>"
        cases.append(FuzzCase(name=f"nested_mailto_{i:03d}", text=text, expected=expected))
    return cases


def non_math_delimiter_cases(seed: int = 202, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    for i in range(count):
        kind = i % 4
        if kind == 0:
            span = f"$ {_plain_phrase(rng, 2, 4)} $"
            text = f"before {span} after"
            expected = f"before {_normalize_nonmath_dollar(span)} after"
        elif kind == 1:
            refs = [f"[^{rng.randrange(1, 50)}]" for _ in range(rng.randrange(2, 5))]
            span = "$ " + " ".join(refs) + " $"
            text = f"{span}"
            expected = _normalize_nonmath_dollar(span)
        elif kind == 2:
            span = _math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)
            text = f"{span} {_math_inline(_pick(rng, _MATH_SNIPPETS))}"
            expected = text
        else:
            code = _fenced_code(f"$ {_plain_phrase(rng, 2, 4)} $")
            inline = _inline_code(f"$ {_plain_phrase(rng, 2, 4)} $")
            math = _math_bracket(f"$ {_plain_phrase(rng, 2, 4)} $")
            text = f"{code}\n{inline} {math}"
            expected = text
        cases.append(FuzzCase(name=f"non_math_{i:03d}", text=text, expected=expected))
    return cases


def math_delimiter_space_cases(seed: int = 303, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    for i in range(count):
        kind = i % 4
        if kind == 0:
            span = _math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)
            text = f"before {span} after"
            expected = f"before {_normalize_math_spaces(span)} after"
        elif kind == 1:
            left = _math_inline(_pick(rng, _MATH_SNIPPETS))
            right = _math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)
            text = f"{left} and {right}"
            expected = f"{left} and {_normalize_math_spaces(right)}"
        elif kind == 2:
            code = _fenced_code(_math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True))
            inline = _inline_code(_math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True))
            math = _math_paren(_math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True))
            text = f"{code}\n{inline}\n{math}"
            expected = text
        else:
            left = _math_inline(_pick(rng, _MATH_SNIPPETS))
            right = _math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)
            text = f"{left} {right}"
            expected = f"{left} {_normalize_math_spaces(right)}"
        cases.append(FuzzCase(name=f"math_spaces_{i:03d}", text=text, expected=expected))
    return cases


def html_table_math_space_cases(seed: int = 404, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    for i in range(count):
        kind = i % 4
        if kind == 0:
            math = _math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)
            content = f"{_word(rng)}  {math}"
            text = _table(_table_cell(content))
            expected = _table(_table_cell(_normalize_table_content(content)))
        elif kind == 1:
            style = "text-align: center;"
            math = _math_inline(rng.choice(_MATH_SNIPPETS), spaced=True)
            content = f"{_phrase(rng, 2, 4)}  {math}s"
            text = _table(_table_cell(content, style=style))
            expected = _table(_table_cell(_normalize_table_content(content), style=style))
        elif kind == 2:
            text = f"<p>text  {_math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)}</p>"
            expected = text
        else:
            code = _fenced_code(
                _table(
                    _table_cell(
                        f"{_word(rng)}  {_math_inline(_pick(rng, _MATH_SNIPPETS), spaced=True)}"
                    )
                )
            )
            text = code
            expected = text
        cases.append(FuzzCase(name=f"html_table_{i:03d}", text=text, expected=expected))
    return cases


def markdown_fix_cases(seed: int = 505, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    levels = ["normal", "moderate", "aggressive"]
    for i in range(count):
        kind = i % 4
        if kind == 0:
            email = _email(rng)
            nested = _nested_mailto(email, 3)
            code = _fenced_code(f"nested={nested} [1] {_url(rng)} {_phone(rng)}")
            inline = _inline_code(f"{_ref_range(rng)} {_email(rng)}")
            math = _math_paren(f"[{rng.randrange(1, 5)}] {_url(rng)}")
            text = f"Intro {nested} middle {code} end {inline} {math}"
            must_contain = (f"<mailto:{email}>", code, inline, math)
            must_not_contain = ()
            cases.append(
                FuzzCase(
                    name=f"markdown_nested_{i:03d}",
                    text=text,
                    must_contain=must_contain,
                    must_not_contain=must_not_contain,
                    level="normal",
                )
            )
        elif kind == 1:
            ref = _ref_range(rng)
            multi = _ref_list(rng)
            text = f"Paragraph {ref} and {multi} with {_word(rng)}"
            must_contain = tuple(
                f"[^{n}]"
                for n in [ref[1 : ref.index("-")], *[p.strip() for p in multi[1:-1].split(",")]]
            )
            must_not_contain = (ref, multi)
            cases.append(
                FuzzCase(
                    name=f"markdown_refs_{i:03d}",
                    text=text,
                    must_contain=must_contain,
                    must_not_contain=must_not_contain,
                    level="normal",
                )
            )
        elif kind == 2:
            code = _fenced_code(f"{_url(rng)} {_email(rng)} {_phone(rng)} [2]")
            inline = _inline_code(f"{_url(rng)} {_email(rng)} {_phone(rng)} [3]")
            math = _math_inline(f"{_url(rng)} {_email(rng)} {_phone(rng)} [4]", spaced=True)
            text = f"Keep {code}\n{inline}\n{math}"
            cases.append(
                FuzzCase(
                    name=f"markdown_protected_{i:03d}",
                    text=text,
                    must_contain=(code, inline, math),
                    level="normal",
                )
            )
        else:
            email = _email(rng)
            nested = _nested_mailto(email, 2)
            ref = _ref_range(rng)
            code = _fenced_code(f"{nested} {_url(rng)}")
            inline = _inline_code(f"{ref} {_phone(rng)}")
            math = _math_bracket(f"{nested} {_url(rng)}")
            text = f"Before {nested} {ref} {_url(rng)}\n{code}\n{inline}\n{math}"
            cases.append(
                FuzzCase(
                    name=f"markdown_combined_{i:03d}",
                    text=text,
                    must_contain=(f"<mailto:{email}>", code, inline, math),
                    must_not_contain=(),
                    level=_pick(rng, levels),
                )
            )
    return cases


def markdown_text_fix_cases(seed: int = 606, count: int = 120) -> list[FuzzCase]:
    rng = _rng(seed)
    cases: list[FuzzCase] = []
    levels = ["normal", "moderate", "aggressive"]
    for i in range(count):
        kind = i % 4
        fix_level = _pick(rng, levels)
        format_enabled = bool(i % 2)
        if kind == 0:
            email = _email(rng)
            nested = _nested_mailto(email, 3)
            code = _fenced_code(f"nested={nested} [1] {_url(rng)} {_phone(rng)}")
            inline = _inline_code(f"{_ref_range(rng)} {_email(rng)}")
            math = _math_paren(f"[{rng.randrange(1, 5)}] {_url(rng)}")
            text = f"Intro {nested} middle {code} end {inline} {math}"
            must_contain = (f"<mailto:{email}>", code, inline, math)
            must_not_contain = ()
        elif kind == 1:
            ref = _ref_range(rng)
            multi = _ref_list(rng)
            text = f"Paragraph {ref} and {multi} with {_word(rng)}"
            must_contain = tuple(
                f"[^{n}]"
                for n in [ref[1 : ref.index("-")], *[p.strip() for p in multi[1:-1].split(",")]]
            )
            must_not_contain = (ref, multi)
        elif kind == 2:
            code = _fenced_code(f"{_url(rng)} {_email(rng)} {_phone(rng)} [2]")
            inline = _inline_code(f"{_url(rng)} {_email(rng)} {_phone(rng)} [3]")
            math = _math_inline(f"{_url(rng)} {_email(rng)} {_phone(rng)} [4]", spaced=True)
            text = f"Keep {code}\n{inline}\n{math}"
            must_contain = (code, inline, math)
            must_not_contain = ()
        else:
            email = _email(rng)
            nested = _nested_mailto(email, 2)
            ref = _ref_range(rng)
            code = _fenced_code(f"{nested} {_url(rng)}")
            inline = _inline_code(f"{ref} {_phone(rng)}")
            math = _math_bracket(f"{nested} {_url(rng)}")
            text = f"Before {nested} {ref} {_url(rng)}\n{code}\n{inline}\n{math}"
            must_contain = (f"<mailto:{email}>", code, inline, math)
            must_not_contain = ()
        cases.append(
            FuzzCase(
                name=f"markdown_text_{i:03d}",
                text=text,
                must_contain=must_contain,
                must_not_contain=must_not_contain,
                fix_level=fix_level,
                format_enabled=format_enabled,
            )
        )
    return cases
