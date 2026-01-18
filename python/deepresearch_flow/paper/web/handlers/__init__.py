"""Route handlers for paper web UI."""

from .api import api_markdown, api_papers, api_pdf, api_stats
from .pages import index_page, paper_detail, robots_txt, stats_page

__all__ = [
    "api_papers",
    "api_pdf",
    "api_stats",
    "api_markdown",
    "index_page",
    "paper_detail",
    "robots_txt",
    "stats_page",
]
