"""Query normalization: NFC, whitespace, language detection, FTS rewrite."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.text import rewrite_search_query

_WS_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


@dataclass(frozen=True)
class NormalizedQuery:
    raw: str
    normalized: str
    fts_expr: str
    lang: str


def normalize(raw: str) -> NormalizedQuery:
    normalized = unicodedata.normalize("NFC", raw or "")
    normalized = _WS_RE.sub(" ", normalized).strip()
    if not normalized:
        return NormalizedQuery(raw=raw, normalized="", fts_expr="", lang="en")
    lang = _detect_lang(normalized)
    return NormalizedQuery(
        raw=raw,
        normalized=normalized,
        fts_expr=rewrite_search_query(normalized) or "",
        lang=lang,
    )


def _detect_lang(text: str) -> str:
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return "en"
    cjk = len(_CJK_RE.findall(text))
    ratio = cjk / len(non_ws)
    if ratio > 0.5:
        return "zh"
    if ratio > 0.1:
        return "mixed"
    return "en"
