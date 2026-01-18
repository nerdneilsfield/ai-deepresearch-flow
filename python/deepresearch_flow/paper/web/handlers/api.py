"""API route handlers for paper web UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from deepresearch_flow.paper.db_ops import PaperIndex
from deepresearch_flow.paper.utils import stable_hash
from deepresearch_flow.paper.web.filters import (
    compute_counts,
    matches_presence,
    merge_filter_set,
    parse_filters,
    parse_filter_query,
    presence_filter,
    sorted_ids,
)
from deepresearch_flow.paper.web.markdown import normalize_markdown_images
from deepresearch_flow.paper.web.static_assets import resolve_asset_urls
from deepresearch_flow.paper.web.text import extract_summary_snippet, normalize_title, normalize_venue
from deepresearch_flow.paper.web.query import Query, QueryTerm, parse_query


def _ensure_under_roots(path: Path, roots: list[Path]) -> bool:
    """Check if path is under one of the allowed root directories."""
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


def _apply_query(index: PaperIndex, query: Query) -> set[int]:
    """Apply a search query to the paper index and return matching IDs."""
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


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


async def api_papers(request: Request) -> JSONResponse:
    """API endpoint for paper list with filtering, sorting, and pagination."""
    index: PaperIndex = request.app.state.index
    asset_config = request.app.state.asset_config
    prefer_local = request.app.state.static_mode == "dev"
    filters = parse_filters(request)
    page = int(filters["page"])
    page_size = int(filters["page_size"])
    q = str(filters["q"])
    filter_query = str(filters["filter_query"])
    sort_by = str(filters["sort_by"]).strip().lower()
    sort_dir = str(filters["sort_dir"]).strip().lower()
    if sort_by not in {"year", "title", "venue", "author"}:
        sort_by = ""
    query = parse_query(q)
    candidate = _apply_query(index, query)
    filter_terms = parse_filter_query(filter_query)
    pdf_filter = merge_filter_set(presence_filter(filters["pdf"]), presence_filter(list(filter_terms["pdf"])))
    source_filter = merge_filter_set(
        presence_filter(filters["source"]), presence_filter(list(filter_terms["source"]))
    )
    summary_filter = merge_filter_set(
        presence_filter(filters["summary"]), presence_filter(list(filter_terms["summary"]))
    )
    translated_filter = merge_filter_set(
        presence_filter(filters["translated"]), presence_filter(list(filter_terms["translated"]))
    )
    template_selected = {item.lower() for item in filters["template"] if item}
    template_filter = merge_filter_set(
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
            if not matches_presence(pdf_filter, has_pdf):
                continue
            if not matches_presence(source_filter, has_source):
                continue
            if not matches_presence(summary_filter, has_summary):
                continue
            if not matches_presence(translated_filter, has_translated):
                continue
            if template_filter:
                tags = paper.get("_template_tags_lc") or []
                if not any(tag in template_filter for tag in tags):
                    continue
            filtered.add(idx)
        candidate = filtered
    ordered = sorted_ids(index, candidate, sort_by, sort_dir)
    total = len(ordered)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_ids = ordered[start:end]
    stats_payload = None
    if page == 1:
        all_ids = set(index.ordered_ids)
        stats_payload = {
            "all": compute_counts(index, all_ids),
            "filtered": compute_counts(index, candidate),
        }

    items: list[dict[str, Any]] = []
    for idx in page_ids:
        paper = index.papers[idx]
        source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or idx)))
        translations = index.translated_md_by_hash.get(source_hash, {})
        translation_languages = sorted(translations.keys(), key=str.lower)
        asset_urls = resolve_asset_urls(index, source_hash, asset_config, prefer_local=prefer_local)
        items.append(
            {
                "source_hash": source_hash,
                "title": normalize_title(paper.get("paper_title") or ""),
                "summary_excerpt": extract_summary_snippet(paper),
                "summary_full": paper.get("summary") or "",
                "authors": paper.get("_authors") or [],
                "year": paper.get("_year") or "",
                "month": paper.get("_month") or "",
                "venue": normalize_venue(paper.get("_venue") or ""),
                "tags": paper.get("_tags") or [],
                "template_tags": paper.get("_template_tags") or [],
                "has_source": source_hash in index.md_path_by_hash,
                "has_translation": bool(translation_languages),
                "has_pdf": source_hash in index.pdf_path_by_hash,
                "has_summary": bool(paper.get("_has_summary")),
                "is_pdf_only": bool(paper.get("_is_pdf_only")),
                "translation_languages": translation_languages,
                "pdf_url": asset_urls["pdf_url"],
                "md_url": asset_urls["md_url"],
                "md_translated_url": asset_urls["md_translated_url"],
                "images_base_url": asset_urls["images_base_url"],
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


async def api_stats(request: Request) -> JSONResponse:
    """API endpoint for database statistics."""
    index: PaperIndex = request.app.state.index
    return JSONResponse(index.stats)


async def api_pdf(request: Request) -> Response:
    """API endpoint to serve PDF files."""
    index: PaperIndex = request.app.state.index
    source_hash = request.path_params["source_hash"]
    pdf_path = index.pdf_path_by_hash.get(source_hash)
    if not pdf_path:
        return Response("PDF not found", status_code=404)
    allowed_roots: list[Path] = request.app.state.pdf_roots
    if allowed_roots and not _ensure_under_roots(pdf_path, allowed_roots):
        return Response("Forbidden", status_code=403)
    return FileResponse(pdf_path)


async def api_markdown(request: Request) -> Response:
    """Dev-only API endpoint to serve raw markdown content."""
    if request.app.state.static_mode != "dev":
        return Response("Not Found", status_code=404)
    index: PaperIndex = request.app.state.index
    asset_config = request.app.state.asset_config
    export_dir = request.app.state.static_export_dir
    source_hash = request.path_params["source_hash"]
    lang = request.query_params.get("lang")
    md_path = None
    if export_dir and asset_config and asset_config.enabled and (asset_config.base_url or "") == "":
        if lang:
            translated_url = asset_config.translated_md_urls.get(source_hash, {}).get(lang.lower())
            if translated_url:
                rel_path = translated_url.lstrip("/")
                export_path = export_dir / rel_path
                if export_path.exists():
                    raw = _safe_read_text(export_path)
                    return Response(raw, media_type="text/markdown")
        else:
            md_url = asset_config.md_urls.get(source_hash)
            if md_url:
                rel_path = md_url.lstrip("/")
                export_path = export_dir / rel_path
                if export_path.exists():
                    raw = _safe_read_text(export_path)
                    return Response(raw, media_type="text/markdown")
    if lang:
        md_path = index.translated_md_by_hash.get(source_hash, {}).get(lang.lower())
    else:
        md_path = index.md_path_by_hash.get(source_hash)
    if not md_path:
        return Response("Markdown not found", status_code=404)
    raw = _safe_read_text(md_path)
    if lang:
        raw = normalize_markdown_images(raw)
    return Response(raw, media_type="text/markdown")
