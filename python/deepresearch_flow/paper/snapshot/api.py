from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
import logging
import sqlite3
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

from mcp.shared.auth import OAuthClientInformationFull
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from deepresearch_flow.paper.snapshot.common import (
    ApiLimits,
    _column_exists,
    _open_ro_conn,
    _table_exists,
)
from deepresearch_flow.paper.snapshot.text import (
    merge_adjacent_markers,
    remove_cjk_spaces,
    rewrite_search_query,
)

_WHITESPACE_RE = re.compile(r"\s+")
_MCP_CANONICAL_PATHS = {
    "/mcp": "/mcp/",
    "/mcp-sse": "/mcp-sse/",
    "/mcp-sse/messages": "/mcp-sse/messages/",
}
_LOGGER = logging.getLogger(__name__)


def _package_version() -> str:
    try:
        return package_version("deepresearch-flow")
    except PackageNotFoundError:
        return "unknown"


def _mask_value(value: str | None, *, keep_start: int = 4, keep_end: int = 4) -> str:
    text = str(value or "")
    if not text:
        return "<unset>"
    if len(text) <= keep_start + keep_end:
        return "<set>"
    return f"{text[:keep_start]}…{text[-keep_end:]}"


def _mask_url_origin(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "<unset>"
    try:
        from urllib.parse import urlparse

        parsed = urlparse(text)
    except Exception:
        return "<set>"
    host = parsed.hostname or ""
    if not host:
        return "<set>"
    labels = host.split(".")
    if len(labels) >= 2:
        masked_host = f"{_mask_value(labels[0], keep_start=2, keep_end=1)}.{'.'.join(labels[1:])}"
    else:
        masked_host = _mask_value(host, keep_start=2, keep_end=1)
    return f"{parsed.scheme}://{masked_host}" if parsed.scheme else masked_host


class _McpTrailingSlashMiddleware:
    """Route exact MCP mount paths without forcing clients through redirects."""

    def __init__(self, app, canonical_paths: dict[str, str] | None = None) -> None:
        self.app = app
        self.canonical_paths = canonical_paths or _MCP_CANONICAL_PATHS

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path")
        canonical = self.canonical_paths.get(path)
        if canonical is None:
            await self.app(scope, receive, send)
            return

        rewritten = dict(scope)
        rewritten["path"] = canonical
        rewritten["raw_path"] = canonical.encode("latin-1")
        await self.app(rewritten, receive, send)


class _ExactAsgiBridge:
    """Forward a single public route to an ASGI app without mounting a catch-all."""

    def __init__(self, app, *, forward_path: str | None = None) -> None:
        self.app = app
        self.forward_path = forward_path

    async def __call__(self, scope, receive, send) -> None:
        if self.forward_path is None or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        rewritten = dict(scope)
        rewritten["path"] = self.forward_path
        rewritten["raw_path"] = self.forward_path.encode("latin-1")
        await self.app(rewritten, receive, send)


class _OAuthAuthorizeResourceAliasBridge:
    """Normalize known ChatGPT OAuth resource aliases before FastMCP validation."""

    def __init__(
        self,
        app,
        *,
        resource_metadata_url: str,
        canonical_resource_url: str,
        oauth_provider: Any | None = None,
    ) -> None:
        self.app = app
        self.resource_metadata_url = resource_metadata_url.rstrip("/")
        self.canonical_resource_url = canonical_resource_url.rstrip("/")
        self.oauth_provider = oauth_provider

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_query = scope.get("query_string", b"")
        if not raw_query:
            await self.app(scope, receive, send)
            return

        query_text = raw_query.decode("latin-1")
        query_items = parse_qsl(query_text, keep_blank_values=True)
        changed = False
        rewritten_items: list[tuple[str, str]] = []
        for key, value in query_items:
            if key == "resource" and value.rstrip("/") == self.resource_metadata_url:
                rewritten_items.append((key, self.canonical_resource_url))
                changed = True
            else:
                rewritten_items.append((key, value))

        await self._ensure_client_registered(rewritten_items)

        if not changed:
            await self.app(scope, receive, send)
            return

        rewritten = dict(scope)
        rewritten["query_string"] = urlencode(rewritten_items).encode("latin-1")
        await self.app(rewritten, receive, send)

    async def _ensure_client_registered(self, query_items: list[tuple[str, str]]) -> None:
        if self.oauth_provider is None:
            return
        params = {key: value for key, value in query_items}
        client_id = str(params.get("client_id") or "").strip()
        redirect_uri = str(params.get("redirect_uri") or "").strip()
        if not client_id or not redirect_uri:
            return
        get_client = getattr(self.oauth_provider, "get_client", None)
        register_client = getattr(self.oauth_provider, "register_client", None)
        if get_client is None or register_client is None:
            return
        if await get_client(client_id) is not None:
            return
        _LOGGER.info(
            "OAuth client_id=%s missing from registry; synthesizing DCR client from authorize request",
            _mask_value(client_id),
        )
        client_info = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None,
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=params.get("scope") or "user",
            client_name="Recovered MCP OAuth Client",
        )
        await register_client(client_info)


