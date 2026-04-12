"""API route handlers for paper web UI."""

from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from deepresearch_flow.paper.db_ops import PaperIndex
from deepresearch_flow.paper.utils import stable_hash
from deepresearch_flow.paper.snapshot.identity import build_paper_key_candidates, choose_preferred_key, paper_id_for_key
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
from deepresearch_flow.paper.search import validate_venue_filter


async def _embed_query(text: str, config: Any, client_obj: httpx.AsyncClient) -> list[float]:
    from deepresearch_flow.paper.embedding import call_embedding

    if config is None or config.embedding is None:
        raise ValueError("Semantic search embedding config is unavailable")

    provider_config, model_config = config.embedding.resolve_active()
    result = await call_embedding(
        base_url=provider_config.base_url,
        api_key=provider_config.api_key,
        model=model_config.model_name,
        texts=[text],
        dimensions=config.embedding.dimensions,
        client=client_obj,
    )
    return result.vectors[0]


def _build_keyword_doc_ids(
    index: PaperIndex,
    query_text: str,
    *,
    year: int | None,
    venue: str | None,
    limit: int,
) -> list[str]:
    query = parse_query(query_text)
    candidate = _apply_query(index, query)
    filtered: set[int] = set()
    venue_lower = venue.strip().lower() if venue else ""
    for idx in candidate:
        paper = index.papers[idx]
        if year is not None and int(paper.get("_year") or 0) != year:
            continue
        if venue_lower and venue_lower not in str(paper.get("_venue") or "").lower():
            continue
        filtered.add(idx)
    ordered = sorted_ids(index, filtered, "", "desc")
    doc_ids: list[str] = []
    for idx in ordered:
        doc_id = _paper_doc_id(index.papers[idx])
        if doc_id:
            doc_ids.append(doc_id)
        if len(doc_ids) >= limit:
            break
    return doc_ids


def _paper_text_for_rerank(paper: dict[str, Any]) -> str:
    title = normalize_title(paper.get("paper_title") or "")
    summary = normalize_markdown_images(str(paper.get("summary") or "")).strip()
    venue = normalize_venue(paper.get("_venue") or "")
    authors = ", ".join(paper.get("_authors") or [])
    parts = [part for part in (title, summary, venue, authors) if part]
    return "\n".join(parts)


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


def _paper_doc_id(paper: dict[str, Any]) -> str | None:
    try:
        candidates = build_paper_key_candidates(paper)
    except Exception:
        return None
    if not candidates:
        return None
    return paper_id_for_key(choose_preferred_key(candidates).paper_key)


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


async def api_papers_semantic(request: Request) -> JSONResponse:
    access_token = getattr(request.app.state, "search_access_token", None)
    if access_token:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not auth.startswith("Bearer ") or not hmac.compare_digest(token, access_token):
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    embed_db = getattr(request.app.state, "embed_db", None)
    if embed_db is None:
        return JSONResponse({"error": "Semantic search not available"}, status_code=503)

    if request.query_params.get("probe") in {"1", "true", "yes"}:
        return JSONResponse({"ok": True})

    query_text = request.query_params.get("q", "").strip()
    if not query_text:
        return JSONResponse({"error": "Query parameter q is required"}, status_code=400)

    top_n = min(int(request.query_params.get("top_n", "10")), 100)

    where_parts: list[str] = []
    safe_venue: str | None = None
    year = request.query_params.get("year")
    if year:
        where_parts.append(f"year = {int(year)}")
    venue = request.query_params.get("venue")
    if venue:
        try:
            safe_venue = validate_venue_filter(venue)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        where_parts.append(f"venue = {json.dumps(safe_venue)}")
    where = " AND ".join(where_parts) if where_parts else None

    from deepresearch_flow.paper.reranker import OpenAICompatibleReranker
    from deepresearch_flow.paper.search import hybrid_search

    paper_config = getattr(request.app.state, "paper_config", None)
    index: PaperIndex | None = getattr(request.app.state, "index", None)
    paper_by_doc_id: dict[str, dict[str, Any]] = {}
    if index is not None:
        for paper in index.papers:
            doc_id = _paper_doc_id(paper)
            if doc_id:
                paper_by_doc_id[doc_id] = paper

    keyword_search_fn = None
    if index is not None:
        keyword_search_fn = lambda q, limit=30: _build_keyword_doc_ids(  # noqa: E731
            index,
            q,
            year=int(year) if year else None,
            venue=safe_venue if venue else None,
            limit=limit,
        )

    reranker = None
    if paper_config is not None and paper_config.rerank and paper_config.rerank.enabled:
        rerank_provider, rerank_model = paper_config.rerank.resolve_active()
        reranker = OpenAICompatibleReranker(
            base_url=rerank_provider.base_url,
            api_key=rerank_provider.api_key,
            model=rerank_model.model_name,
            max_context=rerank_model.max_context,
            max_chunks_per_doc=rerank_model.max_chunks_per_doc,
            instruction=rerank_model.instruction,
        )

    async with httpx.AsyncClient() as client:
        try:
            query_vector_val = await _embed_query(query_text, paper_config, client)
        except Exception:
            return JSONResponse({"error": "Semantic search query embedding failed"}, status_code=502)
        aggregated = await hybrid_search(
            query_vector=query_vector_val,
            query_text=query_text,
            vector_store_db=embed_db,
            keyword_search_fn=keyword_search_fn,
            reranker=reranker,
            vector_top_k=(paper_config.search.vector_top_k if paper_config and paper_config.search else top_n * 5),
            keyword_top_k=(paper_config.search.keyword_top_k if paper_config and paper_config.search else top_n * 3),
            rerank_top_n=top_n,
            hybrid=bool(paper_config.search.hybrid) if paper_config and paper_config.search else True,
            where=where,
            document_text_resolver=lambda doc_id: _paper_text_for_rerank(paper_by_doc_id[doc_id]) if doc_id in paper_by_doc_id else doc_id,
            client=client,
        )

    asset_config = getattr(request.app.state, "asset_config", None)
    prefer_local = getattr(request.app.state, "static_mode", None) == "dev"

    items = []
    for hit in aggregated:
        payload = {
            "doc_id": hit.doc_id,
            "score": hit.score,
            "score_type": hit.score_type,
            "matched_chunk": hit.matched_chunk,
            "matched_field": hit.matched_field,
            "matched_template": hit.matched_template or "_shared",
            "matched_chunk_type": hit.matched_chunk_type,
            "matched_lang": hit.matched_lang,
        }
        paper = paper_by_doc_id.get(hit.doc_id)
        if paper is not None and index is not None and asset_config is not None:
            source_hash = str(paper.get("source_hash") or stable_hash(str(paper.get("source_path") or hit.doc_id)))
            translations = index.translated_md_by_hash.get(source_hash, {})
            translation_languages = sorted(translations.keys(), key=str.lower)
            asset_urls = resolve_asset_urls(index, source_hash, asset_config, prefer_local=prefer_local)
            payload.update(
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
        items.append(payload)
    return JSONResponse({"items": items, "total": len(items)})


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
