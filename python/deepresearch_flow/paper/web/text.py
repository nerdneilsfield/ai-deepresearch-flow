"""Text normalization helpers for web rendering."""

from __future__ import annotations

import html
import re

_INLINE_FORMULA_RE = re.compile(
    r"<inline-formula[^>]*>.*?</inline-formula>", re.IGNORECASE | re.DOTALL
)
_TEX_MATH_RE = re.compile(r"<tex-math[^>]*>(.*?)</tex-math>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_VENUE_BRACE_RE = re.compile(r"\{\{|\}\}")
_ESCAPED_LINE_BREAK_RE = re.compile(r"\\r\\n|\\n|\\r")
_PARAGRAPH_OPEN_RE = re.compile(r"<\s*p(?:\s+[^>]*)?>", re.IGNORECASE)
_PARAGRAPH_CLOSE_RE = re.compile(r"<\s*/\s*p\s*>", re.IGNORECASE)
_LINE_BREAK_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def normalize_title(raw: str) -> str:
    """Normalize paper titles for display by stripping XML/HTML noise."""
    if not raw:
        return ""

    def replace_inline(match: re.Match[str]) -> str:
        block = match.group(0)
        tex = _TEX_MATH_RE.search(block)
        if tex:
            return tex.group(1)
        return ""

    text = _INLINE_FORMULA_RE.sub(replace_inline, raw)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def normalize_venue(raw: str) -> str:
    """Normalize venue strings by removing extra BibTeX braces."""
    if not raw:
        return ""
    text = _VENUE_BRACE_RE.sub("", raw)
    text = _WS_RE.sub(" ", text).strip()
    return text


def normalize_summary_text(raw: str) -> str:
    """Turn escaped line breaks and basic HTML paragraphs into readable text."""
    if not raw:
        return ""
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    text = _ESCAPED_LINE_BREAK_RE.sub("\n", text)
    text = _LINE_BREAK_RE.sub("\n", text)
    text = _PARAGRAPH_CLOSE_RE.sub("\n\n", text)
    text = _PARAGRAPH_OPEN_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_summary_snippet(paper: dict[str, object], max_len: int = 280) -> str:
    """Extract a short summary snippet, preferring the simple/simple_phi templates."""
    summary = ""
    templates = paper.get("templates")
    if isinstance(templates, dict):
        for template_tag in ("simple", "simple_phi"):
            template = templates.get(template_tag)
            if not isinstance(template, dict):
                continue
            for key in ("summary", "abstract"):
                value = template.get(key)
                if isinstance(value, str) and value.strip():
                    summary = value.strip()
                    break
            if summary:
                break
    if not summary:
        for key in ("summary", "abstract"):
            value = paper.get(key)
            if isinstance(value, str) and value.strip():
                summary = value.strip()
                break
    if not summary:
        return ""
    summary = _WS_RE.sub(" ", normalize_summary_text(summary)).strip()
    if len(summary) > max_len:
        return summary[: max_len - 1].rstrip() + "…"
    return summary
