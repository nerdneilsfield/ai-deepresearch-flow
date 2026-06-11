from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Literal
import uuid

import httpx

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as FastMCPToolError

from deepresearch_flow.paper.snapshot.auth import (
    McpGitHubOAuthConfig,
    bearer_auth_app,
    build_mcp_github_oauth_provider,
    oauth_metadata_compat_app,
    validate_mcp_static_access_token,
)
from deepresearch_flow.paper.snapshot.common import ApiLimits, _column_exists, _open_ro_conn, _table_exists
from deepresearch_flow.paper.snapshot.mcp_content import (
    DEFAULT_MAX_CHARS as _DEFAULT_MAX_CHARS,
    MarkdownContentError,
    SummaryContentError,
    get_markdown_line_range as _get_markdown_line_range,
    get_markdown_outline as _get_markdown_outline,
    get_summary_key as _get_summary_key,
    get_summary_keys as _get_summary_keys,
)
from deepresearch_flow.paper.snapshot.text import merge_adjacent_markers, remove_cjk_spaces, rewrite_search_query
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_CONTENT_MAX_CHARS = 8_000
_PAPER_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


class McpToolError(FastMCPToolError):
    """Backward-compatible MCP tool error with code/details fields."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


@dataclass(frozen=True)
class McpSnapshotConfig:
    snapshot_db: Path
    static_base_url: str
    static_export_dir: Path | None
    limits: ApiLimits
    origin_allowlist: list[str]
    advanced_config: Any | None = None
    mcp_access_token: str | None = None
    mcp_auth_mode: Literal["static", "github-oauth"] = "static"
    mcp_github_oauth: McpGitHubOAuthConfig | None = None
    max_chars_default: int = _DEFAULT_MAX_CHARS
    http_timeout: float = _DEFAULT_TIMEOUT
    max_paper_id_length: int = 64
    # HTTP client stored in object __dict__ to avoid dataclass frozen restriction
    _http_client: httpx.Client | None = field(default=None, repr=False, compare=False)
    _http_client_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    
    def get_http_client(self) -> httpx.Client:
        """Get or create a shared HTTP client with connection pooling."""
        with self._http_client_lock:
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

    def close_http_client(self) -> None:
        """Close and clear the lazily-created shared HTTP client if present."""
        with self._http_client_lock:
            client = self._http_client
            if client is not None:
                client.close()
                object.__setattr__(self, "_http_client", None)



_CONFIG: McpSnapshotConfig | None = None
_REQUEST_CONFIG: ContextVar[McpSnapshotConfig | None] = ContextVar(
    "paper_db_mcp_request_config",
    default=None,
)
_MCP_AUTH_BINDING_LOCK = threading.Lock()
mcp = FastMCP("Paper DB MCP")


def configure(config: McpSnapshotConfig) -> None:
    global _CONFIG
    _CONFIG = config


def _allowed_methods_for_transport(transport: Literal["streamable-http", "sse"]) -> set[str]:
    if transport == "sse":
        return {"GET", "POST", "OPTIONS"}
    return {"POST", "OPTIONS"}


def _bind_mcp_config_app(app, config: McpSnapshotConfig):
    """Bind a specific MCP config to an ASGI app per request."""

    async def wrapped(scope, receive, send):
        token = _REQUEST_CONFIG.set(config)
        try:
            await app(scope, receive, send)
        finally:
            _REQUEST_CONFIG.reset(token)

    # Preserve common ASGI app metadata for route inspection and transport helpers.
    wrapped.routes = getattr(app, "routes", None)
    wrapped.router = getattr(app, "router", None)
    return wrapped


def create_mcp_transport_app(
    config: McpSnapshotConfig,
    *,
    transport: Literal["streamable-http", "sse"] = "streamable-http",
) -> tuple[Any, Any]:
    validate_mcp_static_access_token(config.mcp_access_token, context=f"/{transport}")
    if config.mcp_auth_mode == "github-oauth":
        raise ValueError("GitHub OAuth requires create_mcp_apps() so /mcp and OAuth routes share one app")
    bound_app, transport_lifespan = _create_mcp_transport_binding(config, transport=transport)

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with transport_lifespan(app):
                yield
        finally:
            config.close_http_client()

    return bound_app, lifespan


def _create_mcp_transport_binding(
    config: McpSnapshotConfig,
    *,
    transport: Literal["streamable-http", "sse"],
    path: str = "/",
    auth_provider: Any | None = None,
    static_bearer: bool = True,
) -> tuple[Any, Any]:
    """Create MCP app for a specific transport following FastMCP 3.0 best practices.

    See: https://gofastmcp.com/deployment/running-server
    """
    # Use stateless_http=True for optimal scalability with streamable-http transport.
    # FastMCP expands auth routes when http_app() is called, so bind auth only
    # while creating this transport app.
    with _MCP_AUTH_BINDING_LOCK:
        previous_auth = mcp.auth
        mcp.auth = auth_provider
        try:
            mcp_app = mcp.http_app(
                path=path,
                transport=transport,
                stateless_http=(transport == "streamable-http"),
                json_response=True,
            )
        finally:
            mcp.auth = previous_auth

    if auth_provider is not None:
        mcp_app = oauth_metadata_compat_app(mcp_app)

    bound_app = _bind_mcp_config_app(mcp_app, config)
    if static_bearer:
        bound_app = bearer_auth_app(bound_app, config.mcp_access_token)

    @asynccontextmanager
    async def transport_lifespan(app):
        async with mcp_app.lifespan(app):
            yield

    return bound_app, transport_lifespan


def create_mcp_apps(config: McpSnapshotConfig) -> tuple[dict[str, Any], Any]:
    """Create streamable-http and sse MCP apps.

    Returns:
        A tuple of (apps_by_transport, lifespan_context).
    """
    validate_mcp_static_access_token(config.mcp_access_token, context="/mcp and /mcp-sse")
    if config.mcp_auth_mode == "github-oauth":
        if config.mcp_github_oauth is None:
            raise ValueError("GitHub OAuth config is required when mcp_auth_mode='github-oauth'")
        oauth_provider = build_mcp_github_oauth_provider(
            config.mcp_github_oauth,
            static_access_token=config.mcp_access_token,
        )
        streamable_app, streamable_lifespan = _create_mcp_transport_binding(
            config,
            transport="streamable-http",
            path="/mcp",
            auth_provider=oauth_provider,
            static_bearer=False,
        )
    else:
        streamable_app, streamable_lifespan = _create_mcp_transport_binding(
            config,
            transport="streamable-http",
        )

    sse_app, sse_lifespan = _create_mcp_transport_binding(
        config,
        transport="sse",
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with streamable_lifespan(app):
                async with sse_lifespan(app):
                    yield
        finally:
            config.close_http_client()

    return {"streamable-http": streamable_app, "sse": sse_app}, lifespan


def create_mcp_app(config: McpSnapshotConfig) -> tuple[Any, Any]:
    """Backward-compatible helper returning streamable-http MCP app."""
    return create_mcp_transport_app(config, transport="streamable-http")


def _get_config() -> McpSnapshotConfig:
    config = _REQUEST_CONFIG.get() or _CONFIG
    if config is None:
        raise RuntimeError("MCP server not configured")
    return config


def _validate_query(query: str, cfg: McpSnapshotConfig) -> str:
    """Validate search query string.
    
    Raises:
        ToolError: If query is invalid or too long.
    """
    normalized = query.strip() if query else ""
    if not normalized:
        raise McpToolError("invalid_query", "Query cannot be empty")
    if len(normalized) > cfg.limits.max_query_length:
        raise McpToolError(
            "query_too_long",
            f"Query exceeds maximum length of {cfg.limits.max_query_length}",
            length=len(normalized),
            max_length=cfg.limits.max_query_length
        )
    return normalized


def _parse_limit(limit: Any, cfg: McpSnapshotConfig) -> int:
    if type(limit) is not int or limit < 1:
        raise McpToolError("invalid_limit", "Limit must be a positive integer", limit=limit)
    limit_value = limit
    return min(max(1, limit_value), cfg.limits.max_page_size)


def _normalize_advanced_filter_params(filters: dict[str, Any] | None) -> dict[str, list[str]]:
    if not filters:
        return {}
    params: dict[str, list[str]] = {}
    for raw_key, raw_value in filters.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized_key = key if key.startswith("filters.") else f"filters.{key}"
        if isinstance(raw_value, list):
            values: list[str] = []
            for item in raw_value:
                if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                    raise ValueError(
                        f"Filter '{normalized_key}' must use string or numeric values"
                    )
                text = str(item).strip()
                if text:
                    values.append(text)
        else:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
                raise ValueError(
                    f"Filter '{normalized_key}' must use string or numeric values"
                )
            value = str(raw_value).strip()
            values = [value] if value else []
        if values:
            params[normalized_key] = values
    return params


def _validate_paper_id(paper_id: str, cfg: McpSnapshotConfig) -> str:
    """Validate paper ID format.
    
    Raises:
        ToolError: If paper_id is invalid.
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
    """Truncate text while preserving the legacy truncation marker."""
    if max_chars is None or len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    return f"{text[:max_chars]}\n[truncated: {remaining} more chars]"


