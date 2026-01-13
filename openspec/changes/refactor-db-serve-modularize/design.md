## Context
`paper/web/app.py` is a large, mixed-responsibility module. We need a stable data-layer API for non-web commands (e.g., compare) and a modular web layer for maintainability.

## Goals / Non-Goals
- Goals:
  - Centralize all load/merge/index logic in `paper/db_ops.py` with no web dependencies.
  - Keep web behavior the same while reducing `app.py` to a thin entrypoint.
  - Enable reuse of data-layer logic from CLI tools.
- Non-Goals:
  - No UI/behavior changes to the db serve pages.
  - No schema changes to JSON outputs.

## Decisions
- Decision: Rename `paper/db_match.py` to `paper/db_ops.py` and expand it to include load/merge/cache/PDF-only helpers.
  - Rationale: One canonical data-layer module used by both web and CLI.
- Decision: Split web logic into `paper/web/constants.py`, `paper/web/markdown.py`, `paper/web/templates.py`, `paper/web/filters.py`, and `paper/web/handlers/*`.
  - Rationale: Reduce coupling and make files single-purpose.

## Risks / Trade-offs
- Risk: Behavior regressions in db serve due to refactor.
  - Mitigation: Keep logic unchanged; only move functions and update imports.
- Risk: Circular imports between web modules and db_ops.
  - Mitigation: Keep db_ops free of web dependencies and isolate web modules.
- Risk: Detail handler remains large after split.
  - Mitigation: Further split `handlers/detail.py` into view-specific render helpers.
- Risk: Production outage due to refactor.
  - Mitigation: Keep data/web changes in the same commit for easy revert.
  - Rollback: `git revert <commit-hash>` to restore pre-refactor behavior.

## Architecture
```
┌─────────────────────────────────────────┐
│         paper/web/app.py (Thin)        │
└──────────────┬──────────────────────────┘
               │
       ┌───────┴──────────┐
       │                  │
┌──────▼──────┐  ┌────────▼─────────┐
│ db_ops.py   │  │  paper/web/*     │
│ (Data Layer)│  │ (Presentation)   │
└─────────────┘  └──────────────────┘
```

## Migration Plan
1. Rename db_match -> db_ops and update imports.
2. Move data-layer helpers into db_ops and rewire app.py.
3. Extract web modules and update route handlers.
4. Run db serve on sample data to validate output parity:
   - Capture index JSON before refactor with a temporary script that calls data-layer helpers.
   - Capture index JSON after refactor and diff the two outputs.
   - Confirm PDF/Markdown resolution counts are identical.
5. Run a lightweight performance check:
   - Time `paper db serve` index build before/after; ensure no >10% regression.

## Open Questions
- None.
