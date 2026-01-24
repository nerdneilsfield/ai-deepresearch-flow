## Context
Extraction currently retries per request when keys are unavailable. This can spin retries even when all keys are in cooldown/quota. We need a queue-level pause that blocks new tasks while letting in-flight work finish.

## Goals / Non-Goals
- Goals:
  - Pause scheduling when all keys are unavailable (cooldown or quota).
  - Resume scheduling when any key becomes available.
  - Avoid pause/log flicker for short cooldown windows.
  - Allow in-flight requests to complete.
  - Provide clear pause/resume logs with timestamps.
- Non-Goals:
  - Cancel in-flight requests.
  - Change provider-specific retry policies beyond queue pause.

## Decisions
- Add a key pool availability API on the key rotator (e.g., `next_available_delay()` and/or `wait_until_available()`), computed from cooldown/quota timers.
- Extract schedulers (sequential + DAG) will call a shared `await_key_pool_ready()` gate before dequeuing new work.
- A background watcher will monitor the key pool and signal a shared `asyncio.Event` to resume workers when availability returns.
- Introduce a pause threshold (e.g., 10–30s). Only waits above this threshold trigger queue pause and pause logs.
- Pause logging will trigger once per pause window with the computed resume time.
- Wrap watcher logic with a top-level exception guard; on failure, log and release the gate to avoid deadlock.

## Risks / Trade-offs
- Workers may idle while the queue is paused, but this is intended to avoid wasteful retries.
- Timing precision depends on local clock and configured reset times.
- A pause threshold may allow brief rate-limit backoff to proceed without global pause.

## Migration Plan
- Update key rotator with availability checks and resume signaling.
- Integrate queue pause gate into extract schedulers.
- Add logging for pause/resume transitions.

## Open Questions
- None.
