# Change: Add sleep throttling to extract

## Why
Users need to pace extraction requests by time budget (e.g., hourly limits) while running large batches.

## What Changes
- Add `--sleep-every` and `--sleep-time` to `paper extract`.
- Count every request (including retries and multi-stage requests) and sleep globally after each threshold.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/cli.py`, `python/deepresearch_flow/paper/extract.py`, `README.md`
