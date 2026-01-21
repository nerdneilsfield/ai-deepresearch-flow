# Change: add retry-only fixes for recognize fix-math and fix-mermaid

## Why
Re-running full fix passes is slow when only a few formulas or Mermaid blocks failed. Users want the same targeted retry workflow that `paper extract --retry-failed` provides.

## What Changes
- Add a retry-only mode for `recognize fix-math` and `recognize fix-mermaid` that reads the prior error report and processes only failed items.
- Reuse existing error report paths by default so retrying does not require re-specifying inputs.
- Ensure retry mode fails fast when the error report is missing or empty.

## Impact
- Affected spec: recognize-fix.
- Affected code: `python/deepresearch_flow/recognize/cli.py`, `python/deepresearch_flow/recognize/math.py`, `python/deepresearch_flow/recognize/mermaid.py`.
- CLI: add retry-only option for fix-math and fix-mermaid.
