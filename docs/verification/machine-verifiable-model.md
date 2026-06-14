# Machine-Verifiable Assurance Model

This document is the assurance boundary for `ai-deepresearch-flow`.
It does **not** claim absolute correctness. It enumerates every class of evidence that can be checked by machines in this repository and makes gaps explicit.

## Top-Level Rule

For every property that is machine-verifiable at reasonable cost, the project should prefer executable evidence over human inspection.

Allowed evidence classes:

1. Static checks
2. Type checks
3. Unit and integration tests
4. Property-based tests
5. Fuzz tests
6. Fault-injection tests
7. Model checking for critical state machines
8. Build/reproducibility checks
9. Dependency and supply-chain checks
10. Runtime startup/config observability checks
11. Deployment smoke checks

Human review may explain results, but it does not replace machine evidence.

## Threat/Fault Model

### Covered By Software-Level Verification

- Malformed user input
- Malformed markdown/LaTeX/Mermaid/PDF metadata
- Invalid JSON, JSONL, YAML, TOML, and env config
- Missing files and directories
- Permission errors
- Interrupted writes
- Partial writes
- Corrupted cache files detectable by checksum/schema validation
- Unknown OAuth clients
- Lost process memory across restart
- Concurrent requests
- API transport mismatch
- Network timeout and retryable upstream failures
- Dependency version drift detectable by lockfiles/audit tools

### Covered Only With Explicit Fault-Tolerance Mechanisms

- Disk bit flips detectable by checksums
- In-memory bit flips detectable by redundant representation or validation
- Network corruption detectable by TLS and application-level checksum
- Cache corruption recoverable by schema validation and fail-closed behavior

### Not Claimable Without Hardware/Platform Assumptions

- Arbitrary CPU register corruption
- Arbitrary RAM corruption without ECC or redundant execution
- Arbitrary kernel/runtime/compiler corruption
- Byzantine browser/client behavior
- Arbitrary post-verification output buffer mutation

Required statement for this class:

> Under arbitrary unbounded bit flips at any layer, correctness cannot be guaranteed by application code alone. The verifiable target is fail-closed or detectable corruption under an explicit bounded fault model.

## Critical Properties

### P0: Security Fail-Closed

Machine-checkable properties:

- Missing auth token is rejected.
- Wrong bearer token is rejected.
- Missing OAuth client either recovers through a registered safe client record or fails before issuing access.
- GitHub allowlist rejects non-allowed users.
- Sensitive startup config is masked in logs.
- Cache parse failure does not silently authorize.

Required evidence:

- Pytest integration tests for HTTP status and response behavior.
- Property/fault tests for malformed cache/config.
- Static secret grep before release.

### P0: Persistent State Integrity

Machine-checkable properties:

- Writes are atomic: temp file + fsync + rename where feasible.
- On write failure, in-memory state must not claim persistence if persistence is required.
- On corrupted persisted state, startup must either reject, repair from safe source, or ignore with explicit warning according to the component policy.
- Persisted OAuth clients survive app restart.

Required evidence:

- Fault-injection tests for write failure.
- Fault-injection tests for partial/corrupt JSON.
- Restart tests.

### P0: MCP/OAuth Protocol State Machine

Machine-checkable properties:

- DCR registration produces a client usable by `/authorize`.
- Registered clients survive restart when cache path is configured.
- Unknown client on `/authorize` cannot cause unauthorized token issuance.
- Resource URI normalization is consistent between `/authorize` and `/token`.
- Token audience/resource mismatch fails closed.

Required evidence:

- HTTP integration tests.
- State-machine model checking for registry/cache transitions.
- Fault-injection tests for missing cache, stale cache, corrupted cache, and duplicate registration.

### P1: Renderer Robustness

Machine-checkable properties:

- Markdown rendering never crashes on malformed markdown.
- LaTeX warnings do not break page render.
- Mermaid rendering failure degrades to a visible error block, not app crash.
- PDF viewer dependency failure degrades to fallback UI.

Required evidence:

- Frontend component tests.
- Fuzz corpus for markdown/LaTeX/Mermaid fragments.
- Build checks.

### P1: Export/Data Format Correctness

Machine-checkable properties:

- JSONL export emits valid one-object-per-line JSON.
- Selected ZIP export preserves requested content categories.
- Missing per-paper content is reported, not silently emitted as empty success.
- File names are stable and sanitized.

Required evidence:

- Frontend black-box tests against public UI/service boundary.
- Property tests for filename/path sanitization.

### P1: Search/API Robustness

Machine-checkable properties:

- Empty/invalid query returns structured error.
- Invalid pagination/filter bounds are rejected or clamped as specified.
- Upstream embedding/rerank failures return recoverable errors.
- API schemas remain JSON-serializable.

Required evidence:

- API integration tests.
- Schema tests.
- Property tests for filter parsing.

## Verification Matrix

| Layer | Machine-verifiable evidence | Required command/status |
|---|---|---|
| Python syntax | compileall | `uv run python -m compileall -q python tests` |
| Python tests | pytest | `uv run pytest` |
| Python style/type | project check | `rtk make check` |
| Frontend tests | vitest | `cd frontend && npm test -- --run` |
| Frontend build | bundler | `cd frontend && npm run build` |
| Frontend audit | npm advisory scan | `cd frontend && npm audit --audit-level=high` |
| Dependency lock drift | git + lockfiles | `git diff --exit-code -- pyproject.toml uv.lock requirements.txt package.json frontend/package.json frontend/package-lock.json` |
| Secret leakage | grep/static scan | project-defined scanner, must mask real hosts/tokens in docs/examples |
| OAuth registry model | model checker | TLA+/Alloy spec to be added for cache/registry transitions |
| Fault injection | pytest/Hypothesis | corrupt cache, partial write, missing client, concurrent write |
| Fuzz | pytest/Hypothesis/fast-check | markdown, JSONL, URL/path, MCP request payloads |

## Current Evidence Snapshot

Last observed local state on 2026-06-15:

- `rtk make check`: passed.
- `uv run python -m compileall -q python tests`: passed.
- `cd frontend && npm test -- --run`: 26 files passed, 104 tests passed.
- `cd frontend && npm audit --audit-level=high`: 0 vulnerabilities.
- `cd frontend && npm run build`: passed.
- `uv run pytest`: failed, 241 failed and 2042 passed.

Therefore release is blocked until the full Python test suite failure set is explained and resolved or explicitly quarantined with a documented reason.

## Release Gate

A release tag must not be created or pushed unless all mandatory commands for the touched subsystems pass freshly in the release environment.

If any machine-verifiable property has no test/model/check yet, the release note must list it as an assurance gap, not imply coverage.
