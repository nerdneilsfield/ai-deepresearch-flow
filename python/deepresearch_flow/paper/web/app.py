from __future__ import annotations

import html
import json
import logging
import unicodedata
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

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False


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

logger = logging.getLogger(__name__)


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
    translated_md_by_hash: dict[str, dict[str, Path]]
    pdf_path_by_hash: dict[str, Path]
    template_tags: list[str]


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


def _extract_keywords(paper: dict[str, Any]) -> list[str]:
    keywords = paper.get("keywords") or []
    if isinstance(keywords, list):
        return [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if isinstance(keywords, str):
        parts = re.split(r"[;,]", keywords)
        return [part.strip() for part in parts if part.strip()]
    return []


_SUMMARY_FIELDS = (
    "summary",
    "abstract",
    "keywords",
    "question1",
    "question2",
    "question3",
    "question4",
    "question5",
    "question6",
    "question7",
    "question8",
)


def _has_summary(paper: dict[str, Any], template_tags: list[str]) -> bool:
    if template_tags:
        return True
    for key in _SUMMARY_FIELDS:
        value = paper.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


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
    md_translated_roots: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
) -> PaperIndex:
    id_by_hash: dict[str, int] = {}
    by_tag: dict[str, set[int]] = {}
    by_author: dict[str, set[int]] = {}
    by_year: dict[str, set[int]] = {}
    by_month: dict[str, set[int]] = {}
    by_venue: dict[str, set[int]] = {}

    md_path_by_hash: dict[str, Path] = {}
    translated_md_by_hash: dict[str, dict[str, Path]] = {}
    pdf_path_by_hash: dict[str, Path] = {}

    md_file_index = _build_file_index(md_roots or [], suffixes={".md"})
    translated_index = _build_translated_index(md_translated_roots or [])
    pdf_file_index = _build_file_index(pdf_roots or [], suffixes={".pdf"})

    year_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    author_counts: dict[str, int] = {}
    venue_counts: dict[str, int] = {}
    template_tag_counts: dict[str, int] = {}

    def add_index(index: dict[str, set[int]], key: str, idx: int) -> None:
        index.setdefault(key, set()).add(idx)

    for idx, paper in enumerate(papers):
        is_pdf_only = bool(paper.get("_is_pdf_only"))
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
        if not is_pdf_only:
            year_counts[year_label] = year_counts.get(year_label, 0) + 1
            month_counts[month_label] = month_counts.get(month_label, 0) + 1

        venue = _extract_venue(paper).strip()
        paper["_venue"] = venue
        if venue:
            add_index(by_venue, _normalize_key(venue), idx)
            if not is_pdf_only:
                venue_counts[venue] = venue_counts.get(venue, 0) + 1
        else:
            add_index(by_venue, "unknown", idx)
            if not is_pdf_only:
                venue_counts["Unknown"] = venue_counts.get("Unknown", 0) + 1

        authors = _extract_authors(paper)
        paper["_authors"] = authors
        for author in authors:
            key = _normalize_key(author)
            add_index(by_author, key, idx)
            if not is_pdf_only:
                author_counts[author] = author_counts.get(author, 0) + 1

        tags = _extract_tags(paper)
        paper["_tags"] = tags
        for tag in tags:
            key = _normalize_key(tag)
            add_index(by_tag, key, idx)
            if not is_pdf_only:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        keywords = _extract_keywords(paper)
        paper["_keywords"] = keywords
        for keyword in keywords:
            if not is_pdf_only:
                keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

        template_tags = _available_templates(paper)
        if not template_tags:
            fallback_tag = paper.get("template_tag") or paper.get("prompt_template")
            if fallback_tag:
                template_tags = [str(fallback_tag)]
        paper["_template_tags"] = template_tags
        paper["_template_tags_lc"] = [tag.lower() for tag in template_tags]
        paper["_has_summary"] = _has_summary(paper, template_tags)
        if not is_pdf_only:
            for tag in template_tags:
                template_tag_counts[tag] = template_tag_counts.get(tag, 0) + 1

        search_parts = [title, venue, " ".join(authors), " ".join(tags)]
        paper["_search_lc"] = " ".join(part for part in search_parts if part).lower()

        source_hash_str = str(source_hash) if source_hash else str(idx)
        md_path = _resolve_source_md(paper, md_file_index)
        if md_path is not None:
            md_path_by_hash[source_hash_str] = md_path
            base_key = md_path.with_suffix("").name.lower()
            translations = translated_index.get(base_key, {})
            if translations:
                translated_md_by_hash[source_hash_str] = translations
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

    stats_total = sum(1 for paper in papers if not paper.get("_is_pdf_only"))
    stats = {
        "total": stats_total,
        "years": _sorted_counts(year_counts, numeric_desc=True),
        "months": _sorted_month_counts(month_counts),
        "tags": _sorted_counts(tag_counts),
        "keywords": _sorted_counts(keyword_counts),
        "authors": _sorted_counts(author_counts),
        "venues": _sorted_counts(venue_counts),
    }

    template_tags = sorted(template_tag_counts.keys(), key=lambda item: item.lower())

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
        translated_md_by_hash=translated_md_by_hash,
        pdf_path_by_hash=pdf_path_by_hash,
        template_tags=template_tags,
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


def _build_cache_meta(
    db_paths: list[Path],
    bibtex_path: Path | None,
    pdf_roots_meta: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    if pdf_roots_meta is not None:
        meta["pdf_roots"] = pdf_roots_meta
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


def _extract_year_for_matching(paper: dict[str, Any]) -> str | None:
    if isinstance(paper.get("bibtex"), dict):
        fields = paper.get("bibtex", {}).get("fields", {}) or {}
        year = fields.get("year")
        if year and str(year).isdigit():
            return str(year)
    parsed_year, _ = _parse_year_month(str(paper.get("publication_date") or ""))
    return parsed_year


def _prepare_paper_matching_fields(paper: dict[str, Any]) -> None:
    if "_authors" not in paper:
        paper["_authors"] = _extract_authors(paper)
    if "_year" not in paper:
        paper["_year"] = _extract_year_for_matching(paper) or ""


def _build_pdf_only_entries(
    papers: list[dict[str, Any]],
    pdf_paths: list[Path],
    pdf_index: dict[str, list[Path]],
) -> list[dict[str, Any]]:
    matched: set[Path] = set()
    for paper in papers:
        _prepare_paper_matching_fields(paper)
        pdf_path = _resolve_pdf(paper, pdf_index)
        if pdf_path:
            matched.add(pdf_path.resolve())

    entries: list[dict[str, Any]] = []
    for path in pdf_paths:
        resolved = path.resolve()
        if resolved in matched:
            continue
        title = _read_pdf_metadata_title(resolved) or _extract_title_from_filename(resolved.name)
        if not title:
            title = resolved.stem
        year_hint, author_hint = _extract_year_author_from_filename(resolved.name)
        entry: dict[str, Any] = {
            "paper_title": title,
            "paper_authors": [author_hint] if author_hint else [],
            "publication_date": year_hint or "",
            "source_hash": stable_hash(str(resolved)),
            "source_path": str(resolved),
            "_is_pdf_only": True,
        }
        entries.append(entry)
    return entries


def _load_or_merge_papers(
    db_paths: list[Path],
    bibtex_path: Path | None,
    cache_dir: Path | None,
    use_cache: bool,
    pdf_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    cache_meta = None
    pdf_roots = pdf_roots or []
    pdf_paths: list[Path] = []
    pdf_roots_meta: list[dict[str, Any]] | None = None
    if pdf_roots:
        pdf_paths, pdf_roots_meta = _scan_pdf_roots(pdf_roots)
    if cache_dir and use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_meta = _build_cache_meta(db_paths, bibtex_path, pdf_roots_meta)
        cached = _load_cached_papers(cache_dir, cache_meta)
        if cached is not None:
            return cached

    inputs = _load_paper_inputs(db_paths)
    if bibtex_path is not None:
        for bundle in inputs:
            enrich_with_bibtex(bundle["papers"], bibtex_path)
    papers = _merge_paper_inputs(inputs)
    if pdf_paths:
        pdf_index = _build_file_index_from_paths(pdf_paths, suffixes={".pdf"})
        papers.extend(_build_pdf_only_entries(papers, pdf_paths, pdf_index))

    if cache_dir and use_cache and cache_meta is not None:
        _write_cached_papers(cache_dir, cache_meta, papers)
    return papers


def _md_renderer() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True})
    md.enable("table")
    return md


