# Change: Add snapshot build + API for production deployment

## Why
`paper db serve` currently builds its index at startup by loading JSON inputs and scanning local Markdown/PDF roots. It also serves a coupled UI+API. This makes production deployment brittle (the runtime must have access to all original roots), makes it hard to deploy a standalone static frontend, and provides no stable paper identity for attaching business data across weekly refreshes.

## What Changes
- Add an offline snapshot build pipeline that produces:
  - `paper_snapshot.db` (SQLite snapshot for metadata, facets, and FTS5 search corpus)
  - `paper-static/` (hashed static assets: `/pdf`, `/md`, `/md_translate/<lang>`, `/images`)
  - `summary/` (per-paper summary files for static hosting)
  - `manifest/` (per-paper download manifests for client-side ZIP export)
- Define a stable `paper_id` derived from DOI/BibTeX/arXiv (hashed, not a raw DOI string) for long-lived identity.
- Add a standalone API service that reads `paper_snapshot.db` and serves search/browse APIs for a separately deployed frontend.
- Keep the existing `paper db serve` as-is for local/dev usage; the new pipeline is opt-in and additive.

## Impact
- Affected specs: `paper-db-snapshot`, `paper-db-api`
- Affected code: `python/deepresearch_flow/paper/*` (new snapshot builder + API modules), CLI additions under `paper db`
- Backwards compatibility: existing commands keep working; new commands are added

