# Change: update translation debug log

## Why
Translation failures are hard to diagnose without access to the exact request/response payloads for each attempt. A structured JSON log enables precise debugging.

## What Changes
- Add an option to emit a JSON log that records each translation request and response (including retries and fallbacks).
- Include attempt metadata (group id, retry round, provider/model, failure reason).

## Impact
- Affected specs: translator
- Affected code: python/deepresearch_flow/translator/cli.py, python/deepresearch_flow/translator/engine.py
