from __future__ import annotations

import re
from typing import Iterable

from markdown_it import MarkdownIt


_HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3040 <= code <= 0x309F  # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0xAC00 <= code <= 0xD7AF  # Hangul syllables
    )


def insert_cjk_spaces(text: str) -> str:
    out: list[str] = []
    prev_cjk = False
    for ch in text:
        cur_cjk = _is_cjk_char(ch)
        if prev_cjk and cur_cjk:
            out.append(" ")
        out.append(ch)
        prev_cjk = cur_cjk
    return "".join(out)


def remove_cjk_spaces(text: str) -> str:
    if " " not in text:
        return text
    chars = list(text)
    out: list[str] = []
    for idx, ch in enumerate(chars):
        if ch == " " and 0 < idx < len(chars) - 1:
            if _is_cjk_char(chars[idx - 1]) and _is_cjk_char(chars[idx + 1]):
                continue
        out.append(ch)
    return "".join(out)


def merge_adjacent_markers(text: str, *, start_marker: str = "[[[", end_marker: str = "]]]") -> str:
    needle = f"{end_marker}{start_marker}"
    while needle in text:
        text = text.replace(needle, "")
    return text


def _md_renderer() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable("table")
    return md


def markdown_to_plain_text(markdown: str) -> str:
    if not markdown:
        return ""
    text = _HTML_TABLE_RE.sub(" ", markdown)
    md = _md_renderer()
    tokens = md.parse(text)

    out: list[str] = []
    in_table = 0
    for token in tokens:
        if token.type == "table_open":
            in_table += 1
            continue
        if token.type == "table_close":
            in_table = max(0, in_table - 1)
            continue
        if in_table:
            continue
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type in {"text", "code_inline"}:
                out.append(child.content)
            elif child.type == "softbreak":
                out.append("\n")
            elif child.type == "hardbreak":
                out.append("\n")
            elif child.type == "image":
                if child.content:
                    out.append(child.content)

    collapsed = _WS_RE.sub(" ", " ".join(out)).strip()
    collapsed = _TAG_RE.sub(" ", collapsed)
    return _WS_RE.sub(" ", collapsed).strip()


def normalize_query_punctuation(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[，。、《》、；：！？（）【】「」『』·…—]+", " ", text)


def split_mixed_cjk_latin(token: str) -> list[str]:
    if not token:
        return []
    parts: list[str] = []
    buf: list[str] = []
    buf_is_cjk: bool | None = None
    for ch in token:
        cur_is_cjk = _is_cjk_char(ch)
        if buf_is_cjk is None or cur_is_cjk == buf_is_cjk:
            buf.append(ch)
            buf_is_cjk = cur_is_cjk
            continue
        parts.append("".join(buf))
        buf = [ch]
        buf_is_cjk = cur_is_cjk
    if buf:
        parts.append("".join(buf))
    return parts


def rewrite_search_query(user_query: str) -> str:
    cleaned = normalize_query_punctuation(user_query)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return ""

    out: list[str] = []
    for raw in cleaned.split(" "):
        if not raw:
            continue
        upper = raw.upper()
        if upper in {"AND", "OR"}:
            out.append(upper)
            continue

        segments = split_mixed_cjk_latin(raw)
        for seg in segments:
            if not seg:
                continue
            if all(_is_cjk_char(ch) for ch in seg):
                phrase = insert_cjk_spaces(seg)
                out.append(f"\"{phrase}\"")
            else:
                safe = re.sub(r"[^0-9A-Za-z._+-]+", "", seg)
                if safe:
                    out.append(safe.lower())

    return " ".join(out)

