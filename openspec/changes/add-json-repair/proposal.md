# Change: Add JSON repair and permissive extra fields

## Why
Model outputs sometimes contain minor JSON syntax issues or extra fields, which causes avoidable parse failures and retries.

## What Changes
- Add a JSON repair fallback for model outputs before raising parse errors.
- Treat extra top-level fields as non-fatal during extraction (only required fields are enforced).
- Add a lightweight dependency to support JSON repair.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/utils.py`, `python/deepresearch_flow/paper/extract.py`, `python/deepresearch_flow/paper/schema.py`, `pyproject.toml`
