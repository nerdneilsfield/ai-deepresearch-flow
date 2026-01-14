"""Filter, query, and statistics utilities for paper web UI."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from deepresearch_flow.paper.db_ops import PaperIndex
from deepresearch_flow.paper.utils import stable_hash


BOOL_TRUE = {"1", "true", "yes", "with", "has"}
BOOL_FALSE = {"0", "false", "no", "without"}


def tokenize_filter_query(text: str) -> list[str]:
    """Tokenize a filter query string, respecting quoted phrases."""
    out: list[str] = []
    buf: list[str] = []
    in_quote = False

    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            continue
        if not in_quote and ch.isspace():
            token = "".join(buf).strip()
            if token:
                out.append(token)
            buf = []
            continue
        buf.append(ch)

    token = "".join(buf).strip()
    if token:
        out.append(token)
    return out


def normalize_presence_value(value: str) -> str | None:
    """Normalize a presence filter value to 'with' or 'without'."""
    token = value.strip().lower()
    if token in BOOL_TRUE:
        return "with"
    if token in BOOL_FALSE:
        return "without"
    return None


def parse_filter_query(text: str) -> dict[str, set[str]]:
    """Parse a filter query string into structured filters."""
    parsed = {
        "pdf": set(),
        "source": set(),
        "summary": set(),
        "translated": set(),
        "template": set(),
    }
    for token in tokenize_filter_query(text):
        if ":" not in token:
            continue
        key, raw_value = token.split(":", 1)
        key = key.strip().lower()
        raw_value = raw_value.strip()
        if not raw_value:
            continue
        if key in {"tmpl", "template"}:
            for part in raw_value.split(","):
                tag = part.strip()
                if tag:
                    parsed["template"].add(tag.lower())
            continue
        if key in {"pdf", "source", "summary", "translated"}:
            for part in raw_value.split(","):
                normalized = normalize_presence_value(part)
                if normalized:
                    parsed[key].add(normalized)
            continue
        if key in {"has", "no"}:
            targets = [part.strip().lower() for part in raw_value.split(",") if part.strip()]
            for target in targets:
                if target not in {"pdf", "source", "summary", "translated"}:
                    continue
                parsed[target].add("with" if key == "has" else "without")
    return parsed


def presence_filter(values: list[str]) -> set[str] | None:
    """Convert a list of presence filter values to a normalized set."""
    normalized = set()
    for value in values:
        token = normalize_presence_value(value)
        if token:
            normalized.add(token)
    if not normalized or normalized == {"with", "without"}:
        return None
    return normalized


def merge_filter_set(primary: set[str] | None, secondary: set[str] | None) -> set[str] | None:
    """Merge two filter sets with AND logic."""
    if not primary:
        return secondary
    if not secondary:
        return primary
    return primary & secondary


def matches_presence(allowed: set[str] | None, has_value: bool) -> bool:
    """Check if a value matches a presence filter."""
    if not allowed:
        return True
    if has_value and "with" in allowed:
        return True
    if not has_value and "without" in allowed:
        return True
    return False


def template_tag_map(index: PaperIndex) -> dict[str, str]:
    """Create a mapping from lowercase template tags to display tags."""
    return {tag.lower(): tag for tag in index.template_tags}


def compute_counts(index: PaperIndex, ids: set[int]) -> dict[str, Any]:
    """Compute statistics for a set of paper IDs."""
    template_order = list(index.template_tags)
    template_counts = {tag: 0 for tag in template_order}
    pdf_count = 0
    source_count = 0
    summary_count = 0
    translated_count = 0
    total_count = 0
    tag_map = template_tag_map(index)

    for idx in ids:
        paper = index.papers[idx]
        if paper.get("_is_pdf_only"):
            continue
        total_count += 1
        source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))
        has_source = source_hash in index.md_path_by_hash
        has_pdf = source_hash in index.pdf_path_by_hash
        has_summary = bool(paper.get("_has_summary"))
        has_translated = bool(index.translated_md_by_hash.get(source_hash))
        if has_source:
            source_count += 1
        if has_pdf:
            pdf_count += 1
        if has_summary:
            summary_count += 1
        if has_translated:
            translated_count += 1
        for tag_lc in paper.get("_template_tags_lc") or []:
            display = tag_map.get(tag_lc)
            if display:
                template_counts[display] = template_counts.get(display, 0) + 1

    return {
        "total": total_count,
        "pdf": pdf_count,
        "source": source_count,
        "summary": summary_count,
        "translated": translated_count,
        "templates": template_counts,
        "template_order": template_order,
    }


def parse_filters(request: Request) -> dict[str, list[str] | str | int]:
    """Parse filters from request query parameters."""
    qp = request.query_params
    page = int(qp.get("page", "1"))
    page_size = int(qp.get("page_size", "30"))
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    q = qp.get("q", "").strip()
    filter_query = qp.get("fq", "").strip()
    pdf_filters = [item for item in qp.getlist("pdf") if item]
    source_filters = [item for item in qp.getlist("source") if item]
    summary_filters = [item for item in qp.getlist("summary") if item]
    translated_filters = [item for item in qp.getlist("translated") if item]
    template_filters = [item for item in qp.getlist("template") if item]
    sort_by = qp.get("sort_by", "").strip()
    sort_dir = qp.get("sort_dir", "desc").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    return {
        "page": page,
        "page_size": page_size,
        "q": q,
        "filter_query": filter_query,
        "pdf": pdf_filters,
        "source": source_filters,
        "summary": summary_filters,
        "translated": translated_filters,
        "template": template_filters,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }


def safe_int(value: Any) -> int:
    """Safely convert a value to int, returning 0 on error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_sort_value(value: Any) -> str:
    """Normalize a value for sorting."""
    return str(value or "").strip().lower()


def sorted_ids(
    index: PaperIndex,
    ids: set[int],
    sort_by: str,
    sort_dir: str,
) -> list[int]:
    """Sort paper IDs according to sort criteria."""
    if not sort_by:
        return [idx for idx in index.ordered_ids if idx in ids]
    reverse = sort_dir == "desc"

    def sort_value(idx: int) -> tuple[Any, bool]:
        paper = index.papers[idx]
        if sort_by == "year":
            year = safe_int(paper.get("_year"))
            month = safe_int(paper.get("_month"))
            return (year, month), year == 0
        if sort_by == "title":
            value = normalize_sort_value(paper.get("paper_title"))
            return value, not bool(value)
        if sort_by == "venue":
            value = normalize_sort_value(paper.get("_venue"))
            return value, not bool(value)
        if sort_by == "author":
            authors = paper.get("_authors") or paper.get("authors") or []
            value = normalize_sort_value(authors[0] if authors else "")
            return value, not bool(value)
        return normalize_sort_value(paper.get("paper_title")), False

    def key_fn(idx: int) -> tuple[int, Any, int]:
        value, missing = sort_value(idx)
        missing_score = 0 if missing else 1
        if not reverse:
            missing_score = 1 if missing else 0
        return (missing_score, value, idx)

    return sorted(ids, key=key_fn, reverse=reverse)
