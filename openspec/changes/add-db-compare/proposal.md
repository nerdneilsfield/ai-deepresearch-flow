# Change: add paper db compare

## Why
Users need to verify coverage across JSON, PDF folders, Markdown folders, and translated Markdown outputs. The goal is to quickly find gaps (missing translations, missing PDFs, missing Markdown, missing JSON entries) and see how items matched.

## What Changes
- Add a new CLI command: `deepresearch-flow paper db compare`.
- Compare two datasets (A/B), each built from one or more inputs: JSON files, PDF roots, Markdown roots, or translated Markdown roots.
- Use the same matching logic as `paper db serve` to ensure consistent results.
- Present a rich summary table (both/A-only/B-only) and sample lists in the terminal.
- Export full results to CSV with match metadata and file paths.

## Impact
- Affected specs: `paper-db-compare` (new capability).
- Affected code: paper CLI and comparison logic (new module); reuse existing matching helpers in `deepresearch_flow.paper.db_ops`.
- No breaking changes; new command only.
