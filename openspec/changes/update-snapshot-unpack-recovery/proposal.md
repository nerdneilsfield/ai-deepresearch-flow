# Change: Update snapshot unpack recovery commands

## Why
- Recover source and translated Markdown from snapshot exports with filenames aligned to PDF basenames.
- Restore an aggregated `paper_infos.json` from snapshot summaries using a selected template.
- Replace the single-purpose legacy unpack entry with explicit subcommands and clear outputs.

## What Changes
- Replace legacy `paper db snapshot unpack` with `unpack md` and `unpack info` subcommands.
- `unpack md` restores source/translated Markdown into separate output dirs and aligns names to matching PDF basenames; fallback to sanitized `paper_title` when no PDF match exists.
- `unpack info` aggregates per-template summary JSONs into one `paper_infos.json` output.
- Print a Rich summary table with success/failure counts at the end of each unpack command.
- **BREAKING**: the legacy options-only `paper db snapshot unpack` entry is removed.

## Impact
- Affected specs: `paper-db-snapshot`
- Affected code: `python/deepresearch_flow/paper/db.py`, `python/deepresearch_flow/paper/snapshot/unpacker.py`
- Affected docs: `README.md`, `README_ZH.md`