def _strip_paragraph_wrapped_tables(text: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        line = re.sub(r"^\s*<p>\s*\|", "|", line)
        line = re.sub(r"\|\s*</p>\s*$", "|", line)
        lines[idx] = line
    return "\n".join(lines)


def _normalize_markdown_images(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    img_re = re.compile(r"!\[[^\]]*\]\((?:[^)\\]|\\.)*\)")
    list_re = re.compile(r"^\s{0,3}(-|\*|\+|\d{1,9}\.)\s+")

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            run_len = 0
            while run_len < len(stripped) and stripped[run_len] == stripped[0]:
                run_len += 1
            if not in_fence:
                in_fence = True
                fence_char = stripped[0]
                fence_len = run_len
            elif stripped[0] == fence_char and run_len >= fence_len:
                in_fence = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        match = img_re.search(line)
        if not match:
            out.append(line)
            continue
        if list_re.match(line) or (line.lstrip().startswith("|") and line.count("|") >= 2):
            out.append(line)
            continue
        prefix = line[:match.start()]
        if prefix.strip():
            out.append(prefix.rstrip())
            out.append("")
            out.append(line[match.start():].lstrip())
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


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
    text = _strip_paragraph_wrapped_tables(text)
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


_TITLE_PREFIX_LEN = 16
_TITLE_MIN_CHARS = 24
_TITLE_MIN_TOKENS = 4
_AUTHOR_YEAR_MIN_SIMILARITY = 0.8
_LEADING_NUMERIC_MAX_LEN = 2
_SIMILARITY_START = 0.95
_SIMILARITY_STEP = 0.05
_SIMILARITY_MAX_STEPS = 10


def _normalize_title_key(title: str) -> str:
    value = unicodedata.normalize("NFKD", title)
    greek_map = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "ε": "epsilon",
        "ζ": "zeta",
        "η": "eta",
        "θ": "theta",
        "ι": "iota",
        "κ": "kappa",
        "λ": "lambda",
        "μ": "mu",
        "ν": "nu",
        "ξ": "xi",
        "ο": "omicron",
        "π": "pi",
        "ρ": "rho",
        "σ": "sigma",
        "τ": "tau",
        "υ": "upsilon",
        "φ": "phi",
        "χ": "chi",
        "ψ": "psi",
        "ω": "omega",
    }
    for char, name in greek_map.items():
        value = value.replace(char, f" {name} ")
    value = re.sub(
        r"\\(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|omicron|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega)\b",
        r" \1 ",
        value,
        flags=re.IGNORECASE,
    )
    value = value.replace("{", "").replace("}", "")
    value = value.replace("_", " ")
    value = re.sub(r"([a-z])([0-9])", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"([0-9])([a-z])", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    tokens = value.split()
    if not tokens:
        return ""
    merged: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if len(token) == 1 and idx + 1 < len(tokens):
            merged.append(token + tokens[idx + 1])
            idx += 2
            continue
        merged.append(token)
        idx += 1
    return " ".join(merged)


def _compact_title_key(title_key: str) -> str:
    return title_key.replace(" ", "")


def _strip_leading_numeric_tokens(title_key: str) -> str:
    tokens = title_key.split()
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.isdigit() and len(token) <= _LEADING_NUMERIC_MAX_LEN:
            idx += 1
            continue
        break
    if idx == 0:
        return title_key
    return " ".join(tokens[idx:])


def _strip_pdf_hash_suffix(name: str) -> str:
    return re.sub(r"(?i)(\.pdf)(?:-[0-9a-f\-]{8,})$", r"\1", name)


def _extract_title_from_filename(name: str) -> str:
    base = name
    lower = base.lower()
    if lower.endswith(".md"):
        base = base[:-3]
        lower = base.lower()
    if ".pdf-" in lower:
        base = _strip_pdf_hash_suffix(base)
        lower = base.lower()
    if lower.endswith(".pdf"):
        base = base[:-4]
    base = base.replace("_", " ").strip()
    match = re.match(r"\s*\d{4}\s*-\s*(.+)$", base)
    if match:
        return match.group(1).strip()
    match = re.match(r"\s*.+?\s*-\s*\d{4}\s*-\s*(.+)$", base)
    if match:
        return match.group(1).strip()
    return base.strip()


def _clean_pdf_metadata_title(value: str | None, path: Path) -> str | None:
    if not value:
        return None
    text = str(value).replace("\x00", "").strip()
    if not text:
        return None
    text = re.sub(r"(?i)^microsoft\\s+word\\s*-\\s*", "", text)
    text = re.sub(r"(?i)^pdf\\s*-\\s*", "", text)
    text = re.sub(r"(?i)^untitled\\b", "", text).strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4].strip()
    if len(text) < 3:
        return None
    stem = path.stem.strip()
    if stem and text.lower() == stem.lower():
        return None
    return text


def _read_pdf_metadata_title(path: Path) -> str | None:
    if not PYPDF_AVAILABLE:
        return None
    try:
        reader = PdfReader(str(path))
        meta = reader.metadata
        title = meta.title if meta else None
    except Exception:
        return None
    return _clean_pdf_metadata_title(title, path)


def _is_pdf_like(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return True
    name_lower = path.name.lower()
    return ".pdf-" in name_lower and not name_lower.endswith(".md")


def _scan_pdf_roots(roots: list[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    pdf_paths: list[Path] = []
    meta: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        files: list[Path] = []
        for path in root.rglob("*"):
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if not _is_pdf_like(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)
        max_mtime = 0.0
        total_size = 0
        for path in files:
            try:
                stats = path.stat()
            except OSError:
                continue
            max_mtime = max(max_mtime, stats.st_mtime)
            total_size += stats.st_size
        pdf_paths.extend(files)
        meta.append(
            {
                "path": str(root),
                "count": len(files),
                "max_mtime": max_mtime,
                "size": total_size,
            }
        )
    return pdf_paths, meta


def _extract_year_author_from_filename(name: str) -> tuple[str | None, str | None]:
    base = name
    lower = base.lower()
    if lower.endswith(".md"):
        base = base[:-3]
        lower = base.lower()
    if ".pdf-" in lower:
        base = _strip_pdf_hash_suffix(base)
        lower = base.lower()
    if lower.endswith(".pdf"):
        base = base[:-4]
    match = re.match(r"\s*(.+?)\s*-\s*((?:19|20)\d{2})\s*-\s*", base)
    if match:
        return match.group(2), match.group(1).strip()
    match = re.match(r"\s*((?:19|20)\d{2})\s*-\s*", base)
    if match:
        return match.group(1), None
    return None, None


def _normalize_author_key(name: str) -> str:
    raw = name.lower().strip()
    raw = raw.replace("et al.", "").replace("et al", "")
    if "," in raw:
        raw = raw.split(",", 1)[0]
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""
    parts = raw.split()
    return parts[-1] if parts else raw


def _title_prefix_key(title_key: str) -> str | None:
    if len(title_key.split()) < _TITLE_MIN_TOKENS:
        return None
    compact = _compact_title_key(title_key)
    if len(compact) < _TITLE_PREFIX_LEN:
        return None
    prefix = compact[:_TITLE_PREFIX_LEN]
    if not prefix:
        return None
    return f"prefix:{prefix}"


def _title_overlap_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    token_count = len(shorter.split())
    if len(shorter) >= _TITLE_MIN_CHARS or token_count >= _TITLE_MIN_TOKENS:
        if longer.startswith(shorter) or shorter in longer:
            return True
    return False


def _adaptive_similarity_match(title_key: str, candidates: list[Path]) -> Path | None:
    if not title_key:
        return None
    scored: list[tuple[Path, float]] = []
    for path in candidates:
        candidate_title = _normalize_title_key(_extract_title_from_filename(path.name))
        if not candidate_title:
            continue
        if _title_overlap_match(title_key, candidate_title):
            return path
        scored.append((path, _title_similarity(title_key, candidate_title)))
    if not scored:
        return None

    def matches_at(threshold: float) -> list[Path]:
        return [path for path, score in scored if score >= threshold]

    threshold = _SIMILARITY_START
    step = _SIMILARITY_STEP
    prev_threshold = None
    prev_count = None
    for _ in range(_SIMILARITY_MAX_STEPS):
        matches = matches_at(threshold)
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            prev_threshold = threshold
            prev_count = 0
            threshold -= step
            continue
        if prev_count == 0 and prev_threshold is not None:
            low = threshold
            high = prev_threshold
            for _ in range(_SIMILARITY_MAX_STEPS):
                mid = (low + high) / 2
                mid_matches = matches_at(mid)
                if len(mid_matches) == 1:
                    return mid_matches[0]
                if len(mid_matches) == 0:
                    high = mid
                else:
                    low = mid
            return None
        prev_threshold = threshold
        prev_count = len(matches)
        threshold -= step
    return None


def _resolve_by_title_and_meta(
    paper: dict[str, Any],
    file_index: dict[str, list[Path]],
) -> Path | None:
    title = str(paper.get("paper_title") or "")
    title_key = _normalize_title_key(title)
    if not title_key:
        title_key = ""
    candidates = file_index.get(title_key, [])
    if candidates:
        return candidates[0]
    if title_key:
        compact_key = _compact_title_key(title_key)
        compact_candidates = file_index.get(f"compact:{compact_key}", [])
        if compact_candidates:
            return compact_candidates[0]
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            stripped_candidates = file_index.get(stripped_key, [])
            if stripped_candidates:
                return stripped_candidates[0]
            stripped_compact = _compact_title_key(stripped_key)
            stripped_candidates = file_index.get(f"compact:{stripped_compact}", [])
            if stripped_candidates:
                return stripped_candidates[0]
    prefix_candidates: list[Path] = []
    prefix_key = _title_prefix_key(title_key)
    if prefix_key:
        prefix_candidates = file_index.get(prefix_key, [])
    if not prefix_candidates:
        stripped_key = _strip_leading_numeric_tokens(title_key)
        if stripped_key and stripped_key != title_key:
            prefix_key = _title_prefix_key(stripped_key)
            if prefix_key:
                prefix_candidates = file_index.get(prefix_key, [])
    if prefix_candidates:
        match = _adaptive_similarity_match(title_key, prefix_candidates)
        if match is not None:
            return match
    year = str(paper.get("_year") or "").strip()
    if not year.isdigit():
        return None
    author_key = ""
    authors = paper.get("_authors") or []
    if authors:
        author_key = _normalize_author_key(str(authors[0]))
    candidates = []
    if author_key:
        candidates = file_index.get(f"authoryear:{year}:{author_key}", [])
    if not candidates:
        candidates = file_index.get(f"year:{year}", [])
    if not candidates:
        return None
    if len(candidates) == 1 and not title_key:
        return candidates[0]
    match = _adaptive_similarity_match(title_key, candidates)
    if match is not None:
        return match
    return None


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
            suffix = path.suffix.lower()
            if suffix not in suffixes:
                name_lower = path.name.lower()
                if suffixes == {".pdf"} and ".pdf-" in name_lower and suffix != ".md":
                    pass
                else:
                    continue
            resolved = path.resolve()
            name_key = path.name.lower()
            index.setdefault(name_key, []).append(resolved)
            title_candidate = _extract_title_from_filename(path.name)
            title_key = _normalize_title_key(title_candidate)
            if title_key:
                if title_key != name_key:
                    index.setdefault(title_key, []).append(resolved)
                compact_key = _compact_title_key(title_key)
                if compact_key:
                    index.setdefault(f"compact:{compact_key}", []).append(resolved)
                prefix_key = _title_prefix_key(title_key)
                if prefix_key:
                    index.setdefault(prefix_key, []).append(resolved)
                stripped_key = _strip_leading_numeric_tokens(title_key)
                if stripped_key and stripped_key != title_key:
                    index.setdefault(stripped_key, []).append(resolved)
                    stripped_compact = _compact_title_key(stripped_key)
                    if stripped_compact:
                        index.setdefault(f"compact:{stripped_compact}", []).append(resolved)
                    stripped_prefix = _title_prefix_key(stripped_key)
                    if stripped_prefix:
                        index.setdefault(stripped_prefix, []).append(resolved)
            year_hint, author_hint = _extract_year_author_from_filename(path.name)
            if year_hint:
                index.setdefault(f"year:{year_hint}", []).append(resolved)
                if author_hint:
                    author_key = _normalize_author_key(author_hint)
                    if author_key:
                        index.setdefault(f"authoryear:{year_hint}:{author_key}", []).append(resolved)
    return index


def _build_file_index_from_paths(paths: list[Path], *, suffixes: set[str]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix not in suffixes:
            name_lower = path.name.lower()
            if suffixes == {".pdf"} and ".pdf-" in name_lower and suffix != ".md":
                pass
            else:
                continue
        resolved = path.resolve()
        name_key = path.name.lower()
        index.setdefault(name_key, []).append(resolved)
        title_candidate = _extract_title_from_filename(path.name)
        title_key = _normalize_title_key(title_candidate)
        if title_key:
            if title_key != name_key:
                index.setdefault(title_key, []).append(resolved)
            compact_key = _compact_title_key(title_key)
            if compact_key:
                index.setdefault(f"compact:{compact_key}", []).append(resolved)
            prefix_key = _title_prefix_key(title_key)
            if prefix_key:
                index.setdefault(prefix_key, []).append(resolved)
            stripped_key = _strip_leading_numeric_tokens(title_key)
            if stripped_key and stripped_key != title_key:
                index.setdefault(stripped_key, []).append(resolved)
                stripped_compact = _compact_title_key(stripped_key)
                if stripped_compact:
                    index.setdefault(f"compact:{stripped_compact}", []).append(resolved)
                stripped_prefix = _title_prefix_key(stripped_key)
                if stripped_prefix:
                    index.setdefault(stripped_prefix, []).append(resolved)
    return index


def _resolve_source_md(paper: dict[str, Any], md_index: dict[str, list[Path]]) -> Path | None:
    source_path = paper.get("source_path")
    if not source_path:
        source_path = ""
    if source_path:
        name = Path(str(source_path)).name.lower()
        candidates = md_index.get(name, [])
        if candidates:
            return candidates[0]
    return _resolve_by_title_and_meta(paper, md_index)


def _build_translated_index(roots: list[Path]) -> dict[str, dict[str, Path]]:
    index: dict[str, dict[str, Path]] = {}
    candidates: list[Path] = []
    for root in roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        try:
            candidates.extend(root.rglob("*.md"))
        except OSError:
            continue
    for path in sorted(candidates, key=lambda item: str(item)):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        name = path.name
        match = re.match(r"^(.+)\.([^.]+)\.md$", name, flags=re.IGNORECASE)
        if not match:
            continue
        base_name = match.group(1).strip()
        lang = match.group(2).strip()
        if not base_name or not lang:
            continue
        base_key = base_name.lower()
        lang_key = lang.lower()
        index.setdefault(base_key, {}).setdefault(lang_key, path.resolve())
    return index


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
    if name.lower().endswith(".pdf"):
        return [name]
    if name.lower().endswith(".pdf.md"):
        return [name[:-3]]
    return []


def _resolve_pdf(paper: dict[str, Any], pdf_index: dict[str, list[Path]]) -> Path | None:
    for filename in _guess_pdf_names(paper):
        candidates = pdf_index.get(filename.lower(), [])
        if candidates:
            return candidates[0]
    return _resolve_by_title_and_meta(paper, pdf_index)


def _ensure_under_roots(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


_BOOL_TRUE = {"1", "true", "yes", "with", "has"}
_BOOL_FALSE = {"0", "false", "no", "without"}


def _tokenize_filter_query(text: str) -> list[str]:
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


def _normalize_presence_value(value: str) -> str | None:
    token = value.strip().lower()
    if token in _BOOL_TRUE:
        return "with"
    if token in _BOOL_FALSE:
        return "without"
    return None


def _parse_filter_query(text: str) -> dict[str, set[str]]:
    parsed = {
        "pdf": set(),
        "source": set(),
        "summary": set(),
        "translated": set(),
        "template": set(),
    }
    for token in _tokenize_filter_query(text):
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
                normalized = _normalize_presence_value(part)
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


def _presence_filter(values: list[str]) -> set[str] | None:
    normalized = set()
    for value in values:
        token = _normalize_presence_value(value)
        if token:
            normalized.add(token)
    if not normalized or normalized == {"with", "without"}:
        return None
    return normalized


def _merge_filter_set(primary: set[str] | None, secondary: set[str] | None) -> set[str] | None:
    if not primary:
        return secondary
    if not secondary:
        return primary
    return primary & secondary


def _matches_presence(allowed: set[str] | None, has_value: bool) -> bool:
    if not allowed:
        return True
    if has_value and "with" in allowed:
        return True
    if not has_value and "without" in allowed:
        return True
    return False


def _template_tag_map(index: PaperIndex) -> dict[str, str]:
    return {tag.lower(): tag for tag in index.template_tags}


def _compute_counts(index: PaperIndex, ids: set[int]) -> dict[str, Any]:
    template_order = list(index.template_tags)
    template_counts = {tag: 0 for tag in template_order}
    pdf_count = 0
    source_count = 0
    summary_count = 0
    translated_count = 0
    total_count = 0
    tag_map = _template_tag_map(index)

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


def _page_shell(
    title: str,
    body_html: str,
    extra_head: str = "",
    extra_scripts: str = "",
    header_title: str | None = None,
) -> str:
    header_html = """
    <header>
      <a href="/">Papers</a>
      <a href="/stats">Stats</a>
    </header>
"""
    if header_title:
        safe_title = html.escape(header_title)
        header_html = f"""
    <header class="detail-header">
      <div class="header-row">
        <a class="header-back" href="/">← Papers</a>
        <span class="header-title" title="{safe_title}">{safe_title}</span>
        <a class="header-link" href="/stats">Stats</a>
      </div>
    </header>
"""
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
      .detail-header .header-row {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; }}
      .detail-header .header-title {{ text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
      .detail-header .header-back {{ margin-right: 0; }}
      .detail-header .header-link {{ margin-right: 0; }}
      .container {{ max-width: 1100px; margin: 0 auto; padding: 16px; }}
      .filters {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin: 12px 0 16px; }}
      .filters input {{ width: 100%; padding: 8px; border: 1px solid #d0d7de; border-radius: 6px; }}
      .filters select {{ width: 100%; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; font-size: 13px; }}
      .filters select:not([multiple]) {{ padding: 6px 8px; }}
      .filters select[multiple] {{ padding: 2px; line-height: 1.25; min-height: 72px; font-size: 13px; }}
      .filters select[multiple] option {{ padding: 2px 6px; line-height: 1.25; }}
      .filters label {{ font-size: 12px; color: #57606a; }}
      .filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
      .card {{ border: 1px solid #d0d7de; border-radius: 10px; padding: 12px; margin: 10px 0; }}
      .muted {{ color: #57606a; font-size: 13px; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid #d0d7de; margin-right: 6px; font-size: 12px; }}
      .pill.template {{ border-color: #8a92a5; color: #243b53; background: #f6f8fa; }}
      .pill.pdf-only {{ border-color: #c8a951; background: #fff8dc; color: #5b4a00; }}
      .warning {{ background: #fff4ce; border: 1px solid #ffd089; padding: 10px; border-radius: 10px; margin: 12px 0; }}
      .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
      .tab {{ display: inline-block; padding: 6px 12px; border-radius: 999px; border: 1px solid #d0d7de; background: #f6f8fa; color: #0969da; text-decoration: none; font-size: 13px; }}
      .tab:hover {{ background: #eef1f4; }}
      .tab.active {{ background: #0969da; border-color: #0969da; color: #fff; }}
      .detail-shell {{ display: flex; flex-direction: column; gap: 12px; min-height: calc(100vh - 120px); }}
      .detail-toolbar {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-start; gap: 12px; padding: 6px 8px 10px; border-bottom: 1px solid #e5e7eb; box-sizing: border-box; }}
      .detail-toolbar .tabs {{ margin: 0; }}
      .toolbar-actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-left: auto; padding-right: 16px; }}
      .split-inline {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
      .split-inline select {{ padding: 6px 8px; border-radius: 8px; border: 1px solid #d0d7de; background: #fff; min-width: 140px; }}
      .split-actions {{ display: flex; align-items: center; justify-content: center; gap: 8px; }}
      .split-actions button {{ padding: 6px 10px; border-radius: 999px; border: 1px solid #d0d7de; background: #f6f8fa; cursor: pointer; min-width: 36px; }}
      .lang-select {{ display: flex; align-items: center; gap: 6px; }}
      .lang-select label {{ color: #57606a; font-size: 13px; }}
      .lang-select select {{ padding: 6px 8px; border-radius: 8px; border: 1px solid #d0d7de; background: #fff; min-width: 120px; }}
      .fullscreen-actions {{ display: flex; align-items: center; gap: 6px; }}
      .fullscreen-actions button {{ padding: 6px 10px; border-radius: 8px; border: 1px solid #d0d7de; background: #f6f8fa; cursor: pointer; }}
      .fullscreen-exit {{ display: none; }}
      body.detail-fullscreen {{ overflow: hidden; --outline-top: 16px; }}
      body.detail-fullscreen header {{ display: none; }}
      body.detail-fullscreen .container {{ max-width: 100%; padding: 0; }}
      body.detail-fullscreen .detail-shell {{
        position: fixed;
        inset: 0;
        padding: 12px 16px;
        background: #fff;
        z-index: 40;
        overflow: auto;
      }}
      body.detail-fullscreen .detail-toolbar {{ position: sticky; top: 0; background: #fff; z-index: 41; }}
      body.detail-fullscreen .fullscreen-enter {{ display: none; }}
      body.detail-fullscreen .fullscreen-exit {{ display: inline-flex; }}
      .detail-body {{ display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }}
      .help-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border-radius: 50%; border: 1px solid #d0d7de; color: #57606a; font-size: 12px; cursor: default; position: relative; }}
      .help-icon::after {{ content: attr(data-tip); display: none; position: absolute; top: 24px; right: 0; background: #0b1220; color: #e6edf3; padding: 8px 10px; border-radius: 8px; font-size: 12px; white-space: pre-line; width: 260px; z-index: 20; }}
      .help-icon:hover::after {{ display: block; }}
      .stats {{ margin: 12px 0 6px; }}
      .stats-row {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
      .stats-label {{ font-weight: 600; color: #0b1220; margin-right: 4px; }}
      .pill.stat {{ background: #f6f8fa; border-color: #c7d2e0; color: #1f2a37; }}
      pre {{ overflow: auto; padding: 10px; background: #0b1220; color: #e6edf3; border-radius: 10px; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      a {{ color: #0969da; }}
      @media (max-width: 768px) {{
        .detail-toolbar {{
          flex-wrap: nowrap;
          overflow-x: auto;
          padding-bottom: 8px;
        }}
        .detail-toolbar::-webkit-scrollbar {{ height: 6px; }}
        .detail-toolbar::-webkit-scrollbar-thumb {{ background: #c7d2e0; border-radius: 999px; }}
        .detail-toolbar .tabs,
        .toolbar-actions {{
          flex: 0 0 auto;
        }}
      }}
    </style>
    {extra_head}
  </head>
  <body>
    {header_html}
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


def _outline_assets(outline_top: str) -> tuple[str, str, str]:
    outline_html = """
<button id="outlineToggle" class="outline-toggle" title="Toggle outline">☰</button>
<div id="outlinePanel" class="outline-panel collapsed">
  <div class="outline-title">Outline</div>
  <div id="outlineList" class="outline-list"></div>
</div>
<button id="backToTop" class="back-to-top" title="Back to top">↑</button>
"""
    outline_css = f"""
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
    outline_js = """
const outlineToggle = document.getElementById('outlineToggle');
const outlinePanel = document.getElementById('outlinePanel');
const outlineList = document.getElementById('outlineList');
const backToTop = document.getElementById('backToTop');

function slugify(text) {
  return text.toLowerCase().trim()
    .replace(/[^a-z0-9\\s-]/g, '')
    .replace(/\\s+/g, '-')
    .replace(/-+/g, '-');
}

function buildOutline() {
  if (!outlineList) return;
  const content = document.getElementById('content');
  if (!content) return;
  const headings = content.querySelectorAll('h1, h2, h3, h4');
  if (!headings.length) {
    outlineList.innerHTML = '<div class="muted">No headings</div>';
    return;
  }
  const used = new Set();
  outlineList.innerHTML = '';
  headings.forEach((heading) => {
    let id = heading.id;
    if (!id) {
      const base = slugify(heading.textContent || 'section') || 'section';
      id = base;
      let i = 1;
      while (used.has(id) || document.getElementById(id)) {
        id = `${base}-${i++}`;
      }
      heading.id = id;
    }
    used.add(id);
    const level = parseInt(heading.tagName.slice(1), 10) || 1;
    const link = document.createElement('a');
    link.href = `#${id}`;
    link.textContent = heading.textContent || '';
    link.style.paddingLeft = `${(level - 1) * 12}px`;
    outlineList.appendChild(link);
  });
}

function toggleBackToTop() {
  if (!backToTop) return;
  if (window.scrollY > 300) {
    backToTop.classList.add('visible');
  } else {
    backToTop.classList.remove('visible');
  }
}

if (outlineToggle && outlinePanel) {
  outlineToggle.addEventListener('click', () => {
    outlinePanel.classList.toggle('collapsed');
  });
}

if (backToTop) {
  backToTop.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}

buildOutline();
window.addEventListener('scroll', toggleBackToTop);
toggleBackToTop();
"""
    return outline_html, outline_css, outline_js


async def _index_page(request: Request) -> HTMLResponse:
    index: PaperIndex = request.app.state.index
    template_options = "".join(
        f'<option value="{html.escape(tag)}">{html.escape(tag)}</option>'
        for tag in index.template_tags
    )
    if not template_options:
        template_options = '<option value="" disabled>(no templates)</option>'
    filter_help = (
        "Filters syntax:\\n"
        "pdf:yes|no source:yes|no translated:yes|no summary:yes|no\\n"
        "tmpl:<tag> or template:<tag>\\n"
        "has:pdf / no:source aliases\\n"
        "Content tags still use the search box (tag:fpga)."
    )
    filter_help_attr = html.escape(filter_help).replace("\n", "&#10;")
    body_html = """
<h2>Paper Database</h2>
<div class="card">
  <div class="muted">Search (Scholar-style): <code>tag:fpga year:2023..2025 -survey</code> · Use quotes for phrases and <code>OR</code> for alternatives.</div>
  <div style="display:flex; gap:8px; margin-top:8px;">
    <input id="query" placeholder='Search... e.g. title:"nearest neighbor" tag:fpga year:2023..2025' style="flex:1; padding:10px; border:1px solid #d0d7de; border-radius:8px;" />
    <select id="openView" style="padding:10px; border:1px solid #d0d7de; border-radius:8px;">
      <option value="summary" selected>Open: Summary</option>
      <option value="source">Open: Source</option>
      <option value="translated">Open: Translated</option>
      <option value="pdf">Open: PDF</option>
      <option value="pdfjs">Open: PDF Viewer</option>
      <option value="split">Open: Split</option>
    </select>
  </div>
  <div class="filters" style="grid-template-columns: repeat(5, 1fr); margin-top:10px;">
    <div class="filter-group">
      <label>PDF</label>
      <select id="filterPdf" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Source</label>
      <select id="filterSource" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Translated</label>
      <select id="filterTranslated" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Summary</label>
      <select id="filterSummary" multiple size="2">
        <option value="with">With</option>
        <option value="without">Without</option>
      </select>
    </div>
    <div class="filter-group">
      <label>Template</label>
      <select id="filterTemplate" multiple size="4">
        __TEMPLATE_OPTIONS__
      </select>
    </div>
  </div>
  <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">
    <input id="filterQuery" placeholder='Filters... e.g. pdf:yes tmpl:simple' style="flex:1; padding:10px; border:1px solid #d0d7de; border-radius:8px;" />
    <span class="help-icon" data-tip="__FILTER_HELP__">?</span>
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
<div id="stats" class="stats">
  <div id="statsTotal" class="stats-row"></div>
  <div id="statsFiltered" class="stats-row" style="margin-top:6px;"></div>
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
  const fq = document.getElementById("filterQuery").value.trim();
  if (fq) params.set("fq", fq);
  function addMulti(id, key) {
    const el = document.getElementById(id);
    const values = Array.from(el.selectedOptions).map(opt => opt.value).filter(Boolean);
    for (const value of values) {
      params.append(key, value);
    }
  }
  addMulti("filterPdf", "pdf");
  addMulti("filterSource", "source");
  addMulti("filterTranslated", "translated");
  addMulti("filterSummary", "summary");
  addMulti("filterTemplate", "template");
  return params;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function viewSuffixForItem(item) {
  let view = document.getElementById("openView").value;
  const isPdfOnly = item.is_pdf_only;
  const pdfFallback = item.has_pdf ? "pdfjs" : "pdf";
  if (isPdfOnly && (view === "summary" || view === "source" || view === "translated")) {
    view = pdfFallback;
  }
  if (!view || view === "summary") return "";
  const params = new URLSearchParams();
  params.set("view", view);
  if (view === "split") {
    if (isPdfOnly) {
      params.set("left", pdfFallback);
      params.set("right", pdfFallback);
    } else {
      params.set("left", "summary");
      if (item.has_pdf) {
        params.set("right", "pdfjs");
      } else if (item.has_source) {
        params.set("right", "source");
      } else {
        params.set("right", "summary");
      }
    }
  }
  return `?${params.toString()}`;
}

function renderItem(item) {
  const tags = (item.tags || []).map(t => `<span class="pill">${escapeHtml(t)}</span>`).join("");
  const templateTags = (item.template_tags || []).map(t => `<span class="pill template">tmpl:${escapeHtml(t)}</span>`).join("");
  const authors = (item.authors || []).slice(0, 6).map(a => escapeHtml(a)).join(", ");
  const meta = `${escapeHtml(item.year || "")}-${escapeHtml(item.month || "")} · ${escapeHtml(item.venue || "")}`;
  const viewSuffix = viewSuffixForItem(item);
  const badges = [
    item.has_source ? `<span class="pill">source</span>` : "",
    item.has_translation ? `<span class="pill">translated</span>` : "",
    item.has_pdf ? `<span class="pill">pdf</span>` : "",
    item.is_pdf_only ? `<span class="pill pdf-only">pdf-only</span>` : "",
  ].join("");
  return `
    <div class="card">
      <div><a href="/paper/${encodeURIComponent(item.source_hash)}${viewSuffix}">${escapeHtml(item.title || "")}</a></div>
      <div class="muted">${authors}</div>
      <div class="muted">${meta}</div>
      <div style="margin-top:6px">${badges} ${templateTags} ${tags}</div>
    </div>
  `;
}

function renderStatsRow(targetId, label, counts) {
  const row = document.getElementById(targetId);
  if (!row || !counts) return;
  const pills = [];
  pills.push(`<span class="stats-label">${escapeHtml(label)}</span>`);
  pills.push(`<span class="pill stat">Count ${counts.total}</span>`);
  pills.push(`<span class="pill stat">PDF ${counts.pdf}</span>`);
  pills.push(`<span class="pill stat">Source ${counts.source}</span>`);
  pills.push(`<span class="pill stat">Translated ${counts.translated || 0}</span>`);
  pills.push(`<span class="pill stat">Summary ${counts.summary}</span>`);
  const order = counts.template_order || Object.keys(counts.templates || {});
  for (const tag of order) {
    const count = (counts.templates && counts.templates[tag]) || 0;
    pills.push(`<span class="pill stat">tmpl:${escapeHtml(tag)} ${count}</span>`);
  }
  row.innerHTML = pills.join("");
}

function updateStats(stats) {
  if (!stats) return;
  renderStatsRow("statsTotal", "Total", stats.all);
  renderStatsRow("statsFiltered", "Filtered", stats.filtered);
}

async function loadMore() {
  if (loading || done) return;
  loading = true;
  document.getElementById("loading").textContent = "Loading...";
  const res = await fetch(`/api/papers?${currentParams(page).toString()}`);
  const data = await res.json();
  if (data.stats) {
    updateStats(data.stats);
  }
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
document.getElementById("filterQuery").addEventListener("change", resetAndLoad);
document.getElementById("filterPdf").addEventListener("change", resetAndLoad);
document.getElementById("filterSource").addEventListener("change", resetAndLoad);
document.getElementById("filterTranslated").addEventListener("change", resetAndLoad);
document.getElementById("filterSummary").addEventListener("change", resetAndLoad);
document.getElementById("filterTemplate").addEventListener("change", resetAndLoad);

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
"""
    body_html = body_html.replace("__TEMPLATE_OPTIONS__", template_options)
    body_html = body_html.replace("__FILTER_HELP__", filter_help_attr)
    return HTMLResponse(_page_shell("Paper DB", body_html))


def _parse_filters(request: Request) -> dict[str, list[str] | str | int]:
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
    }


async def _api_papers(request: Request) -> JSONResponse:
    index: PaperIndex = request.app.state.index
    filters = _parse_filters(request)
    page = int(filters["page"])
    page_size = int(filters["page_size"])
    q = str(filters["q"])
    filter_query = str(filters["filter_query"])
    query = parse_query(q)
    candidate = _apply_query(index, query)
    filter_terms = _parse_filter_query(filter_query)
    pdf_filter = _merge_filter_set(_presence_filter(filters["pdf"]), _presence_filter(list(filter_terms["pdf"])))
    source_filter = _merge_filter_set(
        _presence_filter(filters["source"]), _presence_filter(list(filter_terms["source"]))
    )
    summary_filter = _merge_filter_set(
        _presence_filter(filters["summary"]), _presence_filter(list(filter_terms["summary"]))
    )
    translated_filter = _merge_filter_set(
        _presence_filter(filters["translated"]), _presence_filter(list(filter_terms["translated"]))
    )
    template_selected = {item.lower() for item in filters["template"] if item}
    template_filter = _merge_filter_set(
        template_selected or None,
        filter_terms["template"] or None,
    )

    if candidate:
        filtered: set[int] = set()
        for idx in candidate:
            paper = index.papers[idx]
            source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))
            has_source = source_hash in index.md_path_by_hash
            has_pdf = source_hash in index.pdf_path_by_hash
            has_summary = bool(paper.get("_has_summary"))
            has_translated = bool(index.translated_md_by_hash.get(source_hash))
            if not _matches_presence(pdf_filter, has_pdf):
                continue
            if not _matches_presence(source_filter, has_source):
                continue
            if not _matches_presence(summary_filter, has_summary):
                continue
            if not _matches_presence(translated_filter, has_translated):
                continue
            if template_filter:
                tags = paper.get("_template_tags_lc") or []
                if not any(tag in template_filter for tag in tags):
                    continue
            filtered.add(idx)
        candidate = filtered
    ordered = [idx for idx in index.ordered_ids if idx in candidate]
    total = len(ordered)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_ids = ordered[start:end]
    stats_payload = None
    if page == 1:
        all_ids = set(index.ordered_ids)
        stats_payload = {
            "all": _compute_counts(index, all_ids),
            "filtered": _compute_counts(index, candidate),
        }

    items: list[dict[str, Any]] = []
    for idx in page_ids:
        paper = index.papers[idx]
        source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))
        translations = index.translated_md_by_hash.get(source_hash, {})
        translation_languages = sorted(translations.keys(), key=str.lower)
        items.append(
            {
                "source_hash": source_hash,
                "title": paper.get("paper_title") or "",
                "authors": paper.get("_authors") or [],
                "year": paper.get("_year") or "",
                "month": paper.get("_month") or "",
                "venue": paper.get("_venue") or "",
                "tags": paper.get("_tags") or [],
                "template_tags": paper.get("_template_tags") or [],
                "has_source": source_hash in index.md_path_by_hash,
                "has_translation": bool(translation_languages),
                "has_pdf": source_hash in index.pdf_path_by_hash,
                "has_summary": bool(paper.get("_has_summary")),
                "is_pdf_only": bool(paper.get("_is_pdf_only")),
                "translation_languages": translation_languages,
            }
        )

    return JSONResponse(
        {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": end < total,
            "items": items,
            "stats": stats_payload,
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
    is_pdf_only = bool(paper.get("_is_pdf_only"))
    page_title = str(paper.get("paper_title") or "Paper")
    view = request.query_params.get("view")
    template_param = request.query_params.get("template")
    embed = request.query_params.get("embed") == "1"

    pdf_path = index.pdf_path_by_hash.get(source_hash)
    pdf_url = f"/api/pdf/{source_hash}"
    source_available = source_hash in index.md_path_by_hash
    translations = index.translated_md_by_hash.get(source_hash, {})
    translation_langs = sorted(translations.keys(), key=str.lower)
    lang_param = request.query_params.get("lang")
    normalized_lang = lang_param.lower() if lang_param else None
    selected_lang = None
    if translation_langs:
        if normalized_lang and normalized_lang in translations:
            selected_lang = normalized_lang
        elif "zh" in translations:
            selected_lang = "zh"
        else:
            selected_lang = translation_langs[0]
    allowed_views = {"summary", "source", "translated", "pdf", "pdfjs", "split"}
    if is_pdf_only:
        allowed_views = {"pdf", "pdfjs", "split"}

    def normalize_view(value: str | None, default: str) -> str:
        if value in allowed_views:
            return value
        return default

    preferred_pdf_view = "pdfjs" if pdf_path else "pdf"
    default_view = preferred_pdf_view if is_pdf_only else "summary"
    view = normalize_view(view, default_view)
    if view == "split":
        embed = False
    if is_pdf_only:
        left_param = request.query_params.get("left")
        right_param = request.query_params.get("right")
        left = normalize_view(left_param, preferred_pdf_view) if left_param else preferred_pdf_view
        right = normalize_view(right_param, preferred_pdf_view) if right_param else preferred_pdf_view
    else:
        default_right = "pdfjs" if pdf_path else ("source" if source_available else "summary")
        left_param = request.query_params.get("left")
        right_param = request.query_params.get("right")
        left = normalize_view(left_param, "summary") if left_param else "summary"
        right = normalize_view(right_param, default_right) if right_param else default_right

    def render_page(title: str, body: str, extra_head: str = "", extra_scripts: str = "") -> HTMLResponse:
        if embed:
            return HTMLResponse(_embed_shell(title, body, extra_head, extra_scripts))
        return HTMLResponse(_page_shell(title, body, extra_head, extra_scripts, header_title=page_title))

    def nav_link(label: str, v: str) -> str:
        active = " active" if view == v else ""
        params: dict[str, str] = {"view": v}
        if v == "summary" and template_param:
            params["template"] = str(template_param)
        if v == "translated" and selected_lang:
            params["lang"] = selected_lang
        if v == "split":
            params["left"] = left
            params["right"] = right
        href = f"/paper/{source_hash}?{urlencode(params)}"
        return f'<a class="tab{active}" href="{html.escape(href)}">{html.escape(label)}</a>'

    tab_defs = [
        ("Summary", "summary"),
        ("Source", "source"),
        ("Translated", "translated"),
        ("PDF", "pdf"),
        ("PDF Viewer", "pdfjs"),
        ("Split", "split"),
    ]
    if is_pdf_only:
        tab_defs = [
            ("PDF", "pdf"),
            ("PDF Viewer", "pdfjs"),
            ("Split", "split"),
        ]
    tabs_html = '<div class="tabs">' + "".join(nav_link(label, v) for label, v in tab_defs) + "</div>"
    fullscreen_controls = """
<div class="fullscreen-actions">
  <button id="fullscreenEnter" class="fullscreen-enter" type="button" title="Enter fullscreen">Fullscreen</button>
  <button id="fullscreenExit" class="fullscreen-exit" type="button" title="Exit fullscreen">Exit Fullscreen</button>
</div>
"""

    def detail_toolbar(extra_controls: str = "") -> str:
        if embed:
            return ""
        controls = extra_controls.strip()
        toolbar_controls = f"{controls}{fullscreen_controls}" if controls else fullscreen_controls
        return f"""
<div class="detail-toolbar">
  {tabs_html}
  <div class="toolbar-actions">
    {toolbar_controls}
  </div>
</div>
"""

    def wrap_detail(content: str, toolbar_html: str | None = None) -> str:
        if embed:
            return content
        toolbar = detail_toolbar() if toolbar_html is None else toolbar_html
        return f"""
<div class="detail-shell">
  {toolbar}
  <div class="detail-body">
    {content}
  </div>
</div>
"""

    fullscreen_script = ""
    if not embed:
        fullscreen_script = """
<script>
const fullscreenEnter = document.getElementById('fullscreenEnter');
const fullscreenExit = document.getElementById('fullscreenExit');
function setFullscreen(enable) {
  document.body.classList.toggle('detail-fullscreen', enable);
}
if (fullscreenEnter) {
  fullscreenEnter.addEventListener('click', () => setFullscreen(true));
}
if (fullscreenExit) {
  fullscreenExit.addEventListener('click', () => setFullscreen(false));
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && document.body.classList.contains('detail-fullscreen')) {
    setFullscreen(false);
  }
});
</script>
"""
    pdf_only_warning_html = ""
    if is_pdf_only:
        pdf_only_warning_html = (
            '<div class="warning">PDF-only entry: summary and source views are unavailable.</div>'
        )
    outline_top = "72px" if not embed else "16px"
    outline_html, outline_css, outline_js = _outline_assets(outline_top)

    if view == "split":
        def pane_src(pane_view: str) -> str:
            if pane_view == "pdfjs" and pdf_path:
                return _build_pdfjs_viewer_url(pdf_url)
            params: dict[str, str] = {"view": pane_view, "embed": "1"}
            if pane_view == "summary" and template_param:
                params["template"] = str(template_param)
            if pane_view == "translated" and selected_lang:
                params["lang"] = selected_lang
            return f"/paper/{source_hash}?{urlencode(params)}"

        left_src = pane_src(left)
        right_src = pane_src(right)
        options = [
            ("summary", "Summary"),
            ("source", "Source"),
            ("translated", "Translated"),
            ("pdf", "PDF"),
            ("pdfjs", "PDF Viewer"),
        ]
        if is_pdf_only:
            options = [
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
        split_controls = f"""
<div class="split-inline">
  <span class="muted">Left</span>
  <select id="splitLeft">
    {left_options}
  </select>
  <div class="split-actions">
    <button id="splitTighten" type="button" title="Tighten width">-</button>
    <button id="splitSwap" type="button" title="Swap panes">⇄</button>
    <button id="splitWiden" type="button" title="Widen width">+</button>
  </div>
  <span class="muted">Right</span>
  <select id="splitRight">
    {right_options}
  </select>
</div>
"""
        toolbar_html = detail_toolbar(split_controls)
        split_layout = f"""
{pdf_only_warning_html}
<div class="split-layout">
  <div class="split-pane">
    <iframe id="leftPane" src="{html.escape(left_src)}" title="Left pane"></iframe>
  </div>
  <div class="split-pane">
    <iframe id="rightPane" src="{html.escape(right_src)}" title="Right pane"></iframe>
  </div>
</div>
"""
        body = wrap_detail(split_layout, toolbar_html=toolbar_html)
        extra_head = """
<style>
.container {
  max-width: 100%;
  width: 100%;
  margin: 0 auto;
}
.split-layout {
  display: flex;
  gap: 12px;
  width: 100%;
  max-width: var(--split-max-width, 100%);
  margin: 0 auto;
  flex: 1;
  min-height: 440px;
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
    min-height: 0;
  }
  .split-pane {
    height: 70vh;
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
const widthSteps = ["1200px", "1400px", "1600px", "1800px", "2000px", "100%"];
let widthIndex = widthSteps.length - 1;
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
  const value = widthSteps[widthIndex];
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
        return render_page(
            "Split View",
            body,
            extra_head=extra_head,
            extra_scripts=extra_scripts + fullscreen_script,
        )

    if view == "translated":
        if translation_langs:
            lang_options = "\n".join(
                f'<option value="{html.escape(lang)}"{" selected" if lang == selected_lang else ""}>'
                f'{html.escape(lang)}</option>'
                for lang in translation_langs
            )
            disabled_attr = ""
        else:
            lang_options = '<option value="" selected>(no translations)</option>'
            disabled_attr = " disabled"
        lang_controls = f"""
<div class="lang-select">
  <label for="translationLang">Language</label>
  <select id="translationLang"{disabled_attr}>
    {lang_options}
  </select>
</div>
"""
        toolbar_html = detail_toolbar(lang_controls)
        if not translation_langs or not selected_lang:
            body = wrap_detail(
                '<div class="warning">No translated markdown found. '
                'Provide <code>--md-translated-root</code> and place '
                '<code>&lt;base&gt;.&lt;lang&gt;.md</code> under that root.</div>',
                toolbar_html=toolbar_html,
            )
            return render_page("Translated", body, extra_scripts=fullscreen_script)
        translated_path = translations.get(selected_lang)
        if not translated_path:
            body = wrap_detail(
                '<div class="warning">Translated markdown not found for the selected language.</div>',
                toolbar_html=toolbar_html,
            )
            return render_page("Translated", body, extra_scripts=fullscreen_script)
        try:
            raw = translated_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = translated_path.read_text(encoding="latin-1")
        raw = _normalize_markdown_images(raw)
        rendered = _render_markdown_with_math_placeholders(md, raw)
        body = wrap_detail(
            f"""
<div class="muted">Language: {html.escape(selected_lang)}</div>
<div class="muted">{html.escape(str(translated_path))}</div>
<div class="muted" style="margin-top:10px;">Rendered from translated markdown:</div>
{outline_html}
<div id="content">{rendered}</div>
<details style="margin-top:12px;"><summary>Raw markdown</summary>
  <pre><code>{html.escape(raw)}</code></pre>
</details>
""",
            toolbar_html=toolbar_html,
        )
        extra_head = f"""
<link rel="stylesheet" href="{_CDN_KATEX}" />
{outline_css}
<style>
#content img {{
  max-width: 100%;
  height: auto;
}}
</style>
"""
        extra_scripts = f"""
<script src="{_CDN_MERMAID}"></script>
<script src="{_CDN_KATEX_JS}"></script>
<script src="{_CDN_KATEX_AUTO}"></script>
<script>
const translationSelect = document.getElementById('translationLang');
if (translationSelect) {{
  translationSelect.addEventListener('change', () => {{
    const params = new URLSearchParams(window.location.search);
    params.set('view', 'translated');
    params.set('lang', translationSelect.value);
    window.location.search = params.toString();
  }});
}}
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
{outline_js}
</script>
"""
        return render_page(
            "Translated",
            body,
            extra_head=extra_head,
            extra_scripts=extra_scripts + fullscreen_script,
        )

    if view == "source":
        source_path = index.md_path_by_hash.get(source_hash)
        if not source_path:
            body = wrap_detail(
                '<div class="warning">Source markdown not found. Provide --md-root to enable source viewing.</div>'
            )
            return render_page("Source", body, extra_scripts=fullscreen_script)
        try:
            raw = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = source_path.read_text(encoding="latin-1")
        rendered = _render_markdown_with_math_placeholders(md, raw)
        body = wrap_detail(
            f"""
<div class="muted">{html.escape(str(source_path))}</div>
<div class="muted" style="margin-top:10px;">Rendered from source markdown:</div>
{outline_html}
<div id="content">{rendered}</div>
<details style="margin-top:12px;"><summary>Raw markdown</summary>
  <pre><code>{html.escape(raw)}</code></pre>
</details>
"""
        )
        extra_head = f"""
<link rel="stylesheet" href="{_CDN_KATEX}" />
{outline_css}
<style>
#content img {{
  max-width: 100%;
  height: auto;
}}
</style>
"""
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
{outline_js}
</script>
"""
        return render_page("Source", body, extra_head=extra_head, extra_scripts=extra_scripts + fullscreen_script)

    if view == "pdf":
        if not pdf_path:
            body = wrap_detail('<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>')
            return render_page("PDF", body, extra_scripts=fullscreen_script)
        body = wrap_detail(
            f"""
{pdf_only_warning_html}
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
        )
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
        return render_page("PDF", body, extra_scripts=extra_scripts + fullscreen_script)

    if view == "pdfjs":
        if not pdf_path:
            body = wrap_detail('<div class="warning">PDF not found. Provide --pdf-root to enable PDF viewing.</div>')
            return render_page("PDF Viewer", body, extra_scripts=fullscreen_script)
        viewer_url = _build_pdfjs_viewer_url(pdf_url)
        frame_height = "calc(100vh - 32px)" if embed else "100%"
        body = wrap_detail(
            f"""
{pdf_only_warning_html}
<div class="muted">{html.escape(str(pdf_path.name))}</div>
<iframe class="pdfjs-frame" src="{html.escape(viewer_url)}" title="PDF.js Viewer"></iframe>
"""
        )
        extra_head = f"""
<style>
.pdfjs-frame {{
  width: 100%;
  height: {frame_height};
  border: 1px solid #d0d7de;
  border-radius: 10px;
  flex: 1;
}}
</style>
"""
        return render_page("PDF Viewer", body, extra_head=extra_head, extra_scripts=fullscreen_script)

    selected_tag, available_templates = _select_template_tag(paper, template_param)
    markdown, template_name, warning = _render_paper_markdown(
        paper,
        request.app.state.fallback_language,
        template_tag=selected_tag,
    )
    rendered_html = _render_markdown_with_math_placeholders(md, markdown)

    warning_html = f'<div class="warning">{html.escape(warning)}</div>' if warning else ""
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
    content_html = f"""
{template_controls}
{warning_html}
{outline_html}
<div id="content">{rendered_html}</div>
"""
    body = wrap_detail(content_html)

    extra_head = f"""
<link rel="stylesheet" href="{_CDN_KATEX}" />
{outline_css}
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
{outline_js}
</script>
"""
    return render_page(page_title, body, extra_head=extra_head, extra_scripts=extra_scripts + fullscreen_script)


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
<div id="keywords" style="width:100%;height:420px"></div>
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
  bar('keywords', 'Top Keywords', (data.keywords || []).slice(0, 20));
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
    md_translated_roots: list[Path] | None = None,
    pdf_roots: list[Path] | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
) -> Starlette:
    papers = _load_or_merge_papers(db_paths, bibtex_path, cache_dir, use_cache, pdf_roots=pdf_roots)

    md_roots = md_roots or []
    md_translated_roots = md_translated_roots or []
    pdf_roots = pdf_roots or []
    index = build_index(
        papers,
        md_roots=md_roots,
        md_translated_roots=md_translated_roots,
        pdf_roots=pdf_roots,
    )
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
    elif pdf_roots:
        logger.warning(
            "PDF.js viewer assets not found at %s; PDF Viewer mode will be unavailable.",
            _PDFJS_STATIC_DIR,
        )
    app = Starlette(routes=routes)
    app.state.index = index
    app.state.md = md
    app.state.fallback_language = fallback_language
    app.state.pdf_roots = pdf_roots
    return app
