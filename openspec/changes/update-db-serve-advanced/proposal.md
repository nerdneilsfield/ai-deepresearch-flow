# Change: Enhance db serve with advanced search and source views

## Why
The web viewer should support a single, powerful search experience (Google Scholar–style query), plus better data fidelity via optional BibTeX and access to original sources (markdown/PDF).

## What Changes
- Replace the multiple filter inputs with a single query box supporting a Google Scholar–style syntax.
- Add an Advanced Search helper UI to build queries visually.
- Allow optionally loading a BibTeX file to enrich per-paper metadata (field-level merge).
- Allow configuring one or more markdown root directories to locate original markdown sources for papers.
- Allow configuring one or more PDF root directories to locate original PDFs and view them via pdf.js.
- Improve stats to prefer BibTeX fields when present (year/month/venue) with fallback to extracted fields.

## Static Assets
- Default: load ECharts / Mermaid / KaTeX / pdf.js via CDN to avoid increasing wheel size.
- Optional future: add a download-assets workflow to support offline viewing.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/web/app.py`, `python/deepresearch_flow/paper/db.py`, `pyproject.toml`, `uv.lock`, `README.md`
