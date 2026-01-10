## Context
The ref implementation prints detailed statistics with rich tables and favors BibTeX fields when present. The current command only prints basic counts with plain text.

## Goals / Non-Goals
- Goals:
  - Provide rich, scannable tables for key distributions.
  - Keep output deterministic and non-interactive.
- Non-Goals:
  - Change schema or add new extraction fields.

## Decisions
- Use `rich` tables and a top-level panel header.
- Prefer BibTeX fields per-field (e.g., use bibtex year if present, fall back to extracted venue when bibtex venue is missing).
- Normalize date/month formats to a consistent representation for grouping.
- Add a simple distribution bar column for year/month tables.
- Expose `--top-n` (default 20) to control table sizes.

## Risks / Trade-offs
- Adds a dependency (`rich`).
- Venue normalization heuristics may not cover all formats.

## Migration Plan
- Add `rich` dependency.
- Update statistics implementation and README.

## Open Questions
- Confirm default top-N for tables (proposal uses 20).
