# Change: Modularize paper db serve data layer and web modules

## Why
`paper/web/app.py` currently mixes data loading, matching, caching, rendering, and routing. This blocks reuse (e.g., db compare) and makes maintenance risky.

## What Changes
- Create a dedicated data-layer module (`paper/db_ops.py`) by renaming and expanding `db_match.py` to own load/merge/index logic.
- Move loading/merge/cache/PDF-only helpers out of `paper/web/app.py` into `paper/db_ops.py`.
- Split web-specific logic (markdown rendering, templates, filters, handlers) into focused modules under `paper/web/`.
- Keep `paper/web/app.py` as a thin create_app entrypoint that wires routes and delegates.

## Impact
- Affected specs: `paper-db-ops`
- Affected code: `python/deepresearch_flow/paper/db_ops.py`, `python/deepresearch_flow/paper/web/*`, `python/deepresearch_flow/paper/db.py`
- No intended user-facing behavior changes; this is an internal refactor.
