# Change: add per-key quota reset handling

## Why
Some providers enforce fixed quota windows and return reset timestamps. Current extraction keeps retrying, wasting time and tokens when quotas are exhausted.

## What Changes
- Allow `api_keys` to be `list[string|object]` with per-key quota metadata.
- Detect quota exhaustion via configurable error substrings and pause until the next reset time.
- Bind cooldown/pausing to the specific API key that hit quota.

## Impact
- Affected specs: paper-extract
- Affected code: config parsing, extract key rotation/error handling, README/README_ZH
