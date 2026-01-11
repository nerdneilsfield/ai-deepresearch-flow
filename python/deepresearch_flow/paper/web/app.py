from __future__ import annotations

import html
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import re
from urllib.parse import urlencode, quote

from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from deepresearch_flow.paper.render import load_default_template
from deepresearch_flow.paper.template_registry import (
    list_template_names_in_registry_order,
    load_render_template,
    load_schema_for_template,
)
from deepresearch_flow.paper.utils import stable_hash
from deepresearch_flow.paper.web.query import Query, QueryTerm, parse_query

try:
    from pybtex.database import parse_file
    PYBTEX_AVAILABLE = True
except Exception:
    PYBTEX_AVAILABLE = False


_CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
_CDN_MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
_CDN_KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css"
_CDN_KATEX_JS = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"
_CDN_KATEX_AUTO = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"
# Use legacy builds to ensure `pdfjsLib` is available as a global.
_CDN_PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/legacy/build/pdf.min.js"
_CDN_PDFJS_WORKER = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/legacy/build/pdf.worker.min.js"
_PDFJS_VIEWER_PATH = "/pdfjs/web/viewer.html"
_PDFJS_STATIC_DIR = Path(__file__).resolve().parent / "pdfjs"


@dataclass(frozen=True)
class PaperIndex:
    papers: list[dict[str, Any]]
    id_by_hash: dict[str, int]
    ordered_ids: list[int]
    by_tag: dict[str, set[int]]
    by_author: dict[str, set[int]]
    by_year: dict[str, set[int]]
    by_month: dict[str, set[int]]
    by_venue: dict[str, set[int]]
    stats: dict[str, Any]
    md_path_by_hash: dict[str, Path]
    pdf_path_by_hash: dict[str, Path]


def _split_csv(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _parse_year_month(date_str: str | None) -> tuple[str | None, str | None]:
    if not date_str:
        return None, None
    text = str(date_str).strip()
    year = None
    month = None

    year_match = re.search(r"(19|20)\d{2}", text)
    if year_match:
        year = year_match.group(0)

    numeric_match = re.search(r"(19|20)\d{2}[-/](\d{1,2})", text)
    if numeric_match:
        m = int(numeric_match.group(2))
        if 1 <= m <= 12:
            month = f"{m:02d}"
        return year, month

    month_word = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|november|december)",
        text.lower(),
    )
    if month_word:
        lookup = {
            "january": "01",
            "february": "02",
            "march": "03",
            "april": "04",
            "may": "05",
            "june": "06",
            "july": "07",
            "august": "08",
            "september": "09",
            "october": "10",
            "november": "11",
            "december": "12",
            "jan": "01",
            "feb": "02",
            "mar": "03",
            "apr": "04",
            "jun": "06",
            "jul": "07",
            "aug": "08",
            "sep": "09",
            "sept": "09",
            "oct": "10",
            "nov": "11",
            "dec": "12",
        }
        month = lookup.get(month_word.group(0))
    return year, month


def _normalize_month_token(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 1 <= value <= 12:
            return f"{value:02d}"
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return _normalize_month_token(int(raw))
    lookup = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "sept": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    return lookup.get(raw)


def _extract_authors(paper: dict[str, Any]) -> list[str]:
    value = paper.get("paper_authors")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def _extract_tags(paper: dict[str, Any]) -> list[str]:
    tags = paper.get("ai_generated_tags") or []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    return []


def _extract_venue(paper: dict[str, Any]) -> str:
    if isinstance(paper.get("bibtex"), dict):
        bib = paper.get("bibtex") or {}
        fields = bib.get("fields") or {}
        bib_type = (bib.get("type") or "").lower()
        if bib_type == "article" and fields.get("journal"):
            return str(fields.get("journal"))
        if bib_type in {"inproceedings", "conference", "proceedings"} and fields.get("booktitle"):
            return str(fields.get("booktitle"))
    return str(paper.get("publication_venue") or "")


def build_index(
    papers: list[dict[str, Any]],
    *,
    md_roots: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
) -> PaperIndex:
    id_by_hash: dict[str, int] = {}
    by_tag: dict[str, set[int]] = {}
    by_author: dict[str, set[int]] = {}
    by_year: dict[str, set[int]] = {}
    by_month: dict[str, set[int]] = {}
    by_venue: dict[str, set[int]] = {}

    md_path_by_hash: dict[str, Path] = {}
    pdf_path_by_hash: dict[str, Path] = {}

    md_file_index = _build_file_index(md_roots or [], suffixes={".md"})
    pdf_file_index = _build_file_index(pdf_roots or [], suffixes={".pdf"})

    year_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    author_counts: dict[str, int] = {}
    venue_counts: dict[str, int] = {}

    def add_index(index: dict[str, set[int]], key: str, idx: int) -> None:
        index.setdefault(key, set()).add(idx)

    for idx, paper in enumerate(papers):
        source_hash = paper.get("source_hash")
        if not source_hash and paper.get("source_path"):
            source_hash = stable_hash(str(paper.get("source_path")))
        if source_hash:
            id_by_hash[str(source_hash)] = idx

        title = str(paper.get("paper_title") or "")
        paper["_title_lc"] = title.lower()

        bib_fields: dict[str, Any] = {}
        if isinstance(paper.get("bibtex"), dict):
            bib_fields = paper.get("bibtex", {}).get("fields", {}) or {}

        year = None
        if bib_fields.get("year") and str(bib_fields.get("year")).isdigit():
            year = str(bib_fields.get("year"))
        month = _normalize_month_token(bib_fields.get("month"))
        if not year or not month:
            parsed_year, parsed_month = _parse_year_month(str(paper.get("publication_date") or ""))
            year = year or parsed_year
            month = month or parsed_month

        year_label = year or "Unknown"
        month_label = month or "Unknown"
        paper["_year"] = year_label
        paper["_month"] = month_label
        add_index(by_year, _normalize_key(year_label), idx)
        add_index(by_month, _normalize_key(month_label), idx)
        year_counts[year_label] = year_counts.get(year_label, 0) + 1
        month_counts[month_label] = month_counts.get(month_label, 0) + 1

        venue = _extract_venue(paper).strip()
        paper["_venue"] = venue
        if venue:
            add_index(by_venue, _normalize_key(venue), idx)
            venue_counts[venue] = venue_counts.get(venue, 0) + 1
        else:
            add_index(by_venue, "unknown", idx)
            venue_counts["Unknown"] = venue_counts.get("Unknown", 0) + 1

        authors = _extract_authors(paper)
        paper["_authors"] = authors
        for author in authors:
            key = _normalize_key(author)
            add_index(by_author, key, idx)
            author_counts[author] = author_counts.get(author, 0) + 1

        tags = _extract_tags(paper)
        paper["_tags"] = tags
        for tag in tags:
            key = _normalize_key(tag)
            add_index(by_tag, key, idx)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        search_parts = [title, venue, " ".join(authors), " ".join(tags)]
        paper["_search_lc"] = " ".join(part for part in search_parts if part).lower()

        source_hash_str = str(source_hash) if source_hash else str(idx)
        md_path = _resolve_source_md(paper, md_file_index)
        if md_path is not None:
            md_path_by_hash[source_hash_str] = md_path
        pdf_path = _resolve_pdf(paper, pdf_file_index)
        if pdf_path is not None:
            pdf_path_by_hash[source_hash_str] = pdf_path

    def year_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, str]:
        idx, paper = item
        year_label = str(paper.get("_year") or "Unknown")
        title_label = str(paper.get("paper_title") or "")
        if year_label.isdigit():
            return (0, -int(year_label), title_label.lower())
        return (1, 0, title_label.lower())

    ordered_ids = [idx for idx, _ in sorted(enumerate(papers), key=year_sort_key)]

    stats = {
        "total": len(papers),
        "years": _sorted_counts(year_counts, numeric_desc=True),
        "months": _sorted_month_counts(month_counts),
        "tags": _sorted_counts(tag_counts),
        "authors": _sorted_counts(author_counts),
        "venues": _sorted_counts(venue_counts),
    }

    return PaperIndex(
        papers=papers,
        id_by_hash=id_by_hash,
        ordered_ids=ordered_ids,
        by_tag=by_tag,
        by_author=by_author,
        by_year=by_year,
        by_month=by_month,
        by_venue=by_venue,
        stats=stats,
        md_path_by_hash=md_path_by_hash,
        pdf_path_by_hash=pdf_path_by_hash,
    )


