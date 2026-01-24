## Context
Multi-stage extract currently enforces a strict per-document stage order. Some stages are independent and can run in parallel across a document set.

## Goals / Non-Goals
- Goals:
  - Enable dependency-aware parallelism for multi-stage extract.
  - Preserve current sequential behavior by default.
  - Keep failure handling explicit and deterministic.
- Non-Goals:
  - Cross-document dependency scheduling.
  - Automatic inference of dependencies from schema contents.

## Decisions
- Decision: Add optional `depends_on` to stage definitions.
  - Rationale: Explicit dependencies avoid fragile inference and allow precise control.
- Decision: Introduce `extract.stage_dag` + `--stage-dag` to enable DAG scheduling.
  - Rationale: Avoids breaking existing behavior and keeps opt-in.
- Decision: When `depends_on` is omitted, default dependency is the previous stage in definition order.
  - Rationale: Matches current sequential semantics.
- Decision: Use a ready-only, event-driven queue; workers never block on dependency waits.
  - Rationale: Prevents queue starvation where workers pick blocked tasks and sit idle.
- Decision: In DAG mode, enqueue only stages whose dependencies are satisfied and valid; on failure, mark document failed and skip dependents.
- Decision: In DAG mode, `previous_outputs` includes only the outputs of declared dependencies.
  - Rationale: Avoids race conditions and makes prompt context deterministic.
- Decision: Detect cycles in `depends_on` at startup and fail fast.
  - Rationale: Prevents deadlocks and makes misconfiguration obvious.
- Decision: Reuse `--dry-run` to print a per-document DAG plan when stage_dag is enabled.
  - Rationale: Gives a low-cost way to validate dependency intent before running.

## Risks / Trade-offs
- Increased scheduler complexity: mitigated by keeping the DAG mode optional and preserving sequential defaults.
- Potential confusion when dependencies are misdeclared: mitigated by validation and logging of unresolved dependencies.

## Migration Plan
- Add config/CLI toggle with default false.
- Support `depends_on` without requiring updates to existing templates.
- Update docs with the new flag and dependency behavior.

## Open Questions
- None.