def _resolve_content_max_chars(cfg: McpSnapshotConfig, max_chars: int | None) -> int | None:
    """Resolve the effective max_chars ceiling for server content reads."""
    if max_chars is not None:
        if max_chars <= 0:
            raise McpToolError(
                "invalid_max_chars",
                "max_chars must be a positive integer",
                max_chars=max_chars,
            )
        return max_chars
    return min(cfg.max_chars_default, _DEFAULT_CONTENT_MAX_CHARS)


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


def _load_summary_json(paper_id: str, template: str | None) -> tuple[str | None, list[str] | None, str | None]:
    """Load summary JSON content and return available templates list."""
    cfg = _get_config()
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        row = conn.execute(
            "SELECT preferred_summary_template FROM paper WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not row:
            return None, None, None
        preferred = str(row["preferred_summary_template"] or "")
        template_rows = conn.execute(
            "SELECT template_tag FROM paper_summary WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        available = sorted((str(item["template_tag"]) for item in template_rows), key=str.lower)
        selected = (template or preferred).strip()
        if not selected or selected not in set(available):
            return None, available, selected or None
        if template:
            rel_path = f"summary/{paper_id}/{selected}.json"
        else:
            rel_path = f"summary/{paper_id}.json"
        return _load_static_text(rel_path), available, selected
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
    limit = _parse_limit(limit, cfg)
    
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
    limit = _parse_limit(limit, cfg)
    
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
        has_doi_column = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi_column else "NULL AS doi"
        row = conn.execute(
            f"""
            SELECT paper_id, title, year, venue, preferred_summary_template, {doi_select}
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
        has_bibtex = False
        if _table_exists(conn, "paper_bibtex"):
            bib_row = conn.execute(
                "SELECT 1 FROM paper_bibtex WHERE paper_id = ? LIMIT 1",
                (paper_id,),
            ).fetchone()
            has_bibtex = bib_row is not None
        return {
            "paper_id": str(row["paper_id"]),
            "title": str(row["title"]),
            "year": str(row["year"]),
            "venue": str(row["venue"]),
            "doi": str(row["doi"]) if row["doi"] else None,
            "arxiv_id": None,
            "openreview_id": None,
            "paper_pw_url": None,
            "preferred_summary_template": row["preferred_summary_template"],
            "available_summary_templates": available,
            "has_bibtex": has_bibtex,
        }
    finally:
        conn.close()


@mcp.tool()
def get_paper_bibtex(paper_id: str) -> dict[str, Any]:
    """Get persisted BibTeX payload for a paper.

    Returns canonical DOI from paper metadata and BibTeX entry text when available.
    """
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        has_doi_column = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi_column else "NULL AS doi"
        paper_row = conn.execute(
            f"SELECT paper_id, {doi_select} FROM paper WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not paper_row:
            raise McpToolError("paper_not_found", "paper not found", paper_id=paper_id)

        if not _table_exists(conn, "paper_bibtex"):
            raise McpToolError("bibtex_not_found", "bibtex not found", paper_id=paper_id)

        bib_row = conn.execute(
            "SELECT bibtex_raw, bibtex_key, entry_type FROM paper_bibtex WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not bib_row:
            raise McpToolError("bibtex_not_found", "bibtex not found", paper_id=paper_id)

        return {
            "paper_id": paper_id,
            "doi": str(paper_row["doi"]) if paper_row["doi"] else None,
            "bibtex_raw": str(bib_row["bibtex_raw"]),
            "bibtex_key": str(bib_row["bibtex_key"]) if bib_row["bibtex_key"] else None,
            "entry_type": str(bib_row["entry_type"]) if bib_row["entry_type"] else None,
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
    max_chars = _resolve_content_max_chars(cfg, max_chars)
    
    try:
        payload, available, _ = _load_summary_json(paper_id, template)
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
def get_paper_summary_keys(
    paper_id: str,
    template: str | None = None,
    max_depth: int = 2,
    include_preview: bool = False,
) -> dict[str, Any]:
    """Get recursive summary key paths in document order."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)

    try:
        payload, available, selected_template = _load_summary_json(paper_id, template)
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

    try:
        content = _get_summary_keys(
            payload,
            max_depth=max(0, int(max_depth)),
            include_preview=bool(include_preview),
        )
    except SummaryContentError as exc:
        details = {"paper_id": paper_id, "template": selected_template}
        details.update(exc.details)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        "template": selected_template,
        **content,
    }


@mcp.tool()
def get_paper_summary_key(
    paper_id: str,
    key: str,
    template: str | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Get a single addressed summary node."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    max_chars = _resolve_content_max_chars(cfg, max_chars)

    try:
        payload, available, selected_template = _load_summary_json(paper_id, template)
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

    try:
        content = _get_summary_key(payload, key, max_chars=max_chars)
    except SummaryContentError as exc:
        details = {"paper_id": paper_id, "template": selected_template}
        details.update(exc.details)
        details.setdefault("key", key)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        "template": selected_template,
        **content,
    }


@mcp.tool()
def get_paper_source(paper_id: str, max_chars: int | None = None) -> str:
    """Get source markdown text.
    
    Content may be large; use max_chars to limit size.
    """
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    max_chars = _resolve_content_max_chars(cfg, max_chars)
    
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
def get_paper_source_outline(paper_id: str) -> dict[str, Any]:
    """Get the source markdown outline as section ranges."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)

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
            paper_id=paper_id,
        )

    try:
        outline = _get_markdown_outline(content)
    except MarkdownContentError as exc:
        details = {"paper_id": paper_id}
        details.update(exc.details)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        **outline,
    }


@mcp.tool()
def get_paper_source_lines(paper_id: str, start_line: int, end_line: int) -> dict[str, Any]:
    """Get a 1-based inclusive slice of the source markdown."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)

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
            paper_id=paper_id,
        )

    try:
        slice_payload = _get_markdown_line_range(content, start_line, end_line)
    except MarkdownContentError as exc:
        details = {
            "paper_id": paper_id,
            "start_line": start_line,
            "end_line": end_line,
        }
        details.update(exc.details)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        **slice_payload,
    }


