# Change: update db cli progress

## Why
Users need visibility into scan/match/copy progress when running db compare, extract, or transfer tasks.

## What Changes
- Add progress bars to `paper db compare` for scanning inputs and matching items.
- Add progress bars to `paper db extract` for scanning/matching and copying outputs.
- Add progress bars to `paper db transfer-pdfs` for copy/move operations.

## Impact
- Affected specs: paper-db-compare, paper-db-extract, paper-db-transfer
- Affected code: python/deepresearch_flow/paper/db.py, python/deepresearch_flow/paper/db_ops.py
