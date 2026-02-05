from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Literal

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount

from fastmcp import FastMCP

from deepresearch_flow.paper.snapshot.common import ApiLimits, _open_ro_conn
from deepresearch_flow.paper.snapshot.text import merge_adjacent_markers, remove_cjk_spaces, rewrite_search_query

_SUPPORTED_PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18"}
_DEFAULT_MAX_CHARS = 50_000
_DEFAULT_TIMEOUT = 10.0
_PAPER_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


class McpToolError(Exception):
    """MCP tool exception for standardized error handling.
    
    FastMCP will catch this exception and convert it to a proper
    JSON-RPC error response that the client can understand.
    """
    
    def __init__(self, code: str, message: str, **details):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to error dictionary format."""
        return {"error": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class McpSnapshotConfig:
    snapshot_db: Path
    static_base_url: str
    static_export_dir: Path | None
    limits: ApiLimits
    origin_allowlist: list[str]
    max_chars_default: int = _DEFAULT_MAX_CHARS
    http_timeout: float = _DEFAULT_TIMEOUT
    max_paper_id_length: int = 64
    # HTTP client stored in object __dict__ to avoid dataclass frozen restriction
    _http_client: httpx.Client | None = field(default=None, repr=False, compare=False)
    
    def get_http_client(self) -> httpx.Client:
        """Get or create a shared HTTP client with connection pooling."""
        if self._http_client is None:
            object.__setattr__(
                self,
                '_http_client',
                httpx.Client(
                    timeout=self.http_timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_keepalive_connections=10,
                        max_connections=20
                    )
                )
            )
        return self._http_client


class McpRequestGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, origin_allowlist: list[str], allowed_methods: set[str] | None = None) -> None:
        super().__init__(app)
        self._allowlist = [origin.lower() for origin in origin_allowlist]
        self._allowed_methods = {method.upper() for method in (allowed_methods or {"POST", "OPTIONS"})}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method.upper() not in self._allowed_methods:
            return Response("Method Not Allowed", status_code=405)
        origin = request.headers.get("origin")
        if origin and not self._is_allowed_origin(origin):
            return Response("Forbidden", status_code=403)
        protocol = request.headers.get("mcp-protocol-version")
        if protocol and protocol not in _SUPPORTED_PROTOCOL_VERSIONS:
            return Response("Bad Request", status_code=400)
        return await call_next(request)

    def _is_allowed_origin(self, origin: str) -> bool:
        if not self._allowlist or "*" in self._allowlist:
            return True
        return origin.lower() in self._allowlist


_CONFIG: McpSnapshotConfig | None = None
mcp = FastMCP("Paper DB MCP")


def configure(config: McpSnapshotConfig) -> None:
    global _CONFIG
    _CONFIG = config


def _allowed_methods_for_transport(transport: Literal["streamable-http", "sse"]) -> set[str]:
    if transport == "sse":
        return {"GET", "POST", "OPTIONS"}
    return {"POST", "OPTIONS"}


def create_mcp_transport_app(
    config: McpSnapshotConfig,
    *,
    transport: Literal["streamable-http", "sse"] = "streamable-http",
) -> tuple[Starlette, Any]:
    """Create MCP app for a specific transport with transport-aware method guard."""
    configure(config)
    mcp_app = mcp.http_app(path="/", transport=transport, stateless_http=(transport == "streamable-http"))
    wrapped = Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[
            Middleware(
                McpRequestGuardMiddleware,
                origin_allowlist=config.origin_allowlist,
                allowed_methods=_allowed_methods_for_transport(transport),
            ),
        ],
    )
    return wrapped, mcp_app.lifespan


def create_mcp_apps(config: McpSnapshotConfig) -> tuple[dict[str, Starlette], Any]:
    """Create streamable-http and sse MCP apps.

    Returns:
        A tuple of (apps_by_transport, lifespan_context).
    """
    streamable_app, lifespan = create_mcp_transport_app(config, transport="streamable-http")
    sse_app, _ = create_mcp_transport_app(config, transport="sse")
    return {"streamable-http": streamable_app, "sse": sse_app}, lifespan


def create_mcp_app(config: McpSnapshotConfig) -> tuple[Starlette, Any]:
    """Backward-compatible helper returning streamable-http MCP app."""
    return create_mcp_transport_app(config, transport="streamable-http")


def _get_config() -> McpSnapshotConfig:
    if _CONFIG is None:
        raise RuntimeError("MCP server not configured")
    return _CONFIG


def _validate_query(query: str, cfg: McpSnapshotConfig) -> str:
    """Validate search query string.
    
    Raises:
        McpToolError: If query is invalid or too long.
    """
    if not query or not query.strip():
        raise McpToolError("invalid_query", "Query cannot be empty")
    if len(query) > cfg.limits.max_query_length:
        raise McpToolError(
            "query_too_long",
            f"Query exceeds maximum length of {cfg.limits.max_query_length}",
            length=len(query),
            max_length=cfg.limits.max_query_length
        )
    return query.strip()


def _validate_paper_id(paper_id: str, cfg: McpSnapshotConfig) -> str:
    """Validate paper ID format.
    
    Raises:
        McpToolError: If paper_id is invalid.
    """
    if not paper_id:
        raise McpToolError("invalid_paper_id", "Paper ID cannot be empty")
    if len(paper_id) > cfg.max_paper_id_length:
        raise McpToolError(
            "paper_id_too_long",
            f"Paper ID exceeds maximum length of {cfg.max_paper_id_length}",
            length=len(paper_id),
            max_length=cfg.max_paper_id_length
        )
    if not _PAPER_ID_PATTERN.match(paper_id):
        raise McpToolError(
            "invalid_paper_id_format",
            "Paper ID must contain only alphanumeric characters, hyphens, and underscores",
            paper_id=paper_id
        )
    return paper_id


def _truncate(text: str, max_chars: int | None) -> str:
    """Truncate text with marker."""
    if max_chars is None or max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    return f"{text[:max_chars]}\n[truncated: {remaining} more chars]"


def _read_static_text(rel_path: str) -> str | None:
    """Read static text from local export directory if available."""
    cfg = _get_config()
    if cfg.static_export_dir:
        path = cfg.static_export_dir / rel_path
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def _fetch_static_text(rel_path: str) -> str:
    """Fetch static text from HTTP remote."""
    cfg = _get_config()
    if cfg.static_base_url:
        base = cfg.static_base_url.rstrip("/")
        url = f"{base}/{rel_path.lstrip('/')}"
        client = cfg.get_http_client()
        response = client.get(url)
        response.raise_for_status()
        return response.text
    raise FileNotFoundError("static_base_url not configured")


def _load_static_text(rel_path: str) -> str:
    """Load static text with fallback: local first, then HTTP."""
    try:
        text = _read_static_text(rel_path)
        if text is not None:
            return text
        return _fetch_static_text(rel_path)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"asset_fetch_failed:{exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("asset_fetch_failed:request_error") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("asset_fetch_failed:not_configured") from exc


def _load_summary_json(paper_id: str, template: str | None) -> tuple[str | None, list[str] | None]:
    """Load summary JSON content and return available templates list."""
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        row = conn.execute(
            "SELECT preferred_summary_template FROM paper WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not row:
            return None, None
        preferred = str(row["preferred_summary_template"] or "")
        template_rows = conn.execute(
            "SELECT template_tag FROM paper_summary WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        available = sorted((str(item["template_tag"]) for item in template_rows), key=str.lower)
        selected = (template or preferred).strip()
        if not selected or selected not in set(available):
            return None, available
        if template:
            rel_path = f"summary/{paper_id}/{selected}.json"
        else:
            rel_path = f"summary/{paper_id}.json"
        return _load_static_text(rel_path), available
    finally:
        conn.close()


def _load_source_markdown(paper_id: str) -> str | None:
    """Load source markdown for paper."""
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        row = conn.execute(
            "SELECT source_md_content_hash FROM paper WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not row or not row["source_md_content_hash"]:
            return None
        rel_path = f"md/{row['source_md_content_hash']}.md"
        return _load_static_text(rel_path)
    finally:
        conn.close()


def _load_translation_markdown(paper_id: str, lang: str) -> str | None:
    """Load translation markdown for paper and language."""
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        row = conn.execute(
            "SELECT md_content_hash FROM paper_translation WHERE paper_id = ? AND lang = ?",
            (paper_id, lang),
        ).fetchone()
        if not row or not row["md_content_hash"]:
            return None
        rel_path = f"md_translate/{lang}/{row['md_content_hash']}.md"
        return _load_static_text(rel_path)
    finally:
        conn.close()


# ==================== MCP Tools ====================

@mcp.tool()
def search_papers(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Full-text search for papers (relevance-ranked).
    
    Use when you only have topic keywords.
    Returns paper_id, title, year, venue, snippet_markdown.
    """
    cfg = _get_config()
    query = _validate_query(query, cfg)
    limit = min(max(1, int(limit)), cfg.limits.max_page_size)
    
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        match_expr = rewrite_search_query(query)
        if not match_expr:
            return []
        cur = conn.execute(
            """
            SELECT
              p.paper_id,
              p.title,
              p.year,
              p.venue,
              snippet(paper_fts, -1, '[[[', ']]]', '…', 30) AS snippet_markdown,
              bm25(paper_fts, 5.0, 3.0, 1.0, 1.0, 2.0) AS rank
            FROM paper_fts
            JOIN paper p ON p.paper_id = paper_fts.paper_id
            WHERE paper_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match_expr, limit),
        )
        rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            snippet = str(row["snippet_markdown"] or "")
            snippet = remove_cjk_spaces(snippet)
            snippet = merge_adjacent_markers(snippet)
            results.append({
                "paper_id": str(row["paper_id"]),
                "title": str(row["title"]),
                "year": str(row["year"]),
                "venue": str(row["venue"]),
                "snippet_markdown": snippet,
            })
        return results
    finally:
        conn.close()


@mcp.tool()
def search_papers_by_keyword(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search papers by keyword/tag (exact match).
    
    Use when you know specific keywords or tags.
    """
    cfg = _get_config()
    limit = min(max(1, int(limit)), cfg.limits.max_page_size)
    
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT p.paper_id, p.title, p.year, p.venue, p.summary_preview
            FROM paper p
            JOIN paper_keyword pk ON pk.paper_id = p.paper_id
            JOIN keyword k ON k.keyword_id = pk.keyword_id
            WHERE k.value LIKE ?
            ORDER BY p.year DESC, p.title ASC
            LIMIT ?
            """,
            (f"%{keyword}%", limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            snippet = str(row["summary_preview"] or "")
            snippet = remove_cjk_spaces(snippet)
            snippet = merge_adjacent_markers(snippet)
            results.append({
                "paper_id": str(row["paper_id"]),
                "title": str(row["title"]),
                "year": str(row["year"]),
                "venue": str(row["venue"]),
                "snippet_markdown": snippet,
            })
        return results
    finally:
        conn.close()


@mcp.tool()
def get_paper_metadata(paper_id: str) -> dict[str, Any]:
    """Get paper metadata and available summary templates.
    
    Call this first before requesting a summary to discover available templates.
    """
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        row = conn.execute(
            """
            SELECT paper_id, title, year, venue, preferred_summary_template
            FROM paper WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            raise McpToolError("not_found", "paper not found", paper_id=paper_id)
        template_rows = conn.execute(
            "SELECT template_tag FROM paper_summary WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        available = sorted((str(item["template_tag"]) for item in template_rows), key=str.lower)
        return {
            "paper_id": str(row["paper_id"]),
            "title": str(row["title"]),
            "year": str(row["year"]),
            "venue": str(row["venue"]),
            "doi": None,
            "arxiv_id": None,
            "openreview_id": None,
            "paper_pw_url": None,
            "preferred_summary_template": row["preferred_summary_template"],
            "available_summary_templates": available,
        }
    finally:
        conn.close()


@mcp.tool()
def get_paper_summary(paper_id: str, template: str | None = None, max_chars: int | None = None) -> str:
    """Get summary JSON as raw string.
    
    Uses preferred template if template is not specified.
    Returns the full JSON content (not a URL).
    """
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    max_chars = max_chars if max_chars is not None else cfg.max_chars_default
    
    try:
        payload, available = _load_summary_json(paper_id, template)
    except RuntimeError as exc:
        raise McpToolError(
            "asset_fetch_failed",
            "Failed to fetch summary asset",
            paper_id=paper_id,
            template=template,
            detail=str(exc),
        ) from exc
    
    if payload is None:
        raise McpToolError(
            "template_not_available",
            "Template not available",
            paper_id=paper_id,
            template=template,
            available_summary_templates=available,
        )
    
    return _truncate(payload, max_chars)


@mcp.tool()
def get_paper_source(paper_id: str, max_chars: int | None = None) -> str:
    """Get source markdown text.
    
    Content may be large; use max_chars to limit size.
    """
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    max_chars = max_chars if max_chars is not None else cfg.max_chars_default
    
    try:
        content = _load_source_markdown(paper_id)
    except RuntimeError as exc:
        raise McpToolError(
            "asset_fetch_failed",
            "Failed to fetch source asset",
            paper_id=paper_id,
            detail=str(exc),
        ) from exc
    
    if content is None:
        raise McpToolError(
            "source_not_available",
            "Source markdown not available",
            paper_id=paper_id
        )
    
    return _truncate(content, max_chars)


@mcp.tool()
def get_database_stats() -> dict[str, Any]:
    """Get database statistics.
    
    Returns totals, year/month distributions, and top facets
    (authors, venues, keywords, institutions, tags).
    """
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM paper").fetchone()
        total = int(total_row["c"]) if total_row else 0
        
        def top(table: str, limit: int = 20) -> list[dict[str, Any]]:
            rows = conn.execute(
                f"SELECT value, paper_count FROM {table} ORDER BY paper_count DESC, value ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows]
        
        years = conn.execute(
            """
            SELECT year AS value, paper_count
            FROM year_count
            ORDER BY CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN 0 ELSE 1 END,
                     CAST(year AS INT) DESC, year ASC
            LIMIT 50
            """,
        ).fetchall()
        months = conn.execute(
            """
            SELECT month AS value, paper_count
            FROM month_count
            ORDER BY CASE WHEN month GLOB '[0-1][0-9]' THEN 0 ELSE 1 END,
                     CAST(month AS INT) ASC, month ASC
            """,
        ).fetchall()
        
        return {
            "total": total,
            "years": [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in years],
            "months": [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in months],
            "authors": top("author"),
            "venues": top("venue"),
            "institutions": top("institution"),
            "keywords": top("keyword"),
            "tags": top("tag"),
        }
    finally:
        conn.close()


@mcp.tool()
def list_top_facets(category: str, limit: int = 20) -> list[dict[str, Any]]:
    """List top facet values.
    
    Category: author | venue | keyword | institution | tag
    """
    table_map = {
        "author": "author",
        "venue": "venue",
        "keyword": "keyword",
        "institution": "institution",
        "tag": "tag",
    }
    table = table_map.get((category or "").strip().lower())
    if not table:
        raise McpToolError(
            "invalid_category",
            f"Invalid category: {category}. Must be one of: {', '.join(table_map.keys())}",
            category=category
        )
    
    limit = max(1, int(limit))
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        rows = conn.execute(
            f"SELECT value, paper_count FROM {table} ORDER BY paper_count DESC, value ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in rows]
    finally:
        conn.close()


@mcp.tool()
def filter_papers(
    author: str | None = None,
    venue: str | None = None,
    year: str | None = None,
    keyword: str | None = None,
    tag: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Filter papers by structured fields.
    
    Use for precise filtering by author, venue, year, keyword, or tag.
    """
    cfg = _get_config()
    limit = min(max(1, int(limit)), cfg.limits.max_page_size)
    
    query = "SELECT DISTINCT p.paper_id, p.title, p.year, p.venue FROM paper p"
    joins: list[str] = []
    conditions: list[str] = []
    params: list[Any] = []
    
    if author:
        joins.append("JOIN paper_author pa ON pa.paper_id = p.paper_id")
        joins.append("JOIN author a ON a.author_id = pa.author_id")
        conditions.append("a.value LIKE ?")
        params.append(f"%{author}%")
    if keyword:
        joins.append("JOIN paper_keyword pk ON pk.paper_id = p.paper_id")
        joins.append("JOIN keyword k ON k.keyword_id = pk.keyword_id")
        conditions.append("k.value LIKE ?")
        params.append(f"%{keyword}%")
    if tag:
        joins.append("JOIN paper_tag pt ON pt.paper_id = p.paper_id")
        joins.append("JOIN tag t ON t.tag_id = pt.tag_id")
        conditions.append("t.value LIKE ?")
        params.append(f"%{tag}%")
    if venue:
        conditions.append("p.venue LIKE ?")
        params.append(f"%{venue}%")
    if year:
        conditions.append("p.year = ?")
        params.append(str(year))
    
    if joins:
        query += " " + " ".join(joins)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY p.year DESC, p.title ASC LIMIT ?"
    params.append(limit)
    
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                "paper_id": str(row["paper_id"]),
                "title": str(row["title"]),
                "year": str(row["year"]),
                "venue": str(row["venue"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


# ==================== MCP Resources ====================

@mcp.resource("paper://{paper_id}/metadata")
def resource_metadata(paper_id: str) -> str:
    """Resource: metadata as JSON string."""
    payload = get_paper_metadata(paper_id)
    return json.dumps(payload, ensure_ascii=False)


@mcp.resource("paper://{paper_id}/summary")
def resource_summary_default(paper_id: str) -> str:
    """Resource: preferred summary JSON string."""
    payload = get_paper_summary(paper_id)
    return payload  # Already a JSON string


@mcp.resource("paper://{paper_id}/summary/{template}")
def resource_summary_template(paper_id: str, template: str) -> str:
    """Resource: summary JSON string for a specific template."""
    payload = get_paper_summary(paper_id, template=template)
    return payload  # Already a JSON string


@mcp.resource("paper://{paper_id}/source")
def resource_source(paper_id: str) -> str:
    """Resource: source markdown text."""
    payload = get_paper_source(paper_id)
    return payload


@mcp.resource("paper://{paper_id}/translation/{lang}")
def resource_translation(paper_id: str, lang: str) -> str:
    """Resource: translated markdown text."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    
    try:
        content = _load_translation_markdown(paper_id, lang.lower())
    except RuntimeError as exc:
        raise McpToolError(
            "asset_fetch_failed",
            "Failed to fetch translation asset",
            paper_id=paper_id,
            lang=lang,
            detail=str(exc),
        ) from exc
    
    if content is None:
        raise McpToolError(
            "translation_not_available",
            "Translation not available",
            paper_id=paper_id,
            lang=lang,
        )
    
    return _truncate(content, cfg.max_chars_default)


def resolve_static_export_dir() -> Path | None:
    """Resolve static export directory from environment variable."""
    value = os.getenv("PAPER_DB_STATIC_EXPORT_DIR")
    if not value:
        return None
    return Path(value)
