## Context
The project already supports rendering Markdown from extracted JSON via Jinja2 templates. A web viewer should reuse this rendering logic, but present it in HTML with modern browsing UX.

Constraints:
- Database size is small (~10MB, up to a few thousand papers), so in-memory indexing is acceptable.
- UI is read-only.

## Goals / Non-Goals
- Goals:
  - Provide `paper db serve` as a local web viewer for a JSON database.
  - Fast list browsing with filtering and infinite scrolling.
  - Per-paper detail page rendered from the appropriate template.
  - Stats dashboard with interactive charts.
  - Mermaid + KaTeX support in rendered output.
- Non-Goals:
  - Editing papers/tags/notes in the UI.
  - Multi-user auth or deployment hardening.

## Decisions
- Backend: FastAPI (ASGI) with Uvicorn.
- Data loading:
  - Load `paper_infos.json` into memory on startup.
  - Build indexes for filters: year, month, tags, authors, venue.
  - Store a stable list order (default: year desc, then title).
- Rendering:
  - Determine template per paper:
    - Default: use paper's `prompt_template`.
    - Allow override by query parameter (future) or CLI (optional).
  - Render Markdown using existing Jinja2 templates.
  - Convert Markdown to HTML server-side (e.g., markdown-it-py) and enable Mermaid/KaTeX client-side.
- Assets:
  - Include ECharts, Mermaid, and KaTeX as static assets (vendored) to avoid runtime network dependency.

## Routes (proposed)
- `GET /` list page (infinite scroll)
- `GET /paper/{source_hash}` detail page
- `GET /stats` stats page
- `GET /api/papers` paginated + filters
- `GET /api/stats` stats JSON

## Risks / Trade-offs
- Vendoring JS assets increases repo size.
- Markdown-to-HTML conversion must be safe; we should avoid executing arbitrary HTML from untrusted data by default.

## Migration Plan
- Add dependencies and minimal web module.
- Wire `paper db serve` command.
- Add docs and examples.
