# Change: Add keyword statistics to paper db statistics

## Why
The CLI statistics output lacks keyword frequency, making it harder to audit extracted metadata quality.

## What Changes
- Aggregate keyword frequencies in `paper db statistics`
- Render a keyword table in the CLI output
- Update README to describe the keyword stats

## Impact
- Affected specs: paper
- Affected code: `python/deepresearch_flow/paper/db.py`, `README.md`
