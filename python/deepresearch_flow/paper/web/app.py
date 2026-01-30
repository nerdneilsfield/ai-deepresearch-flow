from __future__ import annotations

import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from deepresearch_flow.paper.db_ops import build_index, load_and_merge_papers
from deepresearch_flow.paper.web.constants import DEFAULT_PDFJS_CDN_BASE_URL, PDFJS_STATIC_DIR, STATIC_DIR
from deepresearch_flow.paper.web.handlers import (
    api_markdown,
    api_papers,
    api_pdf,
    api_stats,
    index_page,
    paper_detail,
    robots_txt,
    stats_page,
)
from deepresearch_flow.paper.web.markdown import create_md_renderer
from deepresearch_flow.paper.web.static_assets import build_static_assets

logger = logging.getLogger(__name__)


class _NoIndexMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noai, noimageai"
        return response


class _StaticAssetFiles(StaticFiles):
    def __init__(self, *args, cache_control: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cache_control = cache_control

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if self._cache_control and response.status_code == 200:
            response.headers.setdefault("Cache-Control", self._cache_control)
        return response


def _normalize_static_mode(value: str | None) -> str:
    if not value:
        return "auto"
    normalized = value.strip().lower()
    if normalized in {"dev", "development"}:
        return "dev"
    if normalized in {"prod", "production"}:
        return "prod"
    return "auto"


def _resolve_static_mode(value: str, static_base_url: str | None) -> str:
    if value == "auto":
        return "prod" if static_base_url else "dev"
    return value


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
    static_base_url: str | None = None,
    static_mode: str | None = None,
    static_export_dir: Path | None = None,
    pdfjs_cdn_base_url: str | None = None,
) -> Starlette:
    papers = load_and_merge_papers(db_paths, bibtex_path, cache_dir, use_cache, pdf_roots=pdf_roots)

    md_roots = md_roots or []
    md_translated_roots = md_translated_roots or []
    pdf_roots = pdf_roots or []
    index = build_index(
        papers,
        md_roots=md_roots,
        md_translated_roots=md_translated_roots,
        pdf_roots=pdf_roots,
    )
    md = create_md_renderer()
    static_base_url = static_base_url or os.getenv("PAPER_DB_STATIC_BASE") or os.getenv("PAPER_DB_STATIC_BASE_URL")
    static_mode = _normalize_static_mode(static_mode or os.getenv("PAPER_DB_STATIC_MODE"))
    resolved_mode = _resolve_static_mode(static_mode, static_base_url)
    export_dir_value = static_export_dir or os.getenv("PAPER_DB_STATIC_EXPORT_DIR")
    export_dir = Path(export_dir_value) if export_dir_value else None
    pdfjs_cdn_base_url = (
        pdfjs_cdn_base_url
        or os.getenv("PAPER_DB_PDFJS_CDN_BASE_URL")
        or DEFAULT_PDFJS_CDN_BASE_URL
    )
    if pdfjs_cdn_base_url:
        lowered = pdfjs_cdn_base_url.strip().lower()
        if lowered in {"none", "off", "local"}:
            pdfjs_cdn_base_url = None
        else:
            pdfjs_cdn_base_url = pdfjs_cdn_base_url.rstrip("/")

    asset_config = None
    if resolved_mode == "prod":
        if not static_base_url:
            logger.warning(
                "Static mode set to prod without base URL; falling back to dev asset routes "
                "(static_mode=%s, static_base_url=%s)",
                static_mode,
                static_base_url or "<empty>",
            )
            resolved_mode = "dev"
        else:
            asset_config = build_static_assets(
                index,
                static_base_url=static_base_url,
                static_export_dir=export_dir,
            )
    if resolved_mode == "dev" and export_dir:
        asset_config = build_static_assets(
            index,
            static_base_url="",
            static_export_dir=export_dir,
            allow_empty_base=True,
        )
    if asset_config is None:
        asset_config = build_static_assets(index, static_base_url=None)

    routes = [
        Route("/", index_page, methods=["GET"]),
        Route("/robots.txt", robots_txt, methods=["GET"]),
        Route("/stats", stats_page, methods=["GET"]),
        Route("/paper/{source_hash:str}", paper_detail, methods=["GET"]),
        Route("/api/papers", api_papers, methods=["GET"]),
        Route("/api/stats", api_stats, methods=["GET"]),
        Route("/api/pdf/{source_hash:str}", api_pdf, methods=["GET"]),
        Route("/api/dev/markdown/{source_hash:str}", api_markdown, methods=["GET"]),
    ]
    if PDFJS_STATIC_DIR.exists():
        routes.append(
            Mount(
                "/pdfjs",
                app=StaticFiles(directory=str(PDFJS_STATIC_DIR), html=True),
                name="pdfjs",
            )
        )
    elif pdf_roots:
        logger.warning(
            "PDF.js viewer assets not found at %s; PDF Viewer mode will be unavailable "
            "(pdf_roots=%d).",
            PDFJS_STATIC_DIR,
            len(pdf_roots),
        )
    if STATIC_DIR.exists():
        routes.append(
            Mount(
                "/static",
                app=StaticFiles(directory=str(STATIC_DIR)),
                name="static",
            )
        )
    if export_dir and export_dir.exists() and asset_config.enabled and not asset_config.base_url:
        cache_header = "public, max-age=31536000, immutable"
        routes.extend(
            [
                Mount(
                    "/pdf",
                    app=_StaticAssetFiles(directory=str(export_dir / "pdf"), cache_control=cache_header),
                    name="static_pdf",
                ),
                Mount(
                    "/images",
                    app=_StaticAssetFiles(directory=str(export_dir / "images"), cache_control=cache_header),
                    name="static_images",
                ),
                Mount(
                    "/md",
                    app=_StaticAssetFiles(directory=str(export_dir / "md"), cache_control=cache_header),
                    name="static_md",
                ),
                Mount(
                    "/md_translate",
                    app=_StaticAssetFiles(directory=str(export_dir / "md_translate"), cache_control=cache_header),
                    name="static_md_translate",
                ),
            ]
        )
    app = Starlette(routes=routes)
    app.add_middleware(_NoIndexMiddleware)
    app.state.index = index
    app.state.md = md
    app.state.fallback_language = fallback_language
    app.state.pdf_roots = pdf_roots
    app.state.static_mode = resolved_mode
    app.state.asset_config = asset_config
    app.state.static_export_dir = export_dir
    app.state.pdfjs_cdn_base_url = pdfjs_cdn_base_url
    return app
