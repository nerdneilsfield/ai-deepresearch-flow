## Context
Extraction can run for hours. Users need to interrupt runs without losing in-flight work. The graceful shutdown should stop scheduling new tasks while letting current requests finish and outputs flush.

## Goals / Non-Goals
- Goals:
  - SIGINT/SIGTERM triggers graceful shutdown.
  - Stop scheduling new tasks once shutdown starts.
  - Wait for in-flight tasks to complete and write outputs/errors.
  - Clear logging of shutdown lifecycle.
- Non-Goals:
  - Force-cancel in-flight HTTP requests.
  - Add extra per-request timeouts beyond existing timeout config.

## Decisions
- Introduce a shared shutdown flag/event set by signal handlers in the extract command.
- Scheduler loops check the shutdown flag before dequeuing new tasks.
- In-flight tasks run to completion; output flush remains unchanged.
- On shutdown, log the number of in-flight tasks and transition to “draining” state.

## Risks / Trade-offs
- Shutdown can take as long as the slowest in-flight request (bounded by request timeout).

## Migration Plan
- Add signal handling and shutdown gate.
- Integrate gate into sequential and DAG schedulers.
- Update logs to show graceful shutdown state.

## Open Questions
- None.
