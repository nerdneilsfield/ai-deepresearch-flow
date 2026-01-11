# Change: Add homepage summary stats for db serve

## Why
Users want quick visibility into total and filtered counts (PDF/Source/Summary and template tags) while browsing the papers list.

## What Changes
- Add two summary rows between filters and results: overall totals and current filtered counts.
- Include counts for PDF/Source/Summary availability and per-template summary counts.
- Document the stats row in README.

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/web/app.py, README.md
