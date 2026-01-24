# Change: Add DB verify + retry-list extraction

## Why
Users need a fast way to validate generated paper JSON databases for empty fields and then re-run extraction only for the problematic items.

## What Changes
- Add `paper db verify` to validate JSON entries against a prompt template schema and report empty fields.
- Emit a JSON report for downstream retries and print a rich console summary + details.
- Add `paper extract --retry-list-json` to re-extract only the items listed in the verify report (stage-specific when possible).

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/cli.py`, `python/deepresearch_flow/paper/extract.py`, `python/deepresearch_flow/paper/db_ops.py` (or similar verify utility), CLI help docs.