class _OAuthTokenResourceBridge:
    """Normalize and validate OAuth token request resource indicators."""

    def __init__(
        self,
        app,
        *,
        resource_metadata_url: str,
        canonical_resource_url: str,
    ) -> None:
        self.app = app
        self.resource_metadata_url = resource_metadata_url.rstrip("/")
        self.canonical_resource_url = canonical_resource_url.rstrip("/")

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        body_parts: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                await self._replay(scope, [message], send)
                return
            body_parts.append(message.get("body", b""))
            more_body = bool(message.get("more_body", False))

        body = b"".join(body_parts)
        form_text = body.decode("latin-1")
        form_items = parse_qsl(form_text, keep_blank_values=True)
        changed = False
        invalid_resource = False
        rewritten_items: list[tuple[str, str]] = []

        for key, value in form_items:
            if key != "resource" or not value:
                rewritten_items.append((key, value))
                continue

            normalized = value.rstrip("/")
            if normalized == self.resource_metadata_url:
                rewritten_items.append((key, self.canonical_resource_url))
                changed = True
            elif normalized == self.canonical_resource_url:
                rewritten_items.append((key, value))
            else:
                rewritten_items.append((key, value))
                invalid_resource = True

        if invalid_resource:
            response = JSONResponse(
                {
                    "error": "invalid_target",
                    "error_description": "Resource does not match this server",
                },
                status_code=400,
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
            await response(scope, receive, send)
            return

        if not changed:
            await self._replay(
                scope, [{"type": "http.request", "body": body, "more_body": False}], send
            )
            return

        rewritten_body = urlencode(rewritten_items).encode("latin-1")
        rewritten_scope = dict(scope)
        rewritten_headers = []
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                rewritten_headers.append((key, str(len(rewritten_body)).encode("latin-1")))
            else:
                rewritten_headers.append((key, value))
        rewritten_scope["headers"] = rewritten_headers
        await self._replay(
            rewritten_scope,
            [{"type": "http.request", "body": rewritten_body, "more_body": False}],
            send,
        )

    async def _replay(self, scope, messages: list[dict[str, object]], send) -> None:
        remaining = list(messages)

        async def replay_receive() -> dict[str, object]:
            if remaining:
                return remaining.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


def _oauth_protocol_routes(oauth_app, *, public_base_url: str) -> list[Route]:
    """Expose only proven FastMCP/GitHub OAuth protocol routes at root."""

    canonical_resource_url = f"{public_base_url.rstrip('/')}/oauth/mcp"
    resource_metadata_url = (
        f"{public_base_url.rstrip('/')}/.well-known/oauth-protected-resource/oauth/mcp"
    )

    async def oauth_mcp_probe_challenge(request: Request) -> Response:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"'},
        )

    def bridge(
        path: str, *, forward_path: str | None = None, methods: list[str] | None = None
    ) -> Route:
        return Route(
            path,
            _ExactAsgiBridge(oauth_app, forward_path=forward_path),
            methods=methods,
        )

    return [
        Route("/oauth/mcp", oauth_mcp_probe_challenge, methods=["GET", "HEAD", "OPTIONS"]),
        Route("/oauth/mcp/", oauth_mcp_probe_challenge, methods=["GET", "HEAD", "OPTIONS"]),
        bridge("/oauth/mcp", methods=["POST", "DELETE"]),
        bridge("/oauth/mcp/", forward_path="/oauth/mcp", methods=["POST", "DELETE"]),
        bridge("/.well-known/oauth-protected-resource/oauth/mcp", methods=["GET", "OPTIONS"]),
        bridge("/.well-known/oauth-authorization-server", methods=["GET", "OPTIONS"]),
        bridge("/register", methods=["POST", "OPTIONS"]),
        Route(
            "/authorize",
            _OAuthAuthorizeResourceAliasBridge(
                oauth_app,
                resource_metadata_url=resource_metadata_url,
                canonical_resource_url=canonical_resource_url,
                oauth_provider=getattr(oauth_app, "_drflow_oauth_provider", None),
            ),
            methods=["GET", "POST"],
        ),
        Route(
            "/token",
            _OAuthTokenResourceBridge(
                oauth_app,
                resource_metadata_url=resource_metadata_url,
                canonical_resource_url=canonical_resource_url,
            ),
            methods=["POST", "OPTIONS"],
        ),
        bridge("/auth/callback", methods=["GET"]),
        bridge("/consent", methods=["GET", "POST"]),
    ]


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
class SnapshotApiConfig:
    snapshot_db: Path
    static_base_url: str
    cors_allowed_origins: list[str]
    limits: ApiLimits


