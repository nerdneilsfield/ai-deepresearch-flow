# Change: add per-key error tracking for provider keys

## Why
Current key rotation is round-robin only. When a key is rate-limited or failing, it keeps getting reused, increasing errors and slowing retries.

## What Changes
- Track per-key errors and apply a temporary cooldown when retryable provider errors occur.
- Skip keys on cooldown when selecting API keys; fall back to waiting if all keys are cooling down.
- Expose cooldown/skip behavior in verbose logs.

## Impact
- Affected specs: paper-extract
- Affected code: python/deepresearch_flow/paper/extract.py (KeyRotator usage and call retry flow)
