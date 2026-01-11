# Change: Update extract output JSON structure

## Why
Users want extract outputs to match the `{template_tag, papers}` structure expected by db serve, and are willing to break the legacy array-only format.

## What Changes
- Update `paper extract` JSON output to be an object with `template_tag` and `papers`.
- Remove legacy array-only output format (**BREAKING**).

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/extract.py, README.md
