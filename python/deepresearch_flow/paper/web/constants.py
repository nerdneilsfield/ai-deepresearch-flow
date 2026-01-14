"""Constants for paper web UI."""

from pathlib import Path

# CDN URLs for external libraries
CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
CDN_MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
CDN_KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css"
CDN_KATEX_JS = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"
CDN_KATEX_AUTO = "https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"

# Use legacy builds to ensure `pdfjsLib` is available as a global.
CDN_PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/legacy/build/pdf.min.js"
CDN_PDFJS_WORKER = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/legacy/build/pdf.worker.min.js"

# PDF.js viewer configuration
PDFJS_VIEWER_PATH = "/pdfjs/web/viewer.html"
PDFJS_STATIC_DIR = Path(__file__).resolve().parent / "pdfjs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
