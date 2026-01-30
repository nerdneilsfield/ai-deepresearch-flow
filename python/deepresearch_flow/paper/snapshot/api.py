from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from deepresearch_flow.paper.snapshot.text import merge_adjacent_markers, remove_cjk_spaces, rewrite_search_query

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_facet_value(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


_FACET_TYPE_BY_NAME = {
    "author": "author",
    "authors": "author",
    "institution": "institution",
    "institutions": "institution",
    "venue": "venue",
    "venues": "venue",
    "keyword": "keyword",
    "keywords": "keyword",
    "tag": "tag",
    "tags": "tag",
    "year": "year",
    "years": "year",
    "month": "month",
    "months": "month",
    "summary_template": "summary_template",
    "summary_templates": "summary_template",
    "templates": "summary_template",
    "output_language": "output_language",
    "output_languages": "output_language",
    "provider": "provider",
    "providers": "provider",
    "model": "model",
    "models": "model",
    "prompt_template": "prompt_template",
    "prompt_templates": "prompt_template",
    "translation_lang": "translation_lang",
    "translation_langs": "translation_lang",
    "translations": "translation_lang",
}

_SEARCH_SORTS = {
    "year_desc": (
        "CASE WHEN p.year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END, "
        "CAST(p.year AS INT) DESC, LOWER(p.title) ASC"
    ),
    "year_asc": (
        "CASE WHEN p.year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END, "
        "CAST(p.year AS INT) ASC, LOWER(p.title) ASC"
    ),
    "title_asc": "LOWER(p.title) ASC",
    "title_desc": "LOWER(p.title) DESC",
    "venue_asc": "LOWER(p.venue) ASC, LOWER(p.title) ASC",
    "venue_desc": "LOWER(p.venue) DESC, LOWER(p.title) ASC",
}

_FACET_TYPE_TO_KEY = {
    "author": "authors",
    "institution": "institutions",
    "venue": "venues",
    "keyword": "keywords",
    "tag": "tags",
    "year": "years",
    "month": "months",
    "summary_template": "summary_templates",
    "output_language": "output_languages",
    "provider": "providers",
    "model": "models",
    "prompt_template": "prompt_templates",
    "translation_lang": "translation_langs",
}


@dataclass(frozen=True)
class ApiLimits:
    max_query_length: int = 500
    max_page_size: int = 100
    max_pagination_offset: int = 10_000  # page * page_size


@dataclass(frozen=True)
class SnapshotApiConfig:
    snapshot_db: Path
    static_base_url: str
    cors_allowed_origins: list[str]
    limits: ApiLimits


def _normalize_base_url(value: str) -> str:
    return (value or "").rstrip("/")


def _json_error(status_code: int, *, error: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status_code)


def _open_ro_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    return conn


def _snapshot_build_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM snapshot_meta WHERE key = 'snapshot_build_id' LIMIT 1"
    ).fetchone()
    return str(row["value"]) if row else ""


def _asset_urls(
    *,
    static_base_url: str,
    snapshot_build_id: str,
    paper_id: str,
    pdf_hash: str | None,
    source_md_hash: str | None,
    translated: dict[str, str],
) -> dict[str, Any]:
    base = _normalize_base_url(static_base_url)
    images_base_url = f"{base}/images" if base else ""
    summary_url = f"{base}/summary/{paper_id}.json"
    manifest_url = f"{base}/manifest/{paper_id}.json"
    if snapshot_build_id:
        summary_url = f"{summary_url}?v={snapshot_build_id}"
        manifest_url = f"{manifest_url}?v={snapshot_build_id}"
    return {
        "static_base_url": base,
        "pdf_url": f"{base}/pdf/{pdf_hash}.pdf" if pdf_hash else None,
        "source_md_url": f"{base}/md/{source_md_hash}.md" if source_md_hash else None,
        "translated_md_urls": {
            lang: f"{base}/md_translate/{lang}/{md_hash}.md" for lang, md_hash in translated.items()
        },
        "images_base_url": images_base_url,
        "summary_url": summary_url,
        "manifest_url": manifest_url,
    }


