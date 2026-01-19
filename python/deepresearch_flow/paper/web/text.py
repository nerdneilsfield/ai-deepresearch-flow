"""Text normalization helpers for web rendering."""

from __future__ import annotations

import html
import re

_INLINE_FORMULA_RE = re.compile(r"<inline-formula[^>]*>.*?</inline-formula>", re.IGNORECASE | re.DOTALL)
_TEX_MATH_RE = re.compile(r"<tex-math[^>]*>(.*?)</tex-math>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_VENUE_BRACE_RE = re.compile(r"\{\{|\}\}")


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
    summary = _TAG_RE.sub("", summary)
    summary = html.unescape(summary)
    summary = _WS_RE.sub(" ", summary).strip()
    if len(summary) > max_len:
        return summary[: max_len - 1].rstrip() + "…"
    return summary
