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

Normative sources:

- MCP Authorization specification 2025-06-18.
- OAuth 2.0 Protected Resource Metadata, RFC 9728.
- OAuth 2.0 Resource Indicators, RFC 8707.
- OAuth 2.0 Dynamic Client Registration, RFC 7591.

Modeling rule: OAuth/MCP model invariants must be derived from those protocol
requirements or from documented project policy. Historical model variables such
as `lastReturn` must not be treated as live authorization state unless the model
explicitly defines them as the current response event.

Machine-checkable properties:

- DCR registration produces a client usable by `/authorize`.
- Registered clients survive restart when cache path is configured.
- Unknown client on `/authorize` cannot cause unauthorized token issuance.
- Resource URI normalization is consistent between `/authorize` and `/token`.
- Token audience/resource mismatch fails closed.

Required evidence:

- HTTP integration tests.
- Local TLC state-space checking for finite TLA+ models.
- Local Z3 finite-universe checks as a secondary SMT sanity gate.
- Fault-injection tests for missing cache, stale cache, corrupted cache, and duplicate registration.

Local formal checks are intentionally excluded from CI/CD and default release
gates. They are run explicitly by maintainers when changing these state
machines.


### P0: Independent State/Fault Discovery

Before a P0/P1 state machine is treated as robust, the project maintains an
independent finite catalog of protocol and environmental states. This catalog is
not generated from current code branches. It exists to expose omitted states such
as storage ENOSPC/fsync/rename failures, duplicate registration, replay, clock
skew, dynamic import failure, malformed upstream responses, cancellation, stale
manifests, and corrupted export assets.

Local commands:

```bash
make discover-state-gaps   # report generated gaps without failing
make verify-state-gaps     # fail while known/uncovered gaps remain
```

These commands are local-only and are not part of CI/CD or default release
gates. A passing TLC/Z3 model without this discovery layer only proves the
current model; it does not prove the model considered externally relevant fault
states.

### P1: Renderer Robustness

Machine-checkable properties:

- Markdown rendering never crashes on malformed markdown.
- LaTeX/formula source is either rendered or shown in a visible diagnostic with
  the source excerpt; it must not silently disappear from the DOM.
- Mermaid rendering failure or delayed processing degrades to a visible
  diagnostic with the diagram source, not app crash or silent content loss.
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
| State/fault discovery | finite obligation catalog | local only: `make discover-state-gaps` / `make verify-state-gaps` |
| OAuth/MCP finite models | TLC model checker | local only: `make verify-formal-tlc` |
| OAuth/MCP SMT sanity | Z3 finite-universe checker | local only: `make verify-formal-smt` |
| Fault injection | pytest/Hypothesis | corrupt cache, partial write, missing client, concurrent write |
| Fuzz | pytest/Hypothesis/fast-check | markdown, JSONL, URL/path, MCP request payloads |

## Local Formal Toolchain

These checks are deliberately not part of CI/CD:

- `make verify-formal-tlc` runs TLC over the checked-in finite TLA+ models and
  requires every reachable queue to be exhausted (`states_left_on_queue = 0`).
- `make verify-formal-smt` runs a Z3-backed finite-universe sanity checker over
  the same core state-machine families. This is not the primary state-space
  enumerator.
- `make verify-formal-local` runs both local formal gates and their focused
  pytest wrappers.
- `make verify-state-gaps` enumerates the bounded adversarial obligation
  catalog. The catalog must have explicit `status`, unique highest-priority
  matches for every state, and evidence files for every implemented obligation.

TLC is the primary tool for exhaustive reachable-state enumeration of the finite
models. Z3 is used for SAT/UNSAT checks over explicitly declared finite
universes; it is not described as a standalone exhaustive state enumerator.

## Current Evidence Snapshot

Last observed local state on 2026-06-15 after removing temporary gap records:

- `make check`: passed; ruff had 0 errors, format check passed, `ty` exited 0 with warnings.
- `uv run python -m compileall -q python tests tools`: passed.
- `make verify-inventory`: passed; manifest coverage reported `coverage ok`.
- Manifest temporary-gap scan: passed; `docs/verification/repo-verification-manifest.yml` contains no `temporary_gap` records.
- `make verify-state-gaps`: passed; `STATE_GAP COVERED total=106560 covered=106560 uncovered=0 known_gaps=0 ambiguous=0 missing_evidence=0`.
- `make verify-formal-local`: passed; TLC checked 4 models with exhausted queues, SMT/Z3 checked 4 finite universes, and the formal gate pytest wrappers passed.
- `make verify-new-tests`: passed; verification tests reported 16 passed / 3 skipped, fuzz-fast passed, docs/version/secret gates passed, and local supply-chain gate had no failing findings. Optional vulnerability scanners (`pip-audit`, `osv-scanner`, `safety`) remain explicitly reported as unavailable local-only scanner gaps.
- `uv run pytest -q`: passed; 2313 passed, 3 skipped, 1 warning.
- `cd frontend && npm test -- --run`: passed; 29 files passed, 120 tests passed.
- `cd frontend && npm run build`: passed with existing dependency/chunk-size warnings.
- `cd frontend && npm audit --audit-level=high`: passed; 0 vulnerabilities.

Therefore the previous full Python test-suite release blocker is resolved in this local run. A future release tag still requires a fresh release-environment run of the mandatory gates.

## Release Gate

A release tag must not be created or pushed unless all mandatory commands for the touched subsystems pass freshly in the release environment.

If any machine-verifiable property has no test/model/check yet, the release note must list it as an assurance gap, not imply coverage.
