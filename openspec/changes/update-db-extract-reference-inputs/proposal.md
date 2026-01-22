# Change: Separate db extract target JSON and reference inputs

## Why
`paper db extract` needs a target JSON database and optional reference lists (JSON or BibTeX) to filter matched entries. Reference inputs should be mutually exclusive and can default to the target JSON when using `--input-json`.

## What Changes
- Add `--json` as the target JSON database to extract from.
- Treat `--input-json` and `--input-bibtex` as mutually exclusive reference inputs.
- If `--input-json` is provided, default `--json` to that same path.
- Include unmatched reference entries in CSV output when requested.

## Impact
- Affected specs: paper
- Affected code: paper db extract CLI + filtering logic
