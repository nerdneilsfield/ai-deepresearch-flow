# Change: Improve db statistics output with rich tables

## Why
The current `paper db statistics` output is too limited compared to the previous implementation and is hard to scan. Users want richer aggregation and better visualization.

## What Changes
- Expand statistics to include year/month/venue/author/tag distributions with counts and percentages.
- Prefer BibTeX metadata per-field when present (year/month/venue) and fall back to extracted fields.
- Normalize date/month formats for consistent grouping.
- Render statistics using rich tables and panels, including a simple distribution bar for year/month.
- Add a --top-n CLI option to control how many entries to display.
- Add a dependency on `rich`.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/db.py`, `pyproject.toml`, `uv.lock`, `README.md`