def _sorted_counts(counts: dict[str, int], *, numeric_desc: bool = False) -> list[dict[str, Any]]:
    items = list(counts.items())
    if numeric_desc:
        def key(item: tuple[str, int]) -> tuple[int, int]:
            label, count = item
            if label.isdigit():
                return (0, -int(label))
            return (1, 0)
        items.sort(key=key)
    else:
        items.sort(key=lambda item: item[1], reverse=True)
    return [{"label": k, "count": v} for k, v in items]


def _sorted_month_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    def month_sort(label: str) -> int:
        if label == "Unknown":
            return 99
        if label.isdigit():
            return int(label)
        return 98

    items = sorted(counts.items(), key=lambda item: month_sort(item[0]))
    return [{"label": k, "count": v} for k, v in items]


_TEMPLATE_INFER_IGNORE_KEYS = {
    "source_path",
    "source_hash",
    "provider",
    "model",
    "extracted_at",
    "truncation",
    "output_language",
    "prompt_template",
}


def _load_paper_inputs(paths: list[Path]) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raise ValueError(
                f"Input JSON must be an object with template_tag and papers (got array): {path}"
            )
        if not isinstance(payload, dict):
            raise ValueError(f"Input JSON must be an object: {path}")
        papers = payload.get("papers")
        if not isinstance(papers, list):
            raise ValueError(f"Input JSON missing papers list: {path}")
        template_tag = payload.get("template_tag")
        if not template_tag:
            template_tag = _infer_template_tag(papers, path)
        inputs.append({"template_tag": str(template_tag), "papers": papers})
    return inputs


def _infer_template_tag(papers: list[dict[str, Any]], path: Path) -> str:
    prompt_tags = {
        str(paper.get("prompt_template"))
        for paper in papers
        if isinstance(paper, dict) and paper.get("prompt_template")
    }
    if len(prompt_tags) == 1:
        return prompt_tags.pop()

    sample = next((paper for paper in papers if isinstance(paper, dict)), None)
    if sample is None:
        raise ValueError(f"Input JSON has no paper objects to infer template_tag: {path}")

    paper_keys = {key for key in sample.keys() if key not in _TEMPLATE_INFER_IGNORE_KEYS}
    if not paper_keys:
        raise ValueError(f"Input JSON papers have no keys to infer template_tag: {path}")

    best_tag = None
    best_score = -1
    for name in list_template_names_in_registry_order():
        schema = load_schema_for_template(name)
        schema_keys = set((schema.get("properties") or {}).keys())
        score = len(paper_keys & schema_keys)
        if score > best_score:
            best_score = score
            best_tag = name
        elif score == best_score:
            if best_tag != "simple" and name == "simple":
                best_tag = name

    if not best_tag:
        raise ValueError(f"Unable to infer template_tag from input JSON: {path}")
    return best_tag


def _build_cache_meta(db_paths: list[Path], bibtex_path: Path | None) -> dict[str, Any]:
    def file_meta(path: Path) -> dict[str, Any]:
        try:
            stats = path.stat()
        except OSError as exc:
            raise ValueError(f"Failed to read input metadata for cache: {path}") from exc
        return {"path": str(path), "mtime": stats.st_mtime, "size": stats.st_size}

    meta = {
        "version": 1,
        "inputs": [file_meta(path) for path in db_paths],
        "bibtex": file_meta(bibtex_path) if bibtex_path else None,
    }
    return meta


def _load_cached_papers(cache_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]] | None:
    meta_path = cache_dir / "db_serve_cache.meta.json"
    data_path = cache_dir / "db_serve_cache.papers.json"
    if not meta_path.exists() or not data_path.exists():
        return None
    try:
        cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if cached_meta != meta:
            return None
        cached_papers = json.loads(data_path.read_text(encoding="utf-8"))
        if not isinstance(cached_papers, list):
            return None
        return cached_papers
    except Exception:
        return None


def _write_cached_papers(cache_dir: Path, meta: dict[str, Any], papers: list[dict[str, Any]]) -> None:
    meta_path = cache_dir / "db_serve_cache.meta.json"
    data_path = cache_dir / "db_serve_cache.papers.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    data_path.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_or_merge_papers(
    db_paths: list[Path],
    bibtex_path: Path | None,
    cache_dir: Path | None,
    use_cache: bool,
) -> list[dict[str, Any]]:
    cache_meta = None
    if cache_dir and use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_meta = _build_cache_meta(db_paths, bibtex_path)
        cached = _load_cached_papers(cache_dir, cache_meta)
        if cached is not None:
            return cached

    inputs = _load_paper_inputs(db_paths)
    if bibtex_path is not None:
        for bundle in inputs:
            enrich_with_bibtex(bundle["papers"], bibtex_path)
    papers = _merge_paper_inputs(inputs)

    if cache_dir and use_cache and cache_meta is not None:
        _write_cached_papers(cache_dir, cache_meta, papers)
    return papers


def _md_renderer() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "linkify": True})


def _normalize_merge_title(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).replace("{", "").replace("}", "").strip().lower()


def _extract_bibtex_title(paper: dict[str, Any]) -> str | None:
    if not isinstance(paper.get("bibtex"), dict):
        return None
    fields = paper.get("bibtex", {}).get("fields", {}) or {}
    return _normalize_merge_title(fields.get("title"))


def _extract_paper_title(paper: dict[str, Any]) -> str | None:
    return _normalize_merge_title(paper.get("paper_title"))


def _available_templates(paper: dict[str, Any]) -> list[str]:
    templates = paper.get("templates")
    if not isinstance(templates, dict):
        return []
    order = paper.get("template_order") or list(templates.keys())
    seen: set[str] = set()
    available: list[str] = []
    for tag in order:
        if tag in templates and tag not in seen:
            available.append(tag)
            seen.add(tag)
    for tag in templates:
        if tag not in seen:
            available.append(tag)
            seen.add(tag)
    return available


def _select_template_tag(
    paper: dict[str, Any], requested: str | None
) -> tuple[str | None, list[str]]:
    available = _available_templates(paper)
    if not available:
        return None, []
    default_tag = paper.get("default_template")
    if not default_tag:
        default_tag = "simple" if "simple" in available else available[0]
    selected = requested if requested in available else default_tag
    return selected, available


def _titles_match(group: dict[str, Any], paper: dict[str, Any], *, threshold: float) -> bool:
    bib_title = _extract_bibtex_title(paper)
    group_bib = group.get("_merge_bibtex_titles") or set()
    if bib_title and group_bib:
        return any(_title_similarity(bib_title, existing) >= threshold for existing in group_bib)

    paper_title = _extract_paper_title(paper)
    group_titles = group.get("_merge_paper_titles") or set()
    if paper_title and group_titles:
        return any(_title_similarity(paper_title, existing) >= threshold for existing in group_titles)
    return False


def _add_merge_titles(group: dict[str, Any], paper: dict[str, Any]) -> None:
    bib_title = _extract_bibtex_title(paper)
    if bib_title:
        group.setdefault("_merge_bibtex_titles", set()).add(bib_title)
    paper_title = _extract_paper_title(paper)
    if paper_title:
        group.setdefault("_merge_paper_titles", set()).add(paper_title)


