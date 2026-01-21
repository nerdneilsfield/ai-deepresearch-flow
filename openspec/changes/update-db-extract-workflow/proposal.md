# Change: update db extract workflow

## Why
Users need a streamlined way to identify unprocessed PDFs and to export matched source Markdown alongside existing JSON/translated outputs.

## What Changes
- Add a `paper db compare` option to export only-in-B file paths for batch OCR.
- Add `paper db extract` support for exporting matched source Markdown.
- Add a `paper db transfer-pdfs` helper to copy/move PDFs from a list.
- Document the end-to-end workflow in README.

## Impact
- Affected specs: paper-db-compare, paper-db-extract, paper-db-transfer
- Affected code: python/deepresearch_flow/paper/db.py
- Documentation: README.md, README_ZH.md
