# Change: Add Translated view to paper db serve

## Why
`paper db serve` already exposes Summary, Source, and PDF views, but users now generate translated Markdown (`.zh.md`, `.ja.md`, etc.) with the new translator workflow. There is no built-in way to browse those translated sources alongside the original Markdown within the db serve UI.

We want a dedicated Translated view that renders translated markdown using the same pipeline as Source, with navigation tools (outline + back-to-top) and a language selector for multiple translations.

## What Changes
- Add a new Translated tab in the db serve detail view alongside Summary/Source/PDF.
- Discover translated markdown files by suffix (`.<lang>.md`) under `--md-translated-root` directories.
- Provide a language dropdown to select the desired translation; default to `zh` when available.
- Render translated markdown using the same renderer and UI affordances as Source (outline panel + back-to-top control).
- Add an empty-state message when no translated files are available.

## Impact
- Affected specs: `paper` (db serve)
- Affected code: `python/deepresearch_flow/paper/web/app.py` (and related UI templates/utilities)
- Data compatibility: no changes to JSON input format
