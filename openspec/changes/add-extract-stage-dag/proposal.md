# Change: Add DAG scheduling for multi-stage extract

## Why
Multi-stage extract currently runs stages sequentially per document, which limits throughput even when stages are independent. A dependency-aware scheduler can run ready stages in parallel while keeping correctness.

## What Changes
- Add an optional DAG scheduler for multi-stage extract.
- Allow stage definitions to declare dependencies (depends_on).
- Add a CLI/config toggle to enable DAG scheduling (default remains sequential).
- Implement ready-only scheduling to avoid queue blocking.
- Update retry/skip logic to respect dependencies.
- Enforce deterministic previous_outputs in DAG mode (dependencies only).
- Detect dependency cycles and fail fast.
- Add a dry-run plan preview for DAG scheduling.
- Add logging to indicate which scheduler mode is active.

## Impact
- Affected specs: paper-extract (new)
- Affected code: python/deepresearch_flow/paper/extract.py, python/deepresearch_flow/paper/template_registry.py, python/deepresearch_flow/paper/cli.py, python/deepresearch_flow/paper/config.py, README.md, README_ZH.md