def _merge_paper_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    threshold = 0.95
    prefix_len = 5
    bibtex_exact: dict[str, set[int]] = {}
    bibtex_prefix: dict[str, set[int]] = {}
    paper_exact: dict[str, set[int]] = {}
    paper_prefix: dict[str, set[int]] = {}

    def prefix_key(value: str) -> str:
        return value[:prefix_len] if len(value) >= prefix_len else value

    def add_index(
        value: str,
        exact_index: dict[str, set[int]],
        prefix_index: dict[str, set[int]],
        idx: int,
    ) -> None:
        exact_index.setdefault(value, set()).add(idx)
        prefix_index.setdefault(prefix_key(value), set()).add(idx)

    def candidate_ids(bib_title: str | None, paper_title: str | None) -> list[int]:
        ids: set[int] = set()
        if bib_title:
            ids |= bibtex_exact.get(bib_title, set())
            ids |= bibtex_prefix.get(prefix_key(bib_title), set())
        if paper_title:
            ids |= paper_exact.get(paper_title, set())
            ids |= paper_prefix.get(prefix_key(paper_title), set())
        return sorted(ids)

    for bundle in inputs:
        template_tag = bundle.get("template_tag")
        papers = bundle.get("papers") or []
        for paper in papers:
            if not isinstance(paper, dict):
                raise ValueError("Input papers must be objects")
            bib_title = _extract_bibtex_title(paper)
            paper_title = _extract_paper_title(paper)
            match = None
            match_idx = None
            for idx in candidate_ids(bib_title, paper_title):
                candidate = merged[idx]
                if _titles_match(candidate, paper, threshold=threshold):
                    match = candidate
                    match_idx = idx
                    break
            if match is None:
                group = {
                    "templates": {template_tag: paper},
                    "template_order": [template_tag],
                }
                _add_merge_titles(group, paper)
                merged.append(group)
                group_idx = len(merged) - 1
                if bib_title:
                    add_index(bib_title, bibtex_exact, bibtex_prefix, group_idx)
                if paper_title:
                    add_index(paper_title, paper_exact, paper_prefix, group_idx)
            else:
                templates = match.setdefault("templates", {})
                templates[template_tag] = paper
                order = match.setdefault("template_order", [])
                if template_tag not in order:
                    order.append(template_tag)
                _add_merge_titles(match, paper)
                if match_idx is not None:
                    if bib_title:
                        add_index(bib_title, bibtex_exact, bibtex_prefix, match_idx)
                    if paper_title:
                        add_index(paper_title, paper_exact, paper_prefix, match_idx)

    for group in merged:
        templates = group.get("templates") or {}
        order = group.get("template_order") or list(templates.keys())
        default_tag = "simple" if "simple" in order else (order[0] if order else None)
        group["default_template"] = default_tag
        if default_tag and default_tag in templates:
            base = templates[default_tag]
            for key, value in base.items():
                group[key] = value
        group.pop("_merge_bibtex_titles", None)
        group.pop("_merge_paper_titles", None)
    return merged


def _render_markdown_with_math_placeholders(md: MarkdownIt, text: str) -> str:
    rendered, table_placeholders = _extract_html_table_placeholders(text)
    rendered, img_placeholders = _extract_html_img_placeholders(rendered)
    rendered, placeholders = _extract_math_placeholders(rendered)
    html_out = md.render(rendered)
    for key, value in placeholders.items():
        html_out = html_out.replace(key, html.escape(value))
    for key, value in img_placeholders.items():
        html_out = re.sub(rf"<p>\s*{re.escape(key)}\s*</p>", lambda _: value, html_out)
        html_out = html_out.replace(key, value)
    for key, value in table_placeholders.items():
        safe_html = _sanitize_table_html(value)
        html_out = re.sub(rf"<p>\s*{re.escape(key)}\s*</p>", lambda _: safe_html, html_out)
    return html_out


def _extract_math_placeholders(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@MATH_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        ch = text[idx]
        if ch == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        # Block math: $$...$$ (can span lines)
        if text.startswith("$$", idx) and (idx == 0 or text[idx - 1] != "\\"):
            search_from = idx + 2
            end = text.find("$$", search_from)
            while end != -1 and text[end - 1] == "\\":
                search_from = end + 2
                end = text.find("$$", search_from)
            if end != -1:
                out.append(next_placeholder(text[idx : end + 2]))
                idx = end + 2
                continue

        # Inline math: $...$ (single-line)
        if ch == "$" and not text.startswith("$$", idx) and (idx == 0 or text[idx - 1] != "\\"):
            search_from = idx + 1
            end = text.find("$", search_from)
            while end != -1 and text[end - 1] == "\\":
                search_from = end + 1
                end = text.find("$", search_from)
            if end != -1:
                out.append(next_placeholder(text[idx : end + 1]))
                idx = end + 1
                continue

        out.append(ch)
        idx += 1

    return "".join(out), placeholders


class _TableSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t not in {
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "caption",
            "colgroup",
            "col",
            "br",
        }:
            return

        allowed: dict[str, str] = {}
        for name, value in attrs:
            if value is None:
                continue
            n = name.lower()
            v = value.strip()
            if t in {"td", "th"} and n in {"colspan", "rowspan"} and v.isdigit():
                allowed[n] = v
            elif t in {"td", "th"} and n == "align" and v.lower() in {"left", "right", "center"}:
                allowed[n] = v.lower()

        attr_text = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in allowed.items())
        self._out.append(f"<{t}{attr_text}>")
        if t not in {"br", "col"}:
            self._stack.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t not in self._stack:
            return
        while self._stack:
            popped = self._stack.pop()
            self._out.append(f"</{popped}>")
            if popped == t:
                break

    def handle_data(self, data: str) -> None:
        self._out.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._out.append(f"&#{name};")

    def close(self) -> None:
        super().close()
        while self._stack:
            self._out.append(f"</{self._stack.pop()}>")

    def get_html(self) -> str:
        return "".join(self._out)


def _sanitize_table_html(raw: str) -> str:
    parser = _TableSanitizer()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return f"<pre><code>{html.escape(raw)}</code></pre>"
    return parser.get_html()


