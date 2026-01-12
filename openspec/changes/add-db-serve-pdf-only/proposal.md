# Change: Add PDF-only entries and keyword stats in db serve

## Why
Some datasets only have PDFs available, so db serve should surface them with clear "PDF-only" messaging. Keyword stats are currently empty and need to be computed from the papers dataset for visibility.

## What Changes
- Add PDF-only entries derived from `--pdf-root` when no JSON record matches
- Extract titles from PDF metadata when available (fallback to filename)
- Mark PDF-only entries in the UI and show a warning in detail views
- Exclude PDF-only entries from db stats and homepage stats
- Compute keyword frequency stats and expose them in `/stats`

## Impact
- Affected specs: paper
- Affected code: `python/deepresearch_flow/paper/web/app.py`, `pyproject.toml`
