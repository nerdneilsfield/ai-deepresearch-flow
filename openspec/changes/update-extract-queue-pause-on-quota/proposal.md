# Change: Pause extract queue when all API keys are unavailable

## Why
When all API keys are in cooldown or quota wait, the extract scheduler should pause new work instead of spinning per-request retries. This avoids wasting tokens, log spam, and inconsistent backoff behavior.

## What Changes
- Introduce a queue-level pause in extract scheduling when the key pool is fully unavailable.
- Allow in-flight requests to complete while blocking new stage/document tasks.
- Resume scheduling once any key becomes available, driven by a background watcher.
- Avoid pause/log flicker for short cooldown windows by using a pause threshold.
- Add watcher safeguards to prevent deadlocks on unexpected exceptions.
- Add explicit pause/resume logging with clear timestamps and reasons.

## Impact
- Affected specs: paper-extract
- Affected code: extract scheduler loops, key rotator availability checks, logging
