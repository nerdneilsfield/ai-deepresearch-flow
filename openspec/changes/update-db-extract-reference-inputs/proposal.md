# Change: Separate db extract target JSON and reference inputs

## Why
`paper db extract` needs a target JSON database and optional reference lists (JSON or BibTeX) to filter matched entries. Reference inputs should be mutually exclusive and can default to the target JSON when using `--input-json`.

## What Changes
- Add `--json` as the target JSON database to extract from.
- Treat `--input-json` and `--input-bibtex` as mutually exclusive reference inputs.
- If `--input-json` is provided, default `--json` to that same path.
- Include unmatched reference entries in CSV output when requested.
- Deduplicate extracted JSON entries by normalized title before writing output.
- When multiple target entries match one reference, keep the most detailed entry.
- Normalize BibTeX title formatting (LaTeX/math markers) to improve title matching.
- Use author/year fallback matching when title matching fails.
- Include matched and only-in-A entries in CSV output for reference filtering.
- Strip leading decimal section numbers in titles (e.g., 23.4) during matching.
- Avoid merging numeric single-character tokens during title normalization.

## Impact
- Affected specs: paper
- Affected code: paper db extract CLI + filtering logic
