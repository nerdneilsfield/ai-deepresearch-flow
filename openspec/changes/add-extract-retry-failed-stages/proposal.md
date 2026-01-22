# Change: add stage-level retry for paper extract

## Why
`paper extract --retry-failed` currently retries any document with a failed stage, which is noisy and makes it hard to target only failed stages.

## What Changes
- Add `--retry-failed-stages` to retry only failed stages per document.
- Extend summary output to include total failed stages and retried stages.
- Clarify behavior in CLI help and README.

## Impact
- Affected specs: paper-extract
- Affected code: python/deepresearch_flow/paper/extract.py, CLI options, README/README_ZH
