# Change: retry failed stages should rerun missing stages

## Why
When `--retry-failed-stages` is used, documents can still fail validation if a required stage is missing but not listed in the errors report.

## What Changes
- If a stage output is missing for a document under `--retry-failed-stages`, that stage is forced to run.
- This avoids validation errors caused by missing required stages when only a later stage is retried.

## Impact
- Affected specs: paper-extract
- Affected code: python/deepresearch_flow/paper/extract.py
