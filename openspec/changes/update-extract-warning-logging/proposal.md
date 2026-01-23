# Change: improve extraction warning logging readability

## Why
Current warning logs for extraction failures are terse and hard to scan. The user asked for colored, more human-readable logs.

## What Changes
- Add colored, structured warning output for extraction failures.
- Include concise reason summaries for parse/validation/provider errors.

## Impact
- Affected specs: paper-extract
- Affected code: python/deepresearch_flow/paper/extract.py
