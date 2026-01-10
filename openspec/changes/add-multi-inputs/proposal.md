# Change: Allow multiple input paths for extract

## Why
Users want to pass multiple input paths in a single extract run and aggregate matching markdown files across all inputs.

## What Changes
- Allow multiple `--input/-i` values for `paper extract`.
- Collect and de-duplicate markdown files from all provided inputs before extraction.

## Impact
- Affected specs: `paper`
- Affected code: `python/deepresearch_flow/paper/cli.py`, `python/deepresearch_flow/paper/utils.py`, `README.md`
