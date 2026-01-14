from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from deepresearch_flow.paper.db_ops import build_index, load_and_merge_papers
from deepresearch_flow.paper.web.constants import PDFJS_STATIC_DIR, STATIC_DIR
from deepresearch_flow.paper.web.handlers import (
    api_papers,
    api_pdf,
    api_stats,
    index_page,
    paper_detail,
    robots_txt,
    stats_page,
)
from deepresearch_flow.paper.web.markdown import create_md_renderer

logger = logging.getLogger(__name__)


class _NoIndexMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noai, noimageai"
        return response


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
    routes = [
        Route("/", index_page, methods=["GET"]),
        Route("/robots.txt", robots_txt, methods=["GET"]),
        Route("/stats", stats_page, methods=["GET"]),
        Route("/paper/{source_hash:str}", paper_detail, methods=["GET"]),
        Route("/api/papers", api_papers, methods=["GET"]),
        Route("/api/stats", api_stats, methods=["GET"]),
        Route("/api/pdf/{source_hash:str}", api_pdf, methods=["GET"]),
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
            "PDF.js viewer assets not found at %s; PDF Viewer mode will be unavailable.",
            PDFJS_STATIC_DIR,
        )
    if STATIC_DIR.exists():
        routes.append(
            Mount(
                "/static",
                app=StaticFiles(directory=str(STATIC_DIR)),
                name="static",
            )
        )
    app = Starlette(routes=routes)
    app.add_middleware(_NoIndexMiddleware)
    app.state.index = index
    app.state.md = md
    app.state.fallback_language = fallback_language
    app.state.pdf_roots = pdf_roots
    return app