def _summary_urls(
    *,
    static_base_url: str,
    snapshot_build_id: str,
    paper_id: str,
    template_tags: list[str],
) -> dict[str, str]:
    base = _normalize_base_url(static_base_url)
    out: dict[str, str] = {}
    for tag in template_tags:
        safe_tag = quote(tag, safe="")
        url = f"{base}/summary/{paper_id}/{safe_tag}.json"
        if snapshot_build_id:
            url = f"{url}?v={snapshot_build_id}"
        out[tag] = url
    return out


def _list_facet_values(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    join_table: str,
    facet_table: str,
    facet_id: str,
) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT f.value
        FROM {join_table} j
        JOIN {facet_table} f ON f.{facet_id} = j.{facet_id}
        WHERE j.paper_id = ?
        ORDER BY f.value ASC
        """,
        (paper_id,),
    ).fetchall()
    return [str(r["value"]) for r in rows]


def _parse_pagination(request: Request, limits: ApiLimits) -> tuple[int, int] | JSONResponse:
    page_raw = request.query_params.get("page", "1")
    page_size_raw = request.query_params.get("page_size", "20")
    try:
        page = int(page_raw)
        page_size = int(page_size_raw)
    except ValueError:
        return _json_error(400, error="invalid_pagination", detail="page and page_size must be integers")
    if page <= 0 or page_size <= 0:
        return _json_error(400, error="invalid_pagination", detail="page and page_size must be positive")
    if page_size > limits.max_page_size:
        return _json_error(
            400,
            error="page_size_too_large",
            detail=f"page_size must not exceed {limits.max_page_size}",
        )
    if page * page_size > limits.max_pagination_offset:
        return _json_error(
            400,
            error="pagination_too_deep",
            detail="pagination depth exceeds limit",
        )
    return page, page_size


async def _api_search(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    pagination = _parse_pagination(request, cfg.limits)
    if isinstance(pagination, JSONResponse):
        return pagination
    page, page_size = pagination
    q = (request.query_params.get("q") or "").strip()
    sort_raw = (request.query_params.get("sort") or "").strip().lower()
    if len(q) > cfg.limits.max_query_length:
        return _json_error(
            400,
            error="query_too_long",
            detail=f"q must not exceed {cfg.limits.max_query_length} characters",
        )

    if sort_raw and sort_raw not in _SEARCH_SORTS and sort_raw != "relevance":
        return _json_error(400, error="invalid_sort", detail="unsupported sort value")
    sort_key = sort_raw
    if not sort_key:
        sort_key = "relevance" if q else "year_desc"
    if not q and sort_key == "relevance":
        sort_key = "year_desc"

    offset = (page - 1) * page_size

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        build_id = _snapshot_build_id(conn)
        items: list[dict[str, Any]] = []
        total = 0

        if q:
            match_expr = rewrite_search_query(q)
            if not match_expr:
                return JSONResponse({"page": page, "page_size": page_size, "total": 0, "has_more": False, "items": []})

            total_row = conn.execute(
                "SELECT COUNT(*) AS c FROM paper_fts WHERE paper_fts MATCH ?",
                (match_expr,),
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0

            order_by = "rank" if sort_key == "relevance" else _SEARCH_SORTS.get(sort_key, _SEARCH_SORTS["year_desc"])
            rows = conn.execute(
                f"""
                SELECT
                  p.paper_id,
                  p.title,
                  p.year,
                  p.venue,
                  p.preferred_summary_template,
                  p.summary_preview,
                  p.paper_index,
                  p.pdf_content_hash,
                  p.source_md_content_hash,
                  snippet(paper_fts, -1, '[[[', ']]]', '…', 30) AS snippet_markdown,
                  bm25(paper_fts, 5.0, 3.0, 1.0, 1.0, 2.0) AS rank
                FROM paper_fts
                JOIN paper p ON p.paper_id = paper_fts.paper_id
                WHERE paper_fts MATCH ?
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (match_expr, page_size, offset),
            ).fetchall()

            for row in rows:
                paper_id = str(row["paper_id"])
                snippet = str(row["snippet_markdown"] or "")
                snippet = remove_cjk_spaces(snippet)
                snippet = merge_adjacent_markers(snippet)
                translated_rows = conn.execute(
                    "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                    (paper_id,),
                ).fetchall()
                translated = {str(r["lang"]): str(r["md_content_hash"]) for r in translated_rows}
                authors = _list_facet_values(conn, paper_id=paper_id, join_table="paper_author", facet_table="author", facet_id="author_id")
                assets = _asset_urls(
                    static_base_url=cfg.static_base_url,
                    snapshot_build_id=build_id,
                    paper_id=paper_id,
                    pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                    source_md_hash=str(row["source_md_content_hash"]) if row["source_md_content_hash"] else None,
                    translated=translated,
                )
                items.append(
                    {
                        "paper_id": paper_id,
                        "title": str(row["title"]),
                        "year": str(row["year"]),
                        "venue": str(row["venue"]),
                        "snippet_markdown": snippet,
                        "summary_preview": str(row["summary_preview"] or ""),
                        "paper_index": int(row["paper_index"] or 0),
                        "authors": authors,
                        "preferred_summary_template": str(row["preferred_summary_template"] or ""),
                        "has_pdf": bool(row["pdf_content_hash"]),
                        "has_source": bool(row["source_md_content_hash"]),
                        "has_translated": bool(translated),
                        **assets,
                    }
                )
        else:
            total_row = conn.execute("SELECT COUNT(*) AS c FROM paper").fetchone()
            total = int(total_row["c"]) if total_row else 0
            order_by = _SEARCH_SORTS.get(sort_key, _SEARCH_SORTS["year_desc"])
            rows = conn.execute(
                f"""
                SELECT p.paper_id, p.title, p.year, p.venue, p.preferred_summary_template, p.summary_preview, p.paper_index,
                       p.pdf_content_hash, p.source_md_content_hash
                FROM paper p
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
            for row in rows:
                paper_id = str(row["paper_id"])
                translated_rows = conn.execute(
                    "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                    (paper_id,),
                ).fetchall()
                translated = {str(r["lang"]): str(r["md_content_hash"]) for r in translated_rows}
                authors = _list_facet_values(conn, paper_id=paper_id, join_table="paper_author", facet_table="author", facet_id="author_id")
                assets = _asset_urls(
                    static_base_url=cfg.static_base_url,
                    snapshot_build_id=build_id,
                    paper_id=paper_id,
                    pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                    source_md_hash=str(row["source_md_content_hash"]) if row["source_md_content_hash"] else None,
                    translated=translated,
                )
                items.append(
                    {
                        "paper_id": paper_id,
                        "title": str(row["title"]),
                        "year": str(row["year"]),
                        "venue": str(row["venue"]),
                        "summary_preview": str(row["summary_preview"] or ""),
                        "paper_index": int(row["paper_index"] or 0),
                        "authors": authors,
                        "preferred_summary_template": str(row["preferred_summary_template"] or ""),
                        "has_pdf": bool(row["pdf_content_hash"]),
                        "has_source": bool(row["source_md_content_hash"]),
                        "has_translated": bool(translated),
                        **assets,
                    }
                )

        has_more = (page * page_size) < total and bool(items)
        return JSONResponse({"page": page, "page_size": page_size, "total": total, "has_more": has_more, "items": items})
    finally:
        conn.close()


async def _api_paper_detail(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    paper_id = str(request.path_params["paper_id"])
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        build_id = _snapshot_build_id(conn)
        row = conn.execute(
            """
            SELECT paper_id, title, year, venue, preferred_summary_template,
                   output_language, provider, model, prompt_template,
                   pdf_content_hash, source_md_content_hash
            FROM paper
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            return _json_error(404, error="not_found", detail="paper not found")

        translated_rows = conn.execute(
            "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        translated = {str(r["lang"]): str(r["md_content_hash"]) for r in translated_rows}
        assets = _asset_urls(
            static_base_url=cfg.static_base_url,
            snapshot_build_id=build_id,
            paper_id=paper_id,
            pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
            source_md_hash=str(row["source_md_content_hash"]) if row["source_md_content_hash"] else None,
            translated=translated,
        )

        summary_rows = conn.execute(
            "SELECT template_tag FROM paper_summary WHERE paper_id = ? ORDER BY LOWER(template_tag) ASC",
            (paper_id,),
        ).fetchall()
        template_tags = [str(r["template_tag"]) for r in summary_rows]
        preferred_template = str(row["preferred_summary_template"] or "")
        summary_urls = _summary_urls(
            static_base_url=cfg.static_base_url,
            snapshot_build_id=build_id,
            paper_id=paper_id,
            template_tags=template_tags,
        )

        authors = _list_facet_values(conn, paper_id=paper_id, join_table="paper_author", facet_table="author", facet_id="author_id")
        keywords = _list_facet_values(conn, paper_id=paper_id, join_table="paper_keyword", facet_table="keyword", facet_id="keyword_id")
        institutions = _list_facet_values(conn, paper_id=paper_id, join_table="paper_institution", facet_table="institution", facet_id="institution_id")
        tags = _list_facet_values(conn, paper_id=paper_id, join_table="paper_tag", facet_table="tag", facet_id="tag_id")

        return JSONResponse(
            {
                "paper_id": paper_id,
                "title": str(row["title"]),
                "year": str(row["year"]),
                "venue": str(row["venue"]),
                "authors": authors,
                "keywords": keywords,
                "institutions": institutions,
                "tags": tags,
                "output_language": str(row["output_language"] or ""),
                "provider": str(row["provider"] or ""),
                "model": str(row["model"] or ""),
                "prompt_template": str(row["prompt_template"] or ""),
                "preferred_summary_template": preferred_template,
                "summary_urls": summary_urls,
                **assets,
            }
        )
    finally:
        conn.close()


async def _api_facet_list(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    facet = str(request.path_params["facet"])
    pagination = _parse_pagination(request, cfg.limits)
    if isinstance(pagination, JSONResponse):
        return pagination
    page, page_size = pagination
    offset = (page - 1) * page_size

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        if facet == "years":
            total_row = conn.execute("SELECT COUNT(*) AS c FROM year_count").fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(
                """
                SELECT year AS id, year AS value, paper_count
                FROM year_count
                ORDER BY
                  CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
                  CAST(year AS INT) DESC,
                  year ASC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
            items = [{"id": str(r["id"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows]
        elif facet == "months":
            total_row = conn.execute("SELECT COUNT(*) AS c FROM month_count").fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(
                """
                SELECT month AS id, month AS value, paper_count
                FROM month_count
                ORDER BY
                  CASE WHEN month GLOB '[0-1][0-9]' THEN 0 ELSE 1 END,
                  CAST(month AS INT) ASC,
                  month ASC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
            items = [{"id": str(r["id"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows]
        else:
            mapping = {
                "authors": ("author", "author_id"),
                "keywords": ("keyword", "keyword_id"),
                "institutions": ("institution", "institution_id"),
                "tags": ("tag", "tag_id"),
                "venues": ("venue", "venue_id"),
            }
            if facet in mapping:
                table, id_col = mapping[facet]
                total_row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                total = int(total_row["c"]) if total_row else 0
                rows = conn.execute(
                    f"""
                    SELECT {id_col} AS id, value, paper_count
                    FROM {table}
                    ORDER BY paper_count DESC, value ASC
                    LIMIT ? OFFSET ?
                    """,
                    (page_size, offset),
                ).fetchall()
                items = [
                    {"id": int(r["id"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows
                ]
            else:
                facet_type = _FACET_TYPE_BY_NAME.get(facet)
                if not facet_type:
                    return _json_error(404, error="not_found", detail="facet not found")
                total_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM facet_node WHERE facet_type = ?",
                    (facet_type,),
                ).fetchone()
                total = int(total_row["c"]) if total_row else 0
                rows = conn.execute(
                    """
                    SELECT value, paper_count
                    FROM facet_node
                    WHERE facet_type = ?
                    ORDER BY paper_count DESC, value ASC
                    LIMIT ? OFFSET ?
                    """,
                    (facet_type, page_size, offset),
                ).fetchall()
                items = [
                    {"id": str(r["value"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])}
                    for r in rows
                ]

        has_more = (page * page_size) < total and bool(items)
        return JSONResponse({"page": page, "page_size": page_size, "total": total, "has_more": has_more, "items": items})
    finally:
        conn.close()


async def _api_facet_papers(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    facet = str(request.path_params["facet"])
    facet_id = str(request.path_params["facet_id"])
    pagination = _parse_pagination(request, cfg.limits)
    if isinstance(pagination, JSONResponse):
        return pagination
    page, page_size = pagination
    offset = (page - 1) * page_size

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        mapping = {
            "authors": ("paper_author", "author_id"),
            "keywords": ("paper_keyword", "keyword_id"),
            "institutions": ("paper_institution", "institution_id"),
            "tags": ("paper_tag", "tag_id"),
            "venues": ("paper_venue", "venue_id"),
        }
        if facet == "years":
            total_row = conn.execute("SELECT paper_count AS c FROM year_count WHERE year = ?", (facet_id,)).fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(
                """
                SELECT paper_id, title, year, venue, summary_preview, pdf_content_hash, source_md_content_hash
                FROM paper
                WHERE year = ?
                ORDER BY LOWER(title) ASC
                LIMIT ? OFFSET ?
                """,
                (facet_id, page_size, offset),
            ).fetchall()
        elif facet == "months":
            total_row = conn.execute(
                "SELECT paper_count AS c FROM month_count WHERE month = ?",
                (facet_id,),
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(
                """
                SELECT paper_id, title, year, venue, summary_preview, pdf_content_hash, source_md_content_hash
                FROM paper
                WHERE month = ?
                ORDER BY
                  CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
                  CAST(year AS INT) DESC,
                  LOWER(title) ASC
                LIMIT ? OFFSET ?
                """,
                (facet_id, page_size, offset),
            ).fetchall()
        else:
            if facet not in mapping:
                return _json_error(404, error="not_found", detail="facet not found")
            join_table, id_col = mapping[facet]
            total_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {join_table} WHERE {id_col} = ?",
                (facet_id,),
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = conn.execute(
                f"""
                SELECT p.paper_id, p.title, p.year, p.venue, p.summary_preview, p.pdf_content_hash, p.source_md_content_hash
                FROM {join_table} j
                JOIN paper p ON p.paper_id = j.paper_id
                WHERE j.{id_col} = ?
                ORDER BY
                  CASE WHEN p.year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
                  CAST(p.year AS INT) DESC,
                  LOWER(p.title) ASC
                LIMIT ? OFFSET ?
                """,
                (facet_id, page_size, offset),
            ).fetchall()

        build_id = _snapshot_build_id(conn)
        items: list[dict[str, Any]] = []
        for row in rows:
            paper_id = str(row["paper_id"])
            translated_rows = conn.execute(
                "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                (paper_id,),
            ).fetchall()
            translated = {str(r["lang"]): str(r["md_content_hash"]) for r in translated_rows}
            authors = _list_facet_values(conn, paper_id=paper_id, join_table="paper_author", facet_table="author", facet_id="author_id")
            assets = _asset_urls(
                static_base_url=cfg.static_base_url,
                snapshot_build_id=build_id,
                paper_id=paper_id,
                pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                source_md_hash=str(row["source_md_content_hash"]) if row["source_md_content_hash"] else None,
                translated=translated,
            )
            items.append(
                {
                    "paper_id": paper_id,
                    "title": str(row["title"]),
                    "year": str(row["year"]),
                    "venue": str(row["venue"]),
                    "summary_preview": str(row["summary_preview"] or ""),
                    "authors": authors,
                    "has_pdf": bool(row["pdf_content_hash"]),
                    "has_source": bool(row["source_md_content_hash"]),
                    "has_translated": bool(translated),
                    **assets,
                }
            )

        has_more = (page * page_size) < total and bool(items)
        return JSONResponse({"page": page, "page_size": page_size, "total": total, "has_more": has_more, "items": items})
    finally:
        conn.close()


def _facet_node_id(conn: sqlite3.Connection, facet_type: str, value: str) -> int | None:
    normalized = _normalize_facet_value(value)
    if not normalized:
        return None
    row = conn.execute(
        "SELECT node_id FROM facet_node WHERE facet_type = ? AND value = ?",
        (facet_type, normalized),
    ).fetchone()
    return int(row["node_id"]) if row else None


def _facet_stats_for_node(conn: sqlite3.Connection, *, facet_type: str, value: str) -> dict[str, Any]:
    node_id = _facet_node_id(conn, facet_type, value)
    related: dict[str, list[dict[str, Any]]] = {key: [] for key in _FACET_TYPE_TO_KEY.values()}
    if node_id is None:
        return {"facet_type": facet_type, "value": _normalize_facet_value(value), "total": 0, "related": related}

    total_row = conn.execute(
        "SELECT paper_count FROM facet_node WHERE node_id = ?",
        (node_id,),
    ).fetchone()
    total = int(total_row["paper_count"]) if total_row else 0

    rows = conn.execute(
        """
        SELECT n.facet_type AS facet_type, n.value AS value, e.paper_count AS paper_count
        FROM facet_edge e
        JOIN facet_node n
          ON n.node_id = CASE WHEN e.node_id_a = ? THEN e.node_id_b ELSE e.node_id_a END
        WHERE e.node_id_a = ? OR e.node_id_b = ?
        ORDER BY e.paper_count DESC, n.value ASC
        """,
        (node_id, node_id, node_id),
    ).fetchall()

    for row in rows:
        other_type = str(row["facet_type"])
        key = _FACET_TYPE_TO_KEY.get(other_type)
        if not key:
            continue
        related[key].append({"value": str(row["value"]), "paper_count": int(row["paper_count"])})

    return {
        "facet_type": facet_type,
        "value": _normalize_facet_value(value),
        "total": total,
        "related": related,
    }


async def _api_facet_by_value_papers(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    facet = str(request.path_params["facet"])
    raw_value = str(request.path_params["value"])
    pagination = _parse_pagination(request, cfg.limits)
    if isinstance(pagination, JSONResponse):
        return pagination
    page, page_size = pagination
    offset = (page - 1) * page_size

    facet_type = _FACET_TYPE_BY_NAME.get(facet)
    if not facet_type:
        return _json_error(404, error="not_found", detail="facet not found")

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        node_id = _facet_node_id(conn, facet_type, raw_value)
        if node_id is None:
            return JSONResponse({"page": page, "page_size": page_size, "total": 0, "has_more": False, "items": []})

        total_row = conn.execute(
            "SELECT paper_count FROM facet_node WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        total = int(total_row["paper_count"]) if total_row else 0

        rows = conn.execute(
            """
            SELECT p.paper_id, p.title, p.year, p.venue, p.summary_preview, p.pdf_content_hash, p.source_md_content_hash
            FROM paper_facet pf
            JOIN paper p ON p.paper_id = pf.paper_id
            WHERE pf.node_id = ?
            ORDER BY
              CASE WHEN p.year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
              CAST(p.year AS INT) DESC,
              LOWER(p.title) ASC
            LIMIT ? OFFSET ?
            """,
            (node_id, page_size, offset),
        ).fetchall()

        build_id = _snapshot_build_id(conn)
        items: list[dict[str, Any]] = []
        for row in rows:
            paper_id = str(row["paper_id"])
            translated_rows = conn.execute(
                "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                (paper_id,),
            ).fetchall()
            translated = {str(r["lang"]): str(r["md_content_hash"]) for r in translated_rows}
            authors = _list_facet_values(conn, paper_id=paper_id, join_table="paper_author", facet_table="author", facet_id="author_id")
            assets = _asset_urls(
                static_base_url=cfg.static_base_url,
                snapshot_build_id=build_id,
                paper_id=paper_id,
                pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                source_md_hash=str(row["source_md_content_hash"]) if row["source_md_content_hash"] else None,
                translated=translated,
            )
            items.append(
                {
                    "paper_id": paper_id,
                    "title": str(row["title"]),
                    "year": str(row["year"]),
                    "venue": str(row["venue"]),
                    "summary_preview": str(row["summary_preview"] or ""),
                    "authors": authors,
                    "has_pdf": bool(row["pdf_content_hash"]),
                    "has_source": bool(row["source_md_content_hash"]),
                    "has_translated": bool(translated),
                    **assets,
                }
            )

        has_more = (page * page_size) < total and bool(items)
        return JSONResponse({"page": page, "page_size": page_size, "total": total, "has_more": has_more, "items": items})
    finally:
        conn.close()


async def _api_facet_by_value_stats(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    facet = str(request.path_params["facet"])
    raw_value = str(request.path_params["value"])
    facet_type = _FACET_TYPE_BY_NAME.get(facet)
    if not facet_type:
        return _json_error(404, error="not_found", detail="facet not found")

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        return JSONResponse(_facet_stats_for_node(conn, facet_type=facet_type, value=raw_value))
    finally:
        conn.close()


async def _api_facet_stats(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    facet = str(request.path_params["facet"])
    facet_id = str(request.path_params["facet_id"])
    facet_type = _FACET_TYPE_BY_NAME.get(facet)
    if not facet_type:
        return _json_error(404, error="not_found", detail="facet not found")

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        value: str | None = None
        mapping = {
            "authors": ("author", "author_id"),
            "keywords": ("keyword", "keyword_id"),
            "institutions": ("institution", "institution_id"),
            "tags": ("tag", "tag_id"),
            "venues": ("venue", "venue_id"),
        }
        if facet in ("years", "months"):
            value = facet_id
        elif facet in mapping:
            table, id_col = mapping[facet]
            row = conn.execute(
                f"SELECT value FROM {table} WHERE {id_col} = ?",
                (facet_id,),
            ).fetchone()
            if row:
                value = str(row["value"])
        else:
            value = facet_id

        if not value:
            value = facet_id
        return JSONResponse(_facet_stats_for_node(conn, facet_type=facet_type, value=value))
    finally:
        conn.close()


async def _api_stats(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM paper").fetchone()
        total = int(total_row["c"]) if total_row else 0

        def top(table: str, *, limit: int = 20) -> list[dict[str, Any]]:
            rows = conn.execute(
                f"""
                SELECT value, paper_count
                FROM {table}
                ORDER BY paper_count DESC, value ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows]

        years = conn.execute(
            """
            SELECT year AS value, paper_count
            FROM year_count
            ORDER BY
              CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
              CAST(year AS INT) DESC,
              year ASC
            LIMIT 50
            """
        ).fetchall()
        months = conn.execute(
            """
            SELECT month AS value, paper_count
            FROM month_count
            ORDER BY
              CASE WHEN month GLOB '[0-1][0-9]' THEN 0 ELSE 1 END,
              CAST(month AS INT) ASC,
              month ASC
            """
        ).fetchall()

        return JSONResponse(
            {
                "total": total,
                "years": [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in years],
                "months": [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in months],
                "authors": top("author"),
                "venues": top("venue"),
                "institutions": top("institution"),
                "keywords": top("keyword"),
                "tags": top("tag"),
            }
        )
    finally:
        conn.close()


async def _api_config(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    return JSONResponse({"static_base_url": cfg.static_base_url})


def create_app(
    *,
    snapshot_db: Path,
    static_base_url: str,
    cors_allowed_origins: list[str] | None = None,
    limits: ApiLimits | None = None,
) -> Starlette:
    cfg = SnapshotApiConfig(
        snapshot_db=snapshot_db,
        static_base_url=_normalize_base_url(static_base_url),
        cors_allowed_origins=cors_allowed_origins or ["*"],
        limits=limits or ApiLimits(),
    )

    routes = [
        Route("/api/v1/config", _api_config, methods=["GET"]),
        Route("/api/v1/search", _api_search, methods=["GET"]),
        Route("/api/v1/stats", _api_stats, methods=["GET"]),
        Route("/api/v1/papers/{paper_id:str}", _api_paper_detail, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}", _api_facet_list, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}/{facet_id:str}/papers", _api_facet_papers, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}/{facet_id:str}/stats", _api_facet_stats, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}/by-value/{value:str}/papers", _api_facet_by_value_papers, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}/by-value/{value:str}/stats", _api_facet_by_value_stats, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    if cfg.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_allowed_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.cfg = cfg
    return app
