# Change: Add extraction summary statistics

## Why
Paper extraction runs can be long and expensive. Users need a clear summary of processed input/output sizes, estimated tokens, and throughput to understand cost and validate pacing.

## What Changes
- Add a rich summary table after `paper extract` completes, including duration and throughput metrics.
- Report input, prompt, and output character totals plus estimated prompt/completion tokens.
- Add a live token ticker on the progress indicator.
- Surface the same counts in dry-run mode (with completion tokens set to zero).

## Impact
- Affected specs: `paper`.
- Affected code: extraction workflow and CLI output.
