# Change: Add db serve web viewer

## Why
Users want a read-only local web UI to browse and visualize `paper_infos.json`, including list filtering, infinite scrolling, Markdown rendering for different templates, and a statistics dashboard.

## What Changes
- Add `deepresearch-flow paper db serve` to start a local read-only web server.
- Provide list browsing with filters: title query, tags, authors, year/month, venue.
- Provide infinite scrolling UX for the paper list.
- Render paper details from JSON using the existing render templates (per-paper `prompt_template` by default) and convert Markdown to HTML.
- Support client-side rendering for Mermaid and KaTeX in the rendered HTML.
- Add a Stats page with ECharts visualizations.
- Default static assets (ECharts/Mermaid/KaTeX) are loaded via CDN to keep wheel size small.
- Optional: add `paper db download-assets` to download assets to a local cache for offline use.
- Implement safe HTML rendering by disabling raw HTML in Markdown and/or sanitizing output.

## Performance
- Load and index the JSON once at startup (in-memory for ~10MB / a few thousand papers).
- Serve paginated results from in-memory indexes; do not re-parse JSON per request.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/db.py` (+ new web module/files), `pyproject.toml`, `uv.lock`, `README.md`
