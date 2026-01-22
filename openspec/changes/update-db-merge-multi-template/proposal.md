# Change: Split db merge into library and template merges

## Why
`paper db merge` currently merges multiple libraries with the same template. We also need a distinct merge workflow for combining different template outputs from the same paper library.

## What Changes
- Replace `paper db merge` with two subcommands: `paper db merge library` and `paper db merge templates`.
- `merge library` keeps the existing behavior (merge multiple JSON libraries of the same template).
- `merge templates` merges different template JSON files from the same library, preserving shared fields once and appending non-shared fields.
- Shared fields are resolved by first-input precedence when both provide a value.

## Impact
- Affected specs: paper
- Affected code: paper db CLI merge logic, docs for merge usage
- **BREAKING**: `paper db merge` command name changes to subcommands.
