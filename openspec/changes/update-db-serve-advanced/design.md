## Context
`paper db serve` currently provides a read-only web UI for `paper_infos.json` with simple per-field filters. Users want:
- a unified search box with composable query semantics,
- optional BibTeX enrichment for accuracy,
- optional original source viewing (markdown and PDF) inside the UI.

Constraints:
- Database size is small (~10MB, up to a few thousand papers), so in-memory indexing is fine.
- UI remains read-only.
- Avoid increasing wheel size significantly.

## Goals / Non-Goals
- Goals:
  - Single search box with Google Scholar–style query syntax.
  - Advanced helper UI to build queries.
  - BibTeX enrichment loaded at serve time.
  - Source views: open original markdown and PDF (if discoverable under provided roots).
  - Stats reflect BibTeX-enhanced fields.
- Non-Goals:
  - Editing records from the UI.
  - Full-blown search engine or database backend.

## Decisions
- Query language (initial):
  - Terms are AND by default.
  - Quoted phrases supported: "...".
  - Negation supported: -term, -tag:fpga.
  - OR supported at term level: `term1 OR term2`.
  - Field filters: `title:`, `author:`, `tag:`, `venue:`, `year:`, `month:`.
  - Year range: `year:2020..2024`.
- Matching:
  - Default matching is case-insensitive substring.
  - Future: optional fuzzy matching.
- BibTeX enrichment:
  - Parse BibTeX via pybtex when provided.
  - Match per record by title similarity (difflib-based), with explicit note that this is heuristic.
  - Merge is field-level: if BibTeX has year but not venue, keep extracted venue.
- Source resolution:
  - `--md-root` and `--pdf-root` are repeatable.
  - Resolve on startup by building filename indexes for quick lookup.
  - For PDFs, use heuristics based on `source_path` patterns (e.g., strip `.pdf-...` from markdown name).
- PDF viewer:
  - Use pdf.js via CDN and serve PDF bytes via FileResponse from allowed roots.
  - Enforce path safety: only serve files under configured roots.

## Safety
- Markdown to HTML keeps `html=False`.
- For raw markdown/source text views, escape HTML.

## Routes (incremental)
- `GET /` list page with unified search + advanced builder (toggle)
- `GET /paper/{source_hash}?view=summary|source|pdf`
- `GET /api/papers` supports `q=` query language and pagination
- `GET /api/stats` uses enriched fields
- `GET /api/source/{source_hash}` returns escaped markdown content if available
- `GET /api/pdf/{source_hash}` streams the matched PDF file

## Open Questions
- PDF mapping heuristic specifics depend on naming conventions of your files.
