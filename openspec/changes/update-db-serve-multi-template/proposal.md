# Change: Support multi-template inputs in db serve

## Why
Users want to serve multiple extraction outputs at once, merge the same paper across templates, and switch summary render templates per paper.

## What Changes
- Allow multiple `--input` values for `paper db serve`.
- Require each input JSON to be an object with `template_tag` and `papers`; reject legacy array-only input.
- Infer missing `template_tag` by comparing paper keys to template schema keys.
- Merge papers across inputs by title similarity (>= 0.95), preferring BibTeX title when available and falling back to `paper_title`.
- Add a Summary template selector per paper; default to `simple` or the first available template by input order; Source/PDF views stay unchanged.
- Add optional `--cache-dir`/`--no-cache` flags to reuse merged inputs between runs.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/db.py`, `python/deepresearch_flow/paper/web/app.py`, `README.md`
