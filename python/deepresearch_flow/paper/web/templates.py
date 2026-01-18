"""Jinja2 template utilities for paper web UI.

This module provides Jinja2 environment setup and template rendering functions.
Templates are stored in the 'templates' directory and use the PackageLoader
for installed package compatibility.
"""

from __future__ import annotations

from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, PackageLoader

from importlib import metadata

from deepresearch_flow import __version__
from deepresearch_flow.paper.web.constants import PDFJS_VIEWER_PATH, REPO_URL, TEMPLATES_DIR


def get_jinja_env() -> Environment:
    """Get a Jinja2 environment configured for web templates.

    Uses PackageLoader for installed packages (works after pip install).
    Falls back to FileSystemLoader for development mode.
    """
    try:
        # Try PackageLoader first (works in installed package)
        env = Environment(
            loader=PackageLoader("deepresearch_flow.paper.web", "templates"),
            autoescape=True,
        )
        return env
    except Exception:
        # Fallback to FileSystemLoader for development
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=True,
        )
        return env


# Global Jinja2 environment
_jinja_env = None


def get_template_env() -> Environment:
    """Get the shared Jinja2 environment for web handlers."""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = get_jinja_env()
    return _jinja_env


def render_template(template_name: str, **context) -> str:
    """Render a template with the given context.

    Args:
        template_name: Name of the template file (e.g., "detail.html")
        **context: Key-value pairs to pass to the template

    Returns:
        Rendered HTML string
    """
    env = get_template_env()
    try:
        resolved_version = metadata.version("deepresearch-flow")
    except metadata.PackageNotFoundError:
        resolved_version = __version__
    context.setdefault("app_version", resolved_version)
    context.setdefault("repo_url", REPO_URL)
    template = env.get_template(template_name)
    return template.render(**context)


def build_pdfjs_viewer_url(pdf_url: str, *, cdn_base_url: str | None = None) -> str:
    """Build a PDF.js viewer URL for the given PDF URL.

    Args:
        pdf_url: The URL of the PDF file
        cdn_base_url: Optional CDN base URL for PDF.js assets

    Returns:
        Full URL to the PDF.js viewer with the PDF file as a query parameter
    """
    params = {"file": pdf_url, "allow_origin": "1"}
    if cdn_base_url:
        params["cdn"] = cdn_base_url.rstrip("/")
    return f"{PDFJS_VIEWER_PATH}?{urlencode(params)}"