def _sanitize_img_html(raw: str) -> str | None:
    attrs = {}
    for match in re.finditer(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", raw):
        name = match.group(1).lower()
        value = match.group(2).strip()
        if value and value[0] in {"\"", "'"} and value[-1] == value[0]:
            value = value[1:-1]
        attrs[name] = value

    src = attrs.get("src", "")
    src_lower = src.lower()
    if not src_lower.startswith("data:image/") or ";base64," not in src_lower:
        return None

    alt = attrs.get("alt", "")
    alt_attr = f' alt="{html.escape(alt, quote=True)}"' if alt else ""
    return f'<img src="{html.escape(src, quote=True)}"{alt_attr} />'


def _extract_html_img_placeholders(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@HTML_IMG_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    lower = text.lower()
    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        if text[idx] == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        if lower.startswith("<img", idx):
            end = text.find(">", idx)
            if end != -1:
                raw = text[idx : end + 1]
                safe_html = _sanitize_img_html(raw)
                if safe_html:
                    out.append(next_placeholder(safe_html))
                    idx = end + 1
                    continue

        out.append(text[idx])
        idx += 1

    return "".join(out), placeholders


def _extract_html_table_placeholders(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    out: list[str] = []
    idx = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    inline_delim_len = 0

    def next_placeholder(value: str) -> str:
        key = f"@@HTML_TABLE_{len(placeholders)}@@"
        placeholders[key] = value
        return key

    lower = text.lower()
    while idx < len(text):
        at_line_start = idx == 0 or text[idx - 1] == "\n"

        if inline_delim_len == 0 and at_line_start:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            line = text[idx:line_end]
            stripped = line.lstrip(" ")
            leading_spaces = len(line) - len(stripped)
            if leading_spaces <= 3 and stripped:
                first = stripped[0]
                if first in {"`", "~"}:
                    run_len = 0
                    while run_len < len(stripped) and stripped[run_len] == first:
                        run_len += 1
                    if run_len >= 3:
                        if not in_fence:
                            in_fence = True
                            fence_char = first
                            fence_len = run_len
                        elif first == fence_char and run_len >= fence_len:
                            in_fence = False
                            fence_char = ""
                            fence_len = 0
                        out.append(line)
                        idx = line_end
                        continue

        if in_fence:
            out.append(text[idx])
            idx += 1
            continue

        if inline_delim_len > 0:
            delim = "`" * inline_delim_len
            if text.startswith(delim, idx):
                out.append(delim)
                idx += inline_delim_len
                inline_delim_len = 0
                continue
            out.append(text[idx])
            idx += 1
            continue

        if text[idx] == "`":
            run_len = 0
            while idx + run_len < len(text) and text[idx + run_len] == "`":
                run_len += 1
            inline_delim_len = run_len
            out.append("`" * run_len)
            idx += run_len
            continue

        if lower.startswith("<table", idx):
            end = lower.find("</table>", idx)
            if end != -1:
                end += len("</table>")
                raw = text[idx:end]
                key = next_placeholder(raw)
                if out and not out[-1].endswith("\n"):
                    out.append("\n\n")
                out.append(key)
                out.append("\n\n")
                idx = end
                continue

        out.append(text[idx])
        idx += 1

    return "".join(out), placeholders


def _render_paper_markdown(
    paper: dict[str, Any],
    fallback_language: str,
    *,
    template_tag: str | None = None,
) -> tuple[str, str, str | None]:
    selected_tag, _ = _select_template_tag(paper, template_tag)
    selected_paper = paper
    if selected_tag:
        selected_paper = (paper.get("templates") or {}).get(selected_tag, paper)

    template_name = selected_tag or selected_paper.get("prompt_template")
    warning = None
    if template_name:
        try:
            template = load_render_template(str(template_name))
        except Exception:
            template = load_default_template()
            warning = "Rendered using default template (missing template)."
            template_name = "default_paper"
    else:
        template = load_default_template()
        warning = "Rendered using default template (no template specified)."
        template_name = "default_paper"

    context = dict(selected_paper)
    if not context.get("output_language"):
        context["output_language"] = fallback_language
    return template.render(**context), str(template_name), warning


def _build_file_index(roots: list[Path], *, suffixes: set[str]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if path.suffix.lower() not in suffixes:
                continue
            index.setdefault(path.name.lower(), []).append(path.resolve())
    return index


def _resolve_source_md(paper: dict[str, Any], md_index: dict[str, list[Path]]) -> Path | None:
    source_path = paper.get("source_path")
    if not source_path:
        return None
    name = Path(str(source_path)).name.lower()
    candidates = md_index.get(name, [])
    return candidates[0] if candidates else None


def _guess_pdf_names(paper: dict[str, Any]) -> list[str]:
    source_path = paper.get("source_path")
    if not source_path:
        return []
    name = Path(str(source_path)).name
    match = re.match(r"(?i)(.+\\.pdf)(?:-[0-9a-f\\-]{8,})?\\.md$", name)
    if match:
        return [Path(match.group(1)).name]
    if ".pdf-" in name.lower():
        base = name[: name.lower().rfind(".pdf-") + 4]
        return [Path(base).name]
    if name.lower().endswith(".pdf.md"):
        return [name[:-3]]
    return []


def _resolve_pdf(paper: dict[str, Any], pdf_index: dict[str, list[Path]]) -> Path | None:
    for filename in _guess_pdf_names(paper):
        candidates = pdf_index.get(filename.lower(), [])
        if candidates:
            return candidates[0]
    return None


def _ensure_under_roots(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


def _apply_query(index: PaperIndex, query: Query) -> set[int]:
    all_ids = set(index.ordered_ids)

    def ids_for_term(term: QueryTerm, base: set[int]) -> set[int]:
        value_lc = term.value.lower()
        if term.field is None:
            return {idx for idx in base if value_lc in str(index.papers[idx].get("_search_lc") or "")}
        if term.field == "title":
            return {idx for idx in base if value_lc in str(index.papers[idx].get("_title_lc") or "")}
        if term.field == "venue":
            return {idx for idx in base if value_lc in str(index.papers[idx].get("_venue") or "").lower()}
        if term.field == "tag":
            exact = index.by_tag.get(value_lc)
            if exact is not None:
                return exact & base
            return {idx for idx in base if any(value_lc in t.lower() for t in (index.papers[idx].get("_tags") or []))}
        if term.field == "author":
            exact = index.by_author.get(value_lc)
            if exact is not None:
                return exact & base
            return {idx for idx in base if any(value_lc in a.lower() for a in (index.papers[idx].get("_authors") or []))}
        if term.field == "month":
            exact = index.by_month.get(value_lc)
            if exact is not None:
                return exact & base
            return {idx for idx in base if value_lc == str(index.papers[idx].get("_month") or "").lower()}
        if term.field == "year":
            if ".." in term.value:
                start_str, end_str = term.value.split("..", 1)
                if start_str.strip().isdigit() and end_str.strip().isdigit():
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    ids: set[int] = set()
                    for y in range(min(start, end), max(start, end) + 1):
                        ids |= index.by_year.get(str(y), set())
                    return ids & base
            exact = index.by_year.get(value_lc)
            if exact is not None:
                return exact & base
            return {idx for idx in base if value_lc in str(index.papers[idx].get("_year") or "").lower()}
        return set()

    result: set[int] = set()
    for group in query.groups:
        group_ids = set(all_ids)
        for term in group:
            matched = ids_for_term(term, group_ids if not term.negated else all_ids)
            if term.negated:
                group_ids -= matched
            else:
                group_ids &= matched
        result |= group_ids

    return result


def _page_shell(title: str, body_html: str, extra_head: str = "", extra_scripts: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; }}
      header {{ position: sticky; top: 0; background: #0b1220; color: #fff; padding: 12px 16px; z-index: 10; }}
      header a {{ color: #cfe3ff; text-decoration: none; margin-right: 12px; }}
      .container {{ max-width: 1100px; margin: 0 auto; padding: 16px; }}
      .filters {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin: 12px 0 16px; }}
      .filters input {{ width: 100%; padding: 8px; border: 1px solid #d0d7de; border-radius: 6px; }}
      .card {{ border: 1px solid #d0d7de; border-radius: 10px; padding: 12px; margin: 10px 0; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #d0d7de; margin-right: 6px; font-size: 12px; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
      .tab {{ display: inline-block; padding: 6px 12px; border-radius: 999px; border: 1px solid #d0d7de; background: #f6f8fa; color: #0969da; text-decoration: none; font-size: 13px; }}
      .tab:hover {{ background: #eef1f4; }}
      .tab.active {{ background: #0969da; border-color: #0969da; color: #fff; }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
    </style>
    {extra_head}
  </head>
  <body>
    <header>
      <a href="/">Papers</a>
      <a href="/stats">Stats</a>
    </header>
    <div class="container">
      {body_html}
    </div>
    {extra_scripts}
  </body>
</html>"""


def _embed_shell(title: str, body_html: str, extra_head: str = "", extra_scripts: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; padding: 16px; }}
      h1, h2, h3, h4 {{ margin-top: 1.2em; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
    </style>
    {extra_head}
  </head>
  <body>
    {body_html}
    {extra_scripts}
  </body>
</html>"""


def _build_pdfjs_viewer_url(pdf_url: str) -> str:
    encoded = quote(pdf_url, safe="")
    return f"{_PDFJS_VIEWER_PATH}?file={encoded}"


async def _index_page(request: Request) -> HTMLResponse:
    return HTMLResponse(
        _page_shell(
            "Paper DB",
            """
<h2>Paper Database</h2>
<div class="card">
  <div class="muted">Search (Scholar-style): <code>tag:fpga year:2023..2025 -survey</code> · Use quotes for phrases and <code>OR</code> for alternatives.</div>
  <div style="display:flex; gap:8px; margin-top:8px;">
    <input id="query" placeholder='Search... e.g. title:"nearest neighbor" tag:fpga year:2023..2025' style="flex:1; padding:10px; border:1px solid #d0d7de; border-radius:8px;" />
    <select id="openView" style="padding:10px; border:1px solid #d0d7de; border-radius:8px;">
      <option value="summary" selected>Open: Summary</option>
      <option value="source">Open: Source</option>
      <option value="pdf">Open: PDF</option>
      <option value="pdfjs">Open: PDF Viewer</option>
      <option value="split">Open: Split</option>
    </select>
  </div>
  <details style="margin-top:10px;">
    <summary>Advanced search</summary>
    <div style="margin-top:10px;" class="muted">Build a query:</div>
    <div class="filters" style="grid-template-columns: repeat(3, 1fr);">
      <input id="advTitle" placeholder="title contains..." />
      <input id="advAuthor" placeholder="author contains..." />
      <input id="advTag" placeholder="tag (comma separated)" />
      <input id="advYear" placeholder="year (e.g. 2020..2024)" />
      <input id="advMonth" placeholder="month (01-12)" />
      <input id="advVenue" placeholder="venue contains..." />
    </div>
    <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">
      <button id="buildQuery" style="padding:8px 12px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Build</button>
      <div class="muted">Generated: <code id="generated"></code></div>
    </div>
  </details>
</div>
<div id="results"></div>
<div id="loading" class="muted">Loading...</div>
<script>
let page = 1;
let loading = false;
let done = false;

function currentParams(nextPage) {
  const params = new URLSearchParams();
  params.set("page", String(nextPage));
  params.set("page_size", "30");
  const q = document.getElementById("query").value.trim();
  if (q) params.set("q", q);
  return params;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function viewSuffixForItem(item) {
  const view = document.getElementById("openView").value;
  if (!view || view === "summary") return "";
  const params = new URLSearchParams();
  params.set("view", view);
  if (view === "split") {
    params.set("left", "summary");
    if (item.has_pdf) {
      params.set("right", "pdfjs");
    } else if (item.has_source) {
      params.set("right", "source");
    } else {
      params.set("right", "summary");
    }
  }
  return `?${params.toString()}`;
}

function renderItem(item) {
  const tags = (item.tags || []).map(t => `<span class="pill">${escapeHtml(t)}</span>`).join("");
  const authors = (item.authors || []).slice(0, 6).map(a => escapeHtml(a)).join(", ");
  const meta = `${escapeHtml(item.year || "")}-${escapeHtml(item.month || "")} · ${escapeHtml(item.venue || "")}`;
  const viewSuffix = viewSuffixForItem(item);
  const badges = [
    item.has_source ? `<span class="pill">source</span>` : "",
    item.has_pdf ? `<span class="pill">pdf</span>` : "",
  ].join("");
  return `
    <div class="card">
      <div><a href="/paper/${encodeURIComponent(item.source_hash)}${viewSuffix}">${escapeHtml(item.title || "")}</a></div>
      <div class="muted">${authors}</div>
      <div class="muted">${meta}</div>
      <div style="margin-top:6px">${badges} ${tags}</div>
    </div>
  `;
}

async function loadMore() {
  if (loading || done) return;
  loading = true;
  document.getElementById("loading").textContent = "Loading...";
  const res = await fetch(`/api/papers?${currentParams(page).toString()}`);
  const data = await res.json();
  const results = document.getElementById("results");
  for (const item of data.items) {
    results.insertAdjacentHTML("beforeend", renderItem(item));
  }
  if (!data.has_more) {
    done = true;
    document.getElementById("loading").textContent = "End.";
  } else {
    page += 1;
    document.getElementById("loading").textContent = "Scroll to load more...";
  }
  loading = false;
}

function resetAndLoad() {
  page = 1;
  done = false;
  document.getElementById("results").innerHTML = "";
  loadMore();
}

document.getElementById("query").addEventListener("change", resetAndLoad);
document.getElementById("openView").addEventListener("change", resetAndLoad);

document.getElementById("buildQuery").addEventListener("click", () => {
  function add(field, value) {
    value = value.trim();
    if (!value) return "";
    if (value.includes(" ")) return `${field}:"${value}"`;
    return `${field}:${value}`;
  }
  const parts = [];
  const t = document.getElementById("advTitle").value.trim();
  const a = document.getElementById("advAuthor").value.trim();
  const tag = document.getElementById("advTag").value.trim();
  const y = document.getElementById("advYear").value.trim();
  const m = document.getElementById("advMonth").value.trim();
  const v = document.getElementById("advVenue").value.trim();
  if (t) parts.push(add("title", t));
  if (a) parts.push(add("author", a));
  if (tag) {
    for (const item of tag.split(",")) {
      const val = item.trim();
      if (val) parts.push(add("tag", val));
    }
  }
  if (y) parts.push(add("year", y));
  if (m) parts.push(add("month", m));
  if (v) parts.push(add("venue", v));
  const q = parts.join(" ");
  document.getElementById("generated").textContent = q;
  document.getElementById("query").value = q;
  resetAndLoad();
});

window.addEventListener("scroll", () => {
  if ((window.innerHeight + window.scrollY) >= (document.body.offsetHeight - 600)) {
    loadMore();
  }
});

loadMore();
</script>
""",
        )
    )


def _parse_filters(request: Request) -> dict[str, list[str] | str | int]:
    qp = request.query_params
    page = int(qp.get("page", "1"))
    page_size = int(qp.get("page_size", "30"))
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    q = qp.get("q", "").strip()

    return {
        "page": page,
        "page_size": page_size,
        "q": q,
    }


async def _api_papers(request: Request) -> JSONResponse:
    index: PaperIndex = request.app.state.index
    filters = _parse_filters(request)
    page = int(filters["page"])
    page_size = int(filters["page_size"])
    q = str(filters["q"])
    query = parse_query(q)
    candidate = _apply_query(index, query)
    ordered = [idx for idx in index.ordered_ids if idx in candidate]
    total = len(ordered)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_ids = ordered[start:end]

    items: list[dict[str, Any]] = []
    for idx in page_ids:
        paper = index.papers[idx]
        source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))
        items.append(
            {
                "source_hash": source_hash,
                "title": paper.get("paper_title") or "",
                "authors": paper.get("_authors") or [],
                "year": paper.get("_year") or "",
                "month": paper.get("_month") or "",
                "venue": paper.get("_venue") or "",
                "tags": paper.get("_tags") or [],
                "has_source": source_hash in index.md_path_by_hash,
                "has_pdf": source_hash in index.pdf_path_by_hash,
            }
        )

    return JSONResponse(
        {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": end < total,
            "items": items,
        }
    )


async def _paper_detail(request: Request) -> HTMLResponse:
    index: PaperIndex = request.app.state.index
    md = request.app.state.md
    source_hash = request.path_params["source_hash"]
    idx = index.id_by_hash.get(source_hash)
    if idx is None:
        return RedirectResponse("/")
    paper = index.papers[idx]
    view = request.query_params.get("view", "summary")
    template_param = request.query_params.get("template")
    embed = request.query_params.get("embed") == "1"
    if view == "split":
        embed = False

    pdf_path = index.pdf_path_by_hash.get(source_hash)
    pdf_url = f"/api/pdf/{source_hash}"
    shell = _embed_shell if embed else _page_shell
    source_available = source_hash in index.md_path_by_hash
    allowed_views = {"summary", "source", "pdf", "pdfjs"}

    def normalize_view(value: str | None, default: str) -> str:
        if value in allowed_views:
            return value
        return default

    default_right = "pdfjs" if pdf_path else ("source" if source_available else "summary")
    left_param = request.query_params.get("left")
    right_param = request.query_params.get("right")
    left = normalize_view(left_param, "summary") if left_param else "summary"
    right = normalize_view(right_param, default_right) if right_param else default_right

    def nav_link(label: str, v: str) -> str:
        active = " active" if view == v else ""
        params: dict[str, str] = {"view": v}
        if v == "summary" and template_param:
            params["template"] = str(template_param)
        if v == "split":
            params["left"] = left
            params["right"] = right
        href = f"/paper/{source_hash}?{urlencode(params)}"
        return f'<a class="tab{active}" href="{html.escape(href)}">{html.escape(label)}</a>'

    nav = f"""
<div class="tabs" style="margin: 8px 0 14px;">
  {nav_link("Summary", "summary")}
  {nav_link("Source", "source")}
  {nav_link("PDF", "pdf")}
  {nav_link("PDF Viewer", "pdfjs")}
  {nav_link("Split", "split")}
</div>
"""
    nav_html = "" if embed else nav

    if view == "split":
        def pane_src(pane_view: str) -> str:
            if pane_view == "pdfjs" and pdf_path:
                return _build_pdfjs_viewer_url(pdf_url)
            params: dict[str, str] = {"view": pane_view, "embed": "1"}
            if pane_view == "summary" and template_param:
                params["template"] = str(template_param)
            return f"/paper/{source_hash}?{urlencode(params)}"

        left_src = pane_src(left)
        right_src = pane_src(right)
        options = [
            ("summary", "Summary"),
            ("source", "Source"),
            ("pdf", "PDF"),
            ("pdfjs", "PDF Viewer"),
        ]
        left_options = "\n".join(
            f'<option value="{value}"{" selected" if value == left else ""}>{label}</option>'
            for value, label in options
        )
        right_options = "\n".join(
            f'<option value="{value}"{" selected" if value == right else ""}>{label}</option>'
            for value, label in options
        )
        body = f"""
<h2>{html.escape(str(paper.get('paper_title') or 'Paper'))}</h2>
{nav}
<div class="split-controls">
  <div>
    <div class="muted">Left pane</div>
    <select id="splitLeft">
      {left_options}
    </select>
  </div>
  <div class="split-actions">
    <button id="splitTighten" type="button" title="Tighten width">-</button>
    <button id="splitSwap" type="button" title="Swap panes">⇄</button>
    <button id="splitWiden" type="button" title="Widen width">+</button>
  </div>
  <div>
    <div class="muted">Right pane</div>
    <select id="splitRight">
      {right_options}
    </select>
  </div>
</div>
<div class="split-layout">
  <div class="split-pane">
    <iframe id="leftPane" src="{html.escape(left_src)}" title="Left pane"></iframe>
  </div>
  <div class="split-pane">
    <iframe id="rightPane" src="{html.escape(right_src)}" title="Right pane"></iframe>
  </div>
</div>
"""
        extra_head = """
<style>
.container {
  max-width: min(var(--split-max-width, 1600px), calc(100vw - 48px));
  width: 100%;
  margin: 0 auto;
}
.split-controls {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: end;
  margin: 10px 0 14px;
}
.split-controls select {
  padding: 6px 8px;
  border-radius: 8px;
  border: 1px solid #d0d7de;
  background: #fff;
  min-width: 160px;
}
.split-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
}
.split-actions button {
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid #d0d7de;
  background: #f6f8fa;
  cursor: pointer;
  min-width: 36px;
}
.split-layout {
  display: flex;
  gap: 12px;
  height: calc(100vh - 260px);
  min-height: 420px;
}
.split-pane {
  flex: 1;
  border: 1px solid #d0d7de;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}
.split-pane iframe {
  width: 100%;
  height: 100%;
  border: 0;
}
@media (max-width: 900px) {
  .split-layout {
    flex-direction: column;
    height: auto;
  }
  .split-pane {
    height: 70vh;
  }
  .split-controls {
    grid-template-columns: 1fr;
  }
}
</style>
"""
        extra_scripts = """
<script>
const leftSelect = document.getElementById('splitLeft');
const rightSelect = document.getElementById('splitRight');
const swapButton = document.getElementById('splitSwap');
const tightenButton = document.getElementById('splitTighten');
const widenButton = document.getElementById('splitWiden');
function updateSplit() {
  const params = new URLSearchParams(window.location.search);
  params.set('view', 'split');
  params.set('left', leftSelect.value);
  params.set('right', rightSelect.value);
  window.location.search = params.toString();
}
leftSelect.addEventListener('change', updateSplit);
rightSelect.addEventListener('change', updateSplit);
swapButton.addEventListener('click', () => {
  const leftValue = leftSelect.value;
  leftSelect.value = rightSelect.value;
  rightSelect.value = leftValue;
  updateSplit();
});
const widthSteps = [1200, 1400, 1600, 1800, 2000];
let widthIndex = 2;
try {
  const stored = localStorage.getItem('splitWidthIndex');
  if (stored !== null) {
    const parsed = Number.parseInt(stored, 10);
    if (!Number.isNaN(parsed)) {
      widthIndex = Math.max(0, Math.min(widthSteps.length - 1, parsed));
    }
  }
} catch (err) {
  // Ignore storage errors (e.g. private mode)
}

function applySplitWidth() {
  const value = `${widthSteps[widthIndex]}px`;
  document.documentElement.style.setProperty('--split-max-width', value);
  try {
    localStorage.setItem('splitWidthIndex', String(widthIndex));
  } catch (err) {
    // Ignore storage errors
  }
}

tightenButton.addEventListener('click', () => {
  widthIndex = Math.max(0, widthIndex - 1);
  applySplitWidth();
});
widenButton.addEventListener('click', () => {
  widthIndex = Math.min(widthSteps.length - 1, widthIndex + 1);
  applySplitWidth();
});
applySplitWidth();
</script>
"""
        return HTMLResponse(_page_shell("Split View", body, extra_head=extra_head, extra_scripts=extra_scripts))

    if view == "source":
        source_path = index.md_path_by_hash.get(source_hash)
        if not source_path:
            body = nav_html + '<div class="warning">Source markdown not found. Provide --md-root to enable source viewing.</div>'
            return HTMLResponse(shell("Source", body))
        try:
            raw = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = source_path.read_text(encoding="latin-1")
        rendered = _render_markdown_with_math_placeholders(md, raw)
        body = (
            nav_html
            + f"<h2>{html.escape(str(paper.get('paper_title') or 'Paper'))}</h2>"
            + f'<div class="muted">{html.escape(str(source_path))}</div>'
            + '<div class="muted" style="margin-top:10px;">Rendered from source markdown:</div>'
            + f'<div id="content">{rendered}</div>'
            + "<details style='margin-top:12px;'><summary>Raw markdown</summary>"
            + f"<pre><code>{html.escape(raw)}</code></pre></details>"
        )
        extra_head = f'<link rel="stylesheet" href="{_CDN_KATEX}" />'
        extra_scripts = f"""
<script src="{_CDN_MERMAID}"></script>
<script src="{_CDN_KATEX_JS}"></script>
<script src="{_CDN_KATEX_AUTO}"></script>
<script>
document.querySelectorAll('code.language-mermaid').forEach((code) => {{
  const pre = code.parentElement;
  const div = document.createElement('div');
  div.className = 'mermaid';
  div.textContent = code.textContent;
  pre.replaceWith(div);
}});
if (window.mermaid) {{
  mermaid.initialize({{ startOnLoad: false }});
  mermaid.run();
}}
if (window.renderMathInElement) {{
  renderMathInElement(document.getElementById('content'), {{
    delimiters: [
      {{left: '$$', right: '$$', display: true}},
      {{left: '$', right: '$', display: false}},
      {{left: '\\\\(', right: '\\\\)', display: false}},
      {{left: '\\\\[', right: '\\\\]', display: true}}
    ],
    throwOnError: false
  }});
}}
</script>
"""
        return HTMLResponse(shell("Source", body, extra_head=extra_head, extra_scripts=extra_scripts))

    if view == "pdf":
        if not pdf_path:
            body = nav_html + '<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>'
            return HTMLResponse(shell("PDF", body))
        body = nav_html + f"""
<h2>{html.escape(str(paper.get('paper_title') or 'Paper'))}</h2>
<div class="muted">{html.escape(str(pdf_path.name))}</div>
<div style="display:flex; gap:8px; align-items:center; margin: 10px 0;">
  <button id="prev" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Prev</button>
  <button id="next" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">Next</button>
  <span class="muted">Page <span id="page_num">1</span> / <span id="page_count">?</span></span>
  <span style="flex:1"></span>
  <button id="zoomOut" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">-</button>
  <button id="zoomIn" style="padding:6px 10px; border-radius:8px; border:1px solid #d0d7de; background:#f6f8fa; cursor:pointer;">+</button>
</div>
<canvas id="the-canvas" style="width: 100%; border: 1px solid #d0d7de; border-radius: 10px;"></canvas>
"""
        extra_scripts = f"""
<script src="{_CDN_PDFJS}"></script>
<script>
const url = {json.dumps(pdf_url)};
pdfjsLib.GlobalWorkerOptions.workerSrc = {json.dumps(_CDN_PDFJS_WORKER)};
let pdfDoc = null;
let pageNum = 1;
let pageRendering = false;
let pageNumPending = null;
let zoomLevel = 1.0;
const canvas = document.getElementById('the-canvas');
const ctx = canvas.getContext('2d');

function renderPage(num) {{
  pageRendering = true;
  pdfDoc.getPage(num).then((page) => {{
    const baseViewport = page.getViewport({{scale: 1}});
    const containerWidth = canvas.clientWidth || baseViewport.width;
    const fitScale = containerWidth / baseViewport.width;
    const scale = fitScale * zoomLevel;

    const viewport = page.getViewport({{scale}});
    const outputScale = window.devicePixelRatio || 1;

    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    canvas.style.width = Math.floor(viewport.width) + 'px';
    canvas.style.height = Math.floor(viewport.height) + 'px';

    const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null;
    const renderContext = {{ canvasContext: ctx, viewport, transform }};
    const renderTask = page.render(renderContext);
    renderTask.promise.then(() => {{
      pageRendering = false;
      document.getElementById('page_num').textContent = String(pageNum);
      if (pageNumPending !== null) {{
        const next = pageNumPending;
        pageNumPending = null;
        renderPage(next);
      }}
    }});
  }});
}}

function queueRenderPage(num) {{
  if (pageRendering) {{
    pageNumPending = num;
  }} else {{
    renderPage(num);
  }}
}}

function onPrevPage() {{
  if (pageNum <= 1) return;
  pageNum--;
  queueRenderPage(pageNum);
}}

function onNextPage() {{
  if (pageNum >= pdfDoc.numPages) return;
  pageNum++;
  queueRenderPage(pageNum);
}}

function adjustZoom(delta) {{
  zoomLevel = Math.max(0.5, Math.min(3.0, zoomLevel + delta));
  queueRenderPage(pageNum);
}}

document.getElementById('prev').addEventListener('click', onPrevPage);
document.getElementById('next').addEventListener('click', onNextPage);
document.getElementById('zoomOut').addEventListener('click', () => adjustZoom(-0.1));
document.getElementById('zoomIn').addEventListener('click', () => adjustZoom(0.1));

pdfjsLib.getDocument(url).promise.then((pdfDoc_) => {{
  pdfDoc = pdfDoc_;
  document.getElementById('page_count').textContent = String(pdfDoc.numPages);
  renderPage(pageNum);
}});

let resizeTimer = null;
window.addEventListener('resize', () => {{
  if (!pdfDoc) return;
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => queueRenderPage(pageNum), 150);
}});
</script>
"""
        return HTMLResponse(shell("PDF", body, extra_scripts=extra_scripts))

    if view == "pdfjs":
        if not pdf_path:
            body = nav_html + '<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>'
            return HTMLResponse(shell("PDF Viewer", body))
        viewer_url = _build_pdfjs_viewer_url(pdf_url)
        header_html = ""
        if not embed:
            header_html = (
                f"<h2>{html.escape(str(paper.get('paper_title') or 'Paper'))}</h2>"
                + f'<div class="muted">{html.escape(str(pdf_path.name))}</div>'
            )
        frame_height = "calc(100vh - 220px)" if not embed else "calc(100vh - 32px)"
        body = f"""
{nav_html}
{header_html}
<iframe class="pdfjs-frame" src="{html.escape(viewer_url)}" title="PDF.js Viewer"></iframe>
"""
        extra_head = f"""
<style>
.pdfjs-frame {{
  width: 100%;
  height: {frame_height};
  border: 1px solid #d0d7de;
  border-radius: 10px;
}}
</style>
"""
        return HTMLResponse(shell("PDF Viewer", body, extra_head=extra_head))

    selected_tag, available_templates = _select_template_tag(paper, template_param)
    markdown, template_name, warning = _render_paper_markdown(
        paper,
        request.app.state.fallback_language,
        template_tag=selected_tag,
    )
    rendered_html = _render_markdown_with_math_placeholders(md, markdown)

    warning_html = f'<div class="warning">{html.escape(warning)}</div>' if warning else ""
    title = str(paper.get("paper_title") or "Paper")
    outline_top = "72px" if not embed else "16px"
    template_controls = f'<div class="muted">Template: {html.escape(template_name)}</div>'
    if available_templates:
        options = "\n".join(
            f'<option value="{html.escape(tag)}"{" selected" if tag == selected_tag else ""}>{html.escape(tag)}</option>'
            for tag in available_templates
        )
        template_controls = f"""
<div class="muted" style="margin: 6px 0;">
  Template:
  <select id="templateSelect" style="padding:6px 8px; border:1px solid #d0d7de; border-radius:6px;">
    {options}
  </select>
</div>
<script>
const templateSelect = document.getElementById('templateSelect');
if (templateSelect) {{
  templateSelect.addEventListener('change', () => {{
    const params = new URLSearchParams(window.location.search);
    params.set('view', 'summary');
    params.set('template', templateSelect.value);
    window.location.search = params.toString();
  }});
}}
</script>
"""
    outline_html = """
<button id="outlineToggle" class="outline-toggle" title="Toggle outline">☰</button>
<div id="outlinePanel" class="outline-panel collapsed">
  <div class="outline-title">Outline</div>
  <div id="outlineList" class="outline-list"></div>
</div>
<button id="backToTop" class="back-to-top" title="Back to top">↑</button>
"""
    body = f"""
<h2>{html.escape(title)}</h2>
{template_controls}
{warning_html}
{nav_html}
{outline_html}
<div id="content">{rendered_html}</div>
"""

    extra_head = f"""
<link rel="stylesheet" href="{_CDN_KATEX}" />
<style>
:root {{
  --outline-top: {outline_top};
}}
.outline-toggle {{
  position: fixed;
  top: var(--outline-top);
  left: 16px;
  z-index: 20;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid #d0d7de;
  background: #f6f8fa;
  cursor: pointer;
}}
.outline-panel {{
  position: fixed;
  top: calc(var(--outline-top) + 42px);
  left: 16px;
  width: 240px;
  max-height: 60vh;
  overflow: auto;
  border: 1px solid #d0d7de;
  border-radius: 10px;
  background: #ffffff;
  padding: 10px;
  z-index: 20;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.08);
}}
.outline-panel.collapsed {{
  display: none;
}}
.outline-title {{
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #57606a;
  margin-bottom: 8px;
}}
.outline-list a {{
  display: block;
  color: #0969da;
  text-decoration: none;
  padding: 4px 0;
}}
.outline-list a:hover {{
  text-decoration: underline;
}}
.back-to-top {{
  position: fixed;
  left: 16px;
  bottom: 16px;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid #d0d7de;
  background: #ffffff;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
  z-index: 20;
}}
.back-to-top.visible {{
  opacity: 1;
  pointer-events: auto;
}}
@media (max-width: 900px) {{
  .outline-panel {{
    width: 200px;
  }}
}}
</style>
"""
    extra_scripts = f"""
<script src="{_CDN_MERMAID}"></script>
<script src="{_CDN_KATEX_JS}"></script>
<script src="{_CDN_KATEX_AUTO}"></script>
<script>
// Mermaid: convert fenced code blocks to mermaid divs
document.querySelectorAll('code.language-mermaid').forEach((code) => {{
  const pre = code.parentElement;
  const div = document.createElement('div');
  div.className = 'mermaid';
  div.textContent = code.textContent;
  pre.replaceWith(div);
}});
if (window.mermaid) {{
  mermaid.initialize({{ startOnLoad: false }});
  mermaid.run();
}}
if (window.renderMathInElement) {{
  renderMathInElement(document.getElementById('content'), {{
    delimiters: [
      {{left: '$$', right: '$$', display: true}},
      {{left: '$', right: '$', display: false}},
      {{left: '\\\\(', right: '\\\\)', display: false}},
      {{left: '\\\\[', right: '\\\\]', display: true}}
    ],
    throwOnError: false
  }});
}}
const outlineToggle = document.getElementById('outlineToggle');
const outlinePanel = document.getElementById('outlinePanel');
const outlineList = document.getElementById('outlineList');
const backToTop = document.getElementById('backToTop');

function slugify(text) {{
  return text.toLowerCase().trim()
    .replace(/[^a-z0-9\\s-]/g, '')
    .replace(/\\s+/g, '-')
    .replace(/-+/g, '-');
}}

function buildOutline() {{
  if (!outlineList) return;
  const content = document.getElementById('content');
  if (!content) return;
  const headings = content.querySelectorAll('h1, h2, h3, h4');
  if (!headings.length) {{
    outlineList.innerHTML = '<div class="muted">No headings</div>';
    return;
  }}
  const used = new Set();
  outlineList.innerHTML = '';
  headings.forEach((heading) => {{
    let id = heading.id;
    if (!id) {{
      const base = slugify(heading.textContent || 'section') || 'section';
      id = base;
      let i = 1;
      while (used.has(id) || document.getElementById(id)) {{
        id = `${{base}}-${{i++}}`;
      }}
      heading.id = id;
    }}
    used.add(id);
    const level = parseInt(heading.tagName.slice(1), 10) || 1;
    const link = document.createElement('a');
    link.href = `#${{id}}`;
    link.textContent = heading.textContent || '';
    link.style.paddingLeft = `${{(level - 1) * 12}}px`;
    outlineList.appendChild(link);
  }});
}}

function toggleBackToTop() {{
  if (!backToTop) return;
  if (window.scrollY > 300) {{
    backToTop.classList.add('visible');
  }} else {{
    backToTop.classList.remove('visible');
  }}
}}

if (outlineToggle && outlinePanel) {{
  outlineToggle.addEventListener('click', () => {{
    outlinePanel.classList.toggle('collapsed');
  }});
}}

if (backToTop) {{
  backToTop.addEventListener('click', () => {{
    window.scrollTo({{ top: 0, behavior: 'smooth' }});
  }});
}}

buildOutline();
window.addEventListener('scroll', toggleBackToTop);
toggleBackToTop();
</script>
"""
    return HTMLResponse(shell(title, body, extra_head=extra_head, extra_scripts=extra_scripts))


async def _api_stats(request: Request) -> JSONResponse:
    index: PaperIndex = request.app.state.index
    return JSONResponse(index.stats)


async def _api_pdf(request: Request) -> Response:
    index: PaperIndex = request.app.state.index
    source_hash = request.path_params["source_hash"]
    pdf_path = index.pdf_path_by_hash.get(source_hash)
    if not pdf_path:
        return Response("PDF not found", status_code=404)
    allowed_roots: list[Path] = request.app.state.pdf_roots
    if allowed_roots and not _ensure_under_roots(pdf_path, allowed_roots):
        return Response("Forbidden", status_code=403)
    return FileResponse(pdf_path)


async def _stats_page(request: Request) -> HTMLResponse:
    body = """
<h2>Stats</h2>
<div class="muted">Charts are rendered with ECharts (CDN).</div>
<div id="year" style="width:100%;height:360px"></div>
<div id="month" style="width:100%;height:360px"></div>
<div id="tags" style="width:100%;height:420px"></div>
<div id="authors" style="width:100%;height:420px"></div>
<div id="venues" style="width:100%;height:420px"></div>
"""
    scripts = f"""
<script src="{_CDN_ECHARTS}"></script>
<script>
async function main() {{
  const res = await fetch('/api/stats');
  const data = await res.json();

  function bar(el, title, items) {{
    const chart = echarts.init(document.getElementById(el));
    const labels = items.map(x => x.label);
    const counts = items.map(x => x.count);
    chart.setOption({{
      title: {{ text: title }},
      tooltip: {{ trigger: 'axis' }},
      xAxis: {{ type: 'category', data: labels }},
      yAxis: {{ type: 'value' }},
      series: [{{ type: 'bar', data: counts }}]
    }});
  }}

  bar('year', 'Publication Year', data.years || []);
  bar('month', 'Publication Month', data.months || []);
  bar('tags', 'Top Tags', (data.tags || []).slice(0, 20));
  bar('authors', 'Top Authors', (data.authors || []).slice(0, 20));
  bar('venues', 'Top Venues', (data.venues || []).slice(0, 20));
}}
main();
</script>
"""
    return HTMLResponse(_page_shell("Stats", body, extra_scripts=scripts))


def _normalize_bibtex_title(title: str) -> str:
    value = title.replace("{", "").replace("}", "")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\\s+", " ", value).strip()


def _title_similarity(a: str, b: str) -> float:
    import difflib

    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def enrich_with_bibtex(papers: list[dict[str, Any]], bibtex_path: Path) -> None:
    if not PYBTEX_AVAILABLE:
        raise RuntimeError("pybtex is required for --bibtex support")

    bib_data = parse_file(str(bibtex_path))
    entries: list[dict[str, Any]] = []
    by_prefix: dict[str, list[int]] = {}
    for key, entry in bib_data.entries.items():
        fields = dict(entry.fields)
        title = str(fields.get("title") or "").strip()
        title_norm = _normalize_bibtex_title(title)
        if not title_norm:
            continue
        record = {
            "key": key,
            "type": entry.type,
            "fields": fields,
            "persons": {role: [str(p) for p in persons] for role, persons in entry.persons.items()},
            "_title_norm": title_norm,
        }
        idx = len(entries)
        entries.append(record)
        prefix = title_norm[:16]
        by_prefix.setdefault(prefix, []).append(idx)

    for paper in papers:
        if isinstance(paper.get("bibtex"), dict):
            continue
        title = str(paper.get("paper_title") or "").strip()
        if not title:
            continue
        norm = _normalize_bibtex_title(title)
        if not norm:
            continue

        candidates = []
        prefix = norm[:16]
        for cand_idx in by_prefix.get(prefix, []):
            candidates.append(entries[cand_idx])
        if not candidates:
            candidates = entries

        best = None
        best_score = 0.0
        for entry in candidates:
            score = _title_similarity(norm, entry["_title_norm"])
            if score > best_score:
                best_score = score
                best = entry

        if best is not None and best_score >= 0.9:
            paper["bibtex"] = {k: v for k, v in best.items() if not k.startswith("_")}


def create_app(
    *,
    db_paths: list[Path],
    fallback_language: str = "en",
    bibtex_path: Path | None = None,
    md_roots: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
) -> Starlette:
    papers = _load_or_merge_papers(db_paths, bibtex_path, cache_dir, use_cache)

    md_roots = md_roots or []
    pdf_roots = pdf_roots or []
    index = build_index(papers, md_roots=md_roots, pdf_roots=pdf_roots)
    md = _md_renderer()
    routes = [
        Route("/", _index_page, methods=["GET"]),
        Route("/stats", _stats_page, methods=["GET"]),
        Route("/paper/{source_hash:str}", _paper_detail, methods=["GET"]),
        Route("/api/papers", _api_papers, methods=["GET"]),
        Route("/api/stats", _api_stats, methods=["GET"]),
        Route("/api/pdf/{source_hash:str}", _api_pdf, methods=["GET"]),
    ]
    if _PDFJS_STATIC_DIR.exists():
        routes.append(
            Mount(
                "/pdfjs",
                app=StaticFiles(directory=str(_PDFJS_STATIC_DIR), html=True),
                name="pdfjs",
            )
        )
    app = Starlette(routes=routes)
    app.state.index = index
    app.state.md = md
    app.state.fallback_language = fallback_language
    app.state.pdf_roots = pdf_roots
    return app
