# Change: retry parse/validation errors and improve warning reasons

## Why
Parse and schema validation failures are currently treated as non-retryable, even though mid-run failures can benefit from retries. Warning logs also omit clear error reasons.

## What Changes
- Treat JSON parse and schema validation failures as retryable using existing `max_retries`.
- Include error_type and detailed error message in extraction warnings.

## Impact
- Affected specs: paper-extract
- Affected code: python/deepresearch_flow/paper/extract.py