@mcp.tool()
def get_paper_translation_outline(paper_id: str, lang: str) -> dict[str, Any]:
    """Get the translated markdown outline as section ranges."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    normalized_lang = (lang or "").strip().lower()

    try:
        content = _load_translation_markdown(paper_id, normalized_lang)
    except RuntimeError as exc:
        raise McpToolError(
            "asset_fetch_failed",
            "Failed to fetch translation asset",
            paper_id=paper_id,
            lang=normalized_lang,
            detail=str(exc),
        ) from exc

    if content is None:
        raise McpToolError(
            "translation_not_available",
            "Translation not available",
            paper_id=paper_id,
            lang=normalized_lang,
        )

    try:
        outline = _get_markdown_outline(content)
    except MarkdownContentError as exc:
        details = {
            "paper_id": paper_id,
            "lang": normalized_lang,
        }
        details.update(exc.details)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        "lang": normalized_lang,
        **outline,
    }


@mcp.tool()
def get_paper_translation_lines(
    paper_id: str,
    lang: str,
    start_line: int,
    end_line: int,
) -> dict[str, Any]:
    """Get a 1-based inclusive slice of the translated markdown."""
    cfg = _get_config()
    paper_id = _validate_paper_id(paper_id, cfg)
    normalized_lang = (lang or "").strip().lower()

    try:
        content = _load_translation_markdown(paper_id, normalized_lang)
    except RuntimeError as exc:
        raise McpToolError(
            "asset_fetch_failed",
            "Failed to fetch translation asset",
            paper_id=paper_id,
            lang=normalized_lang,
            detail=str(exc),
        ) from exc

    if content is None:
        raise McpToolError(
            "translation_not_available",
            "Translation not available",
            paper_id=paper_id,
            lang=normalized_lang,
        )

    try:
        slice_payload = _get_markdown_line_range(content, start_line, end_line)
    except MarkdownContentError as exc:
        details = {
            "paper_id": paper_id,
            "lang": normalized_lang,
            "start_line": start_line,
            "end_line": end_line,
        }
        details.update(exc.details)
        raise McpToolError(exc.code, exc.message, **details) from exc

    return {
        "paper_id": paper_id,
        "lang": normalized_lang,
        **slice_payload,
    }


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
    
    cfg = _get_config()
    limit = _parse_limit(limit, cfg)
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
    limit = _parse_limit(limit, cfg)
    
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


@mcp.tool()
async def search_papers_semantic(
    query: str,
    top_n: int = 10,
    mmr_lambda: float | None = None,
    rerank: str = "auto",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full advanced semantic search pipeline and return its payload."""
    cfg = _get_config()
    ctx = cfg.advanced_config
    if ctx is None:
        raise McpToolError(
            "advanced_search_not_available",
            "Advanced semantic search is not configured for this MCP server",
        )

    # Deferred imports keep MCP startup light when advanced search is disabled.
    from deepresearch_flow.paper.snapshot.advanced.errors import AdvancedSearchError
    from deepresearch_flow.paper.snapshot.advanced.pipeline import run_advanced_search
    from deepresearch_flow.paper.snapshot.advanced.request_spec import build_request_spec

    trace_id = uuid.uuid4().hex
    search_cfg = ctx.search_config
    try:
        request_spec = build_request_spec(
            query_raw=query,
            top_n=top_n,
            mmr_lambda=(
                search_cfg.advanced_mmr_lambda_default
                if mmr_lambda is None
                else mmr_lambda
            ),
            rerank_mode=rerank,
            filter_params=_normalize_advanced_filter_params(filters),
            trace_id=trace_id,
            search_cfg=search_cfg,
        )
    except AdvancedSearchError as exc:
        raise McpToolError(exc.code.lower(), str(exc), trace_id=trace_id) from exc
    except ValueError as exc:
        raise McpToolError("invalid_query", str(exc), trace_id=trace_id) from exc

    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        async with httpx.AsyncClient() as client:
            try:
                return await run_advanced_search(
                    request_spec=request_spec,
                    ctx=ctx,
                    conn=conn,
                    client=client,
                )
            except AdvancedSearchError as exc:
                raise McpToolError(exc.code.lower(), str(exc), trace_id=trace_id) from exc
    finally:
        conn.close()


