# Change: Add paper db merge bibtex command

## Why
Users need to merge multiple BibTeX files for snapshot builds. Concatenating files fails when duplicate keys exist, so a dedicated merge command is required to handle duplicates consistently.

## What Changes
- Add a new CLI command: `paper db merge bibtex`.
- Merge multiple `.bib` inputs into a single output file via repeated `-i/--input` and `-o/--output`.
- De-duplicate repeated entries by key by keeping the entry with the most fields, and report duplicates.
- Print a rich summary table at the end of the merge.
- Document the new command in README usage examples.

## Impact
- Affected specs: `paper` CLI
- Affected code: `python/deepresearch_flow/paper/db.py`, `python/deepresearch_flow/paper/db_ops.py` (if helpers are added), docs
