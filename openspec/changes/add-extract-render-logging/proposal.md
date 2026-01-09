# Change: Add extract-time markdown rendering and structured logging

## Why
Users want extraction to optionally render markdown outputs in the same run, and need clearer progress/logging with optional verbose details.

## What Changes
- Add extract CLI options to render markdown outputs during extraction.
- Add structured progress logging (tqdm) and colored logs with a verbose flag.
- Document the new flags and behavior.

## Impact
- Affected specs: `paper` capability
- Affected code: `paper extract`, render pipeline, logging setup, README