# ==================== MCP Resources ====================

@mcp.resource(
    "paper://{paper_id}/metadata",
    description="Get paper metadata including title, authors, year, venue, DOI, and available summary templates",
    mime_type="application/json"
)
def resource_metadata(paper_id: str) -> str:
    """Resource: metadata as JSON string."""
    payload = get_paper_metadata(paper_id)
    return json.dumps(payload, ensure_ascii=False)


@mcp.resource(
    "paper://{paper_id}/summary",
    description="Get paper summary using the preferred template as JSON",
    mime_type="application/json"
)
def resource_summary_default(paper_id: str) -> str:
    """Resource: preferred summary JSON string."""
    payload = get_paper_summary(paper_id)
    return payload  # Already a JSON string


@mcp.resource(
    "paper://{paper_id}/summary/{template}",
    description="Get paper summary using a specific template as JSON",
    mime_type="application/json"
)
def resource_summary_template(paper_id: str, template: str) -> str:
    """Resource: summary JSON string for a specific template."""
    payload = get_paper_summary(paper_id, template=template)
    return payload  # Already a JSON string


@mcp.resource(
    "paper://{paper_id}/source",
    description="Get the source markdown content of the paper",
    mime_type="text/markdown"
)
def resource_source(paper_id: str) -> str:
    """Resource: source markdown text."""
    payload = get_paper_source(paper_id)
    return payload


@mcp.resource(
    "paper://{paper_id}/translation/{lang}",
    description="Get the translated markdown content of the paper in the specified language",
    mime_type="text/markdown"
)
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
    
    return _truncate(content, _resolve_content_max_chars(cfg, None))


def resolve_static_export_dir() -> Path | None:
    """Resolve static export directory from environment variable."""
    value = os.getenv("PAPER_DB_STATIC_EXPORT_DIR")
    if not value:
        return None
    return Path(value)
