# Change: add paper db extract

## Why
Users need to extract already-processed artifacts after comparing coverage, producing a clean JSON subset and/or translated markdown subset without manual filtering.

## What Changes
- Add a new CLI command: `paper db extract`.
- Export matched JSON entries to a new JSON file while preserving original format.
- Export matched translated markdown files to a new root while preserving directory structure.
- Reuse db compare matching logic for consistency.
- Export CSV report compatible with `paper db compare`.

## Impact
- Affected specs: paper-db-extract (new)
- Affected code: python/deepresearch_flow/paper/db.py, python/deepresearch_flow/paper/db_ops.py