def _normalize_base_url(value: str) -> str:
    return (value or "").rstrip("/")


def _json_error(status_code: int, *, error: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status_code)


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
        return _json_error(
            400, error="invalid_pagination", detail="page and page_size must be integers"
        )
    if page <= 0 or page_size <= 0:
        return _json_error(
            400, error="invalid_pagination", detail="page and page_size must be positive"
        )
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
                return JSONResponse(
                    {
                        "page": page,
                        "page_size": page_size,
                        "total": 0,
                        "has_more": False,
                        "items": [],
                    }
                )

            total_row = conn.execute(
                "SELECT COUNT(*) AS c FROM paper_fts WHERE paper_fts MATCH ?",
                (match_expr,),
            ).fetchone()
            total = int(total_row["c"]) if total_row else 0

            order_by = (
                "rank"
                if sort_key == "relevance"
                else _SEARCH_SORTS.get(sort_key, _SEARCH_SORTS["year_desc"])
            )
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
                authors = _list_facet_values(
                    conn,
                    paper_id=paper_id,
                    join_table="paper_author",
                    facet_table="author",
                    facet_id="author_id",
                )
                assets = _asset_urls(
                    static_base_url=cfg.static_base_url,
                    snapshot_build_id=build_id,
                    paper_id=paper_id,
                    pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                    source_md_hash=str(row["source_md_content_hash"])
                    if row["source_md_content_hash"]
                    else None,
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
                authors = _list_facet_values(
                    conn,
                    paper_id=paper_id,
                    join_table="paper_author",
                    facet_table="author",
                    facet_id="author_id",
                )
                assets = _asset_urls(
                    static_base_url=cfg.static_base_url,
                    snapshot_build_id=build_id,
                    paper_id=paper_id,
                    pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                    source_md_hash=str(row["source_md_content_hash"])
                    if row["source_md_content_hash"]
                    else None,
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
        return JSONResponse(
            {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": has_more,
                "items": items,
            }
        )
    finally:
        conn.close()


async def _api_paper_detail(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    paper_id = str(request.path_params["paper_id"])
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        build_id = _snapshot_build_id(conn)
        has_doi_column = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi_column else "NULL AS doi"
        row = conn.execute(
            f"""
            SELECT paper_id, title, year, venue, preferred_summary_template,
                   {doi_select},
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
            source_md_hash=str(row["source_md_content_hash"])
            if row["source_md_content_hash"]
            else None,
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

        authors = _list_facet_values(
            conn,
            paper_id=paper_id,
            join_table="paper_author",
            facet_table="author",
            facet_id="author_id",
        )
        keywords = _list_facet_values(
            conn,
            paper_id=paper_id,
            join_table="paper_keyword",
            facet_table="keyword",
            facet_id="keyword_id",
        )
        institutions = _list_facet_values(
            conn,
            paper_id=paper_id,
            join_table="paper_institution",
            facet_table="institution",
            facet_id="institution_id",
        )
        tags = _list_facet_values(
            conn, paper_id=paper_id, join_table="paper_tag", facet_table="tag", facet_id="tag_id"
        )

        return JSONResponse(
            {
                "paper_id": paper_id,
                "title": str(row["title"]),
                "year": str(row["year"]),
                "venue": str(row["venue"]),
                "doi": str(row["doi"]) if row["doi"] else None,
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


async def _api_paper_bibtex(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    paper_id = str(request.path_params["paper_id"])
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        has_doi_column = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi_column else "NULL AS doi"
        paper_row = conn.execute(
            f"SELECT paper_id, {doi_select} FROM paper WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if not paper_row:
            return _json_error(404, error="paper_not_found", detail="paper not found")

        if not _table_exists(conn, "paper_bibtex"):
            return _json_error(404, error="bibtex_not_found", detail="bibtex not found")

        bib_row = conn.execute(
            """
            SELECT bibtex_raw, bibtex_key, entry_type
            FROM paper_bibtex
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not bib_row:
            return _json_error(404, error="bibtex_not_found", detail="bibtex not found")

        return JSONResponse(
            {
                "paper_id": paper_id,
                "doi": str(paper_row["doi"]) if paper_row["doi"] else None,
                "bibtex_raw": str(bib_row["bibtex_raw"]),
                "bibtex_key": str(bib_row["bibtex_key"]) if bib_row["bibtex_key"] else None,
                "entry_type": str(bib_row["entry_type"]) if bib_row["entry_type"] else None,
            }
        )
    finally:
        conn.close()


async def _api_match_bibtex(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    try:
        body = await request.json()
    except Exception:
        return _json_error(400, error="invalid_json", detail="request body must be valid JSON")

    if not isinstance(body, dict):
        return _json_error(400, error="invalid_json", detail="request body must be a JSON object")

    bibtex_raw = body.get("bibtex_raw")
    if bibtex_raw is None:
        return _json_error(400, error="missing_field", detail="bibtex_raw is required")
    if not isinstance(bibtex_raw, str):
        return _json_error(400, error="invalid_field", detail="bibtex_raw must be a string")

    from deepresearch_flow.paper.snapshot.bibtex_match import (
        match_bibtex_entries,
        _parse_bibtex_entries,
    )

    # Enforce batch size limit (spec: up to 50 entries per request)
    entries = _parse_bibtex_entries(bibtex_raw)
    if len(entries) > 50:
        return _json_error(
            400, error="too_many_entries", detail=f"batch limit is 50 entries, got {len(entries)}"
        )

    result = match_bibtex_entries(bibtex_raw, cfg.snapshot_db)

    return JSONResponse(
        {
            "matched": [
                {
                    "bibtex_key": m.bibtex_key,
                    "paper_id": m.paper_id,
                    "match_method": m.match_method,
                    "title": m.title,
                    "year": m.year,
                    "venue": m.venue,
                    "authors": m.authors,
                }
                for m in result.matched
            ],
            "unmatched": [
                {
                    "bibtex_key": u.bibtex_key,
                    "title": u.title,
                    "search_query": u.search_query,
                }
                for u in result.unmatched
            ],
            "stats": {
                "total": len(result.matched) + len(result.unmatched),
                "matched": len(result.matched),
                "unmatched": len(result.unmatched),
            },
        }
    )


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
            items = [
                {"id": str(r["id"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])}
                for r in rows
            ]
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
            items = [
                {"id": str(r["id"]), "value": str(r["value"]), "paper_count": int(r["paper_count"])}
                for r in rows
            ]
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
                    {
                        "id": int(r["id"]),
                        "value": str(r["value"]),
                        "paper_count": int(r["paper_count"]),
                    }
                    for r in rows
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
                    {
                        "id": str(r["value"]),
                        "value": str(r["value"]),
                        "paper_count": int(r["paper_count"]),
                    }
                    for r in rows
                ]

        has_more = (page * page_size) < total and bool(items)
        return JSONResponse(
            {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": has_more,
                "items": items,
            }
        )
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
            total_row = conn.execute(
                "SELECT paper_count AS c FROM year_count WHERE year = ?", (facet_id,)
            ).fetchone()
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
            authors = _list_facet_values(
                conn,
                paper_id=paper_id,
                join_table="paper_author",
                facet_table="author",
                facet_id="author_id",
            )
            assets = _asset_urls(
                static_base_url=cfg.static_base_url,
                snapshot_build_id=build_id,
                paper_id=paper_id,
                pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                source_md_hash=str(row["source_md_content_hash"])
                if row["source_md_content_hash"]
                else None,
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
        return JSONResponse(
            {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": has_more,
                "items": items,
            }
        )
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


def _facet_stats_for_node(
    conn: sqlite3.Connection, *, facet_type: str, value: str
) -> dict[str, Any]:
    node_id = _facet_node_id(conn, facet_type, value)
    related: dict[str, list[dict[str, Any]]] = {key: [] for key in _FACET_TYPE_TO_KEY.values()}
    if node_id is None:
        return {
            "facet_type": facet_type,
            "value": _normalize_facet_value(value),
            "total": 0,
            "related": related,
        }

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
            return JSONResponse(
                {"page": page, "page_size": page_size, "total": 0, "has_more": False, "items": []}
            )

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
            authors = _list_facet_values(
                conn,
                paper_id=paper_id,
                join_table="paper_author",
                facet_table="author",
                facet_id="author_id",
            )
            assets = _asset_urls(
                static_base_url=cfg.static_base_url,
                snapshot_build_id=build_id,
                paper_id=paper_id,
                pdf_hash=str(row["pdf_content_hash"]) if row["pdf_content_hash"] else None,
                source_md_hash=str(row["source_md_content_hash"])
                if row["source_md_content_hash"]
                else None,
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
        return JSONResponse(
            {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": has_more,
                "items": items,
            }
        )
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
                "years": [
                    {"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in years
                ],
                "months": [
                    {"value": str(r["value"]), "paper_count": int(r["paper_count"])} for r in months
                ],
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
    mcp_access_token: str | None = None,
    mcp_auth_mode: str = "static",
    mcp_public_base_url: str | None = None,
    github_oauth_client_id: str | None = None,
    github_oauth_client_secret: str | None = None,
    mcp_github_allowed_user_ids: list[str] | None = None,
    mcp_oauth_client_cache_path: Path | None = None,
    admin_token: str | None = None,
    admin_embed_db: Path | None = None,
    admin_embed_dimensions: int | None = None,
    advanced_config: Any | None = None,
) -> Starlette:
    cfg = SnapshotApiConfig(
        snapshot_db=snapshot_db,
        static_base_url=_normalize_base_url(static_base_url),
        cors_allowed_origins=cors_allowed_origins or ["*"],
        limits=limits or ApiLimits(),
    )

    # Lazy import to avoid circular dependency
    from deepresearch_flow.paper.snapshot.auth import (
        McpGitHubOAuthConfig,
        validate_mcp_static_access_token,
    )
    from deepresearch_flow.paper.snapshot.mcp_server import (
        McpSnapshotConfig,
        create_mcp_apps,
        resolve_static_export_dir,
    )

    if mcp_auth_mode not in {"static", "github-oauth"}:
        raise ValueError("mcp_auth_mode must be 'static' or 'github-oauth'")
    validate_mcp_static_access_token(mcp_access_token, context="/mcp and /mcp-sse")

    mcp_github_oauth = None
    if mcp_auth_mode == "github-oauth":
        mcp_github_oauth = McpGitHubOAuthConfig(
            public_base_url=mcp_public_base_url or "",
            client_id=github_oauth_client_id or "",
            client_secret=github_oauth_client_secret or "",
            allowed_github_user_ids=tuple(mcp_github_allowed_user_ids or ()),
            client_cache_path=mcp_oauth_client_cache_path,
        )

    mcp_config = McpSnapshotConfig(
        snapshot_db=snapshot_db,
        static_base_url=_normalize_base_url(static_base_url),
        static_export_dir=resolve_static_export_dir(),
        limits=limits or ApiLimits(),
        origin_allowlist=cors_allowed_origins or ["*"],
        advanced_config=advanced_config,
        mcp_access_token=mcp_access_token,
        mcp_auth_mode=mcp_auth_mode,
        mcp_github_oauth=mcp_github_oauth,
    )
    _LOGGER.info(
        "Starting deepresearch-flow API version=%s mcp_auth_mode=%s static_mcp_token=%s "
        "oauth_public_base=%s github_client_id=%s allowed_github_user_count=%d "
        "oauth_client_cache=%s oauth_client_cache_exists=%s",
        _package_version(),
        mcp_auth_mode,
        "set" if mcp_access_token else "unset",
        _mask_url_origin(mcp_public_base_url),
        _mask_value(github_oauth_client_id),
        len(mcp_github_allowed_user_ids or []),
        str(mcp_oauth_client_cache_path) if mcp_oauth_client_cache_path else "<default>",
        bool(mcp_oauth_client_cache_path and Path(mcp_oauth_client_cache_path).exists()),
    )
    mcp_apps, mcp_lifespan = create_mcp_apps(mcp_config)

    routes = [
        Route("/api/v1/config", _api_config, methods=["GET"]),
        Route("/api/v1/search", _api_search, methods=["GET"]),
        Route("/api/v1/stats", _api_stats, methods=["GET"]),
        Route("/api/v1/papers/match-bibtex", _api_match_bibtex, methods=["POST"]),
        Route("/api/v1/papers/{paper_id:str}", _api_paper_detail, methods=["GET"]),
        Route("/api/v1/papers/{paper_id:str}/bibtex", _api_paper_bibtex, methods=["GET"]),
        Route("/api/v1/facets/{facet:str}", _api_facet_list, methods=["GET"]),
        Route(
            "/api/v1/facets/{facet:str}/{facet_id:str}/papers", _api_facet_papers, methods=["GET"]
        ),
        Route("/api/v1/facets/{facet:str}/{facet_id:str}/stats", _api_facet_stats, methods=["GET"]),
        Route(
            "/api/v1/facets/{facet:str}/by-value/{value:str}/papers",
            _api_facet_by_value_papers,
            methods=["GET"],
        ),
        Route(
            "/api/v1/facets/{facet:str}/by-value/{value:str}/stats",
            _api_facet_by_value_stats,
            methods=["GET"],
        ),
    ]
    routes.append(Mount("/mcp", app=mcp_apps["bearer-streamable-http"]))
    routes.append(Mount("/mcp-sse", app=mcp_apps["bearer-sse"]))

    if admin_token:
        from deepresearch_flow.paper.snapshot.admin import create_admin_app

        admin_app = create_admin_app(
            snapshot_db=snapshot_db,
            admin_token=admin_token,
            embed_db=admin_embed_db,
            embed_dimensions=admin_embed_dimensions,
        )
        routes.append(Mount("/api/v1/admin", app=admin_app))

    if advanced_config is not None:
        from deepresearch_flow.paper.snapshot.advanced import create_advanced_routes

        routes.extend(create_advanced_routes(advanced_config))

    if mcp_auth_mode == "github-oauth":
        routes.extend(
            _oauth_protocol_routes(
                mcp_apps["oauth-streamable-http"],
                public_base_url=mcp_github_oauth.public_base_url if mcp_github_oauth else "",
            )
        )

    # Pass MCP lifespan to ensure session manager initializes properly
    # https://gofastmcp.com/deployment/http#mounting-in-starlette
    app = Starlette(
        routes=routes,
        lifespan=mcp_lifespan,
    )
    app.add_middleware(_McpTrailingSlashMiddleware)
    if cfg.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_allowed_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.cfg = cfg
    if advanced_config is not None:
        app.state.advanced = advanced_config
    return app
