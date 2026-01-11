# Change: Improve recognize UX (progress, summary, verbose logging)

## Why
Recognize commands can process large OCR batches. Users need visible progress, a concise completion summary, and a way to enable detailed logs for troubleshooting.

## What Changes
- Add progress bars for `recognize md embed`, `recognize md unpack`, and `recognize organize`.
- Add end-of-run summaries using Rich tables (counts, image totals, duration, relative output locations).
- Add `--dry-run` mode and warnings when output directories are non-empty.
- Add `--verbose` logging to recognize commands using existing logging dependencies.

## Impact
- Affected specs: `recognize`.
- Affected code: recognize CLI implementation.
