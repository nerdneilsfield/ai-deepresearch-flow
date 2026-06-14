# Repository-Wide Formal + Fuzz Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repository-wide formal/fuzz verification program where every tracked source/config/doc surface is inventoried and mapped to machine-verifiable obligations, with formal models for critical state machines and fuzz/fault/property tests for implementation boundaries.

**Architecture:** Generate a source inventory from Python AST, frontend source files, and tracked config/docs. Enforce coverage through `docs/verification/repo-verification-manifest.yml`. Add formal models and executable finite-state checkers for P0/P1 state machines, fuzz/property tests for parsers/renderers/protocols, and deterministic release gates.

**Tech Stack:** Python, pytest, Hypothesis, bounded finite-state model checkers, optional TLA+, TypeScript, Vitest, fast-check, npm audit, existing `rtk make check`.

**Important boundary:** This plan builds a repo-wide verification inventory. It must not report ordinary tests, fuzz, builds, audits, or doc checks as formal proof. Formal status and non-formal evidence status are tracked separately.

---

## Files

- Create: `docs/verification/repo-verification-manifest.yml`
- Create: `docs/verification/repo-verification-inventory.json`
- Create: `tools/verification/generate_inventory.py`
- Create: `tools/verification/check_manifest_coverage.py`
- Create: `tools/verification/check_versions.py`
- Create: `tools/verification/check_doc_secrets.py`
- Create: `tools/verification/check_supply_chain.py`
- Create: `tools/formal/check_all_models.py`
- Create: `tools/formal/check_provider_routing_model.py`
- Create: `tools/formal/check_oauth_client_cache_model.py`
- Create: `tools/formal/check_semantic_ingest_model.py`
- Create: `tools/formal/check_translation_scheduler_model.py`
- Create: `formal/oauth_client_cache/OAuthClientCache.tla`
- Create: `formal/provider_routing/ProviderRouting.tla`
- Create: `formal/semantic_ingest/SemanticIngest.tla`
- Create: `formal/translation_scheduler/TranslationScheduler.tla`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `uv.lock`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `Makefile`
- Add fuzz/fault tests under existing Python test trees.
- Add frontend fuzz/property tests under `frontend/src/__tests__/`.

## Scale Gate

This plan is not complete because it is long. It is complete only if the generated inventory and manifest cover the actual repository.

Observed local repository scale on 2026-06-15:

```text
git tracked files: 1509
python production files: 117
python test files: 94
python functions/methods: 1356
python async functions/methods: 178
python classes: 176
frontend TypeScript files: 80
frontend Vue files: 87
frontend test files: 26
documentation markdown files under docs/root subset: 57
tracked markdown files including openspec/agent docs: 292
tracked PDF.js/vendor/static asset files: 734
```

Required implication:

```text
The implementation must not rely on a hand-written several-thousand-line list.
It must generate the source inventory and fail if any tracked source/config/doc node is unmapped.
```

The manifest generated from this plan is expected to be substantially larger than the plan itself.

## Non-Negotiable Gate Semantics

- `generate_inventory.py` starts from `git ls-files`, not a whitelist.
- Every tracked file is classified, including workflows, scripts, docs, examples, lockfiles, assets, generated files, and ignored files.
- Deploy/release/supply-chain surfaces are included in Task 1, not deferred to docs checks.
- `ignored` and `generated` still require a manifest reason.
- Python symbol matching uses stable IDs: `py:<module>:<qualname>#<signature_hash>`; line spans are metadata only.
- Frontend matching uses stable IDs: `fe:<path>:<export_or_component>#<hash>` or `fe:<path>:component@file` for conservative Vue component coverage; source spans are metadata only.
- Vendored/generated/static trees use artifact groups with file count/checksum/source/license metadata.
- Config files expose config-item rows where practical.
- Test files are evidence assets with targets and commands; they are not recursively treated as production code needing equivalent formal proof.
- `formal_status` is separate from `evidence_status`.
- P0/P1 stateful systems require both `MODEL_PROOF` and `IMPLEMENTATION_REFINEMENT_CHECK`, unless an explicit temporary gap is recorded.
- Fuzz/property/fault tests must follow `AGENTS.md` black-box rules.
- `make verify-inventory` must fail on uncovered inventory items, missing referenced evidence files, invalid P0 gap usage, or suspected boundary items that are unclassified.
- Existing full-suite failures get a baseline ledger; new verification gates must be independently green, while strict release remains blocked.

## Task 0: Freeze and Baseline

- [ ] Capture git state.

```bash
git status --short --branch
git log --oneline --decorate -5
```

Expected: local changes are visible. Do not tag/push.

- [ ] Capture current verification baseline.

```bash
rtk make check
uv run python -m compileall -q python tests
uv run pytest
cd frontend && npm test -- --run
cd frontend && npm run build
cd frontend && npm audit --audit-level=high
```

Expected: report exact failures. Current known blocker: full `uv run pytest` fails. This task does not fix it; it records the baseline.

- [ ] Create a machine-readable baseline failure ledger for the current full pytest failures.

Create:

```text
docs/verification/baseline-pytest-failures.json
```

Required fields:

```text
generated_at
command
exit_code
failed_nodeids[]
failure_count
passed_count optional
notes
```

Acceptance: new verification tests can be run independently without pretending the release suite is green.

## Task 1: Repository Inventory Generator

- [ ] Write failing test for `tools/verification/generate_inventory.py` behavior.

Test behavior only:

```text
Given a temporary git repo tree with tracked Python, root-level Python, tests, TS, Vue, TOML, JSON, Markdown, YAML, shell, workflow, Dockerfile, compose, nginx template, supervisor config, tracked assets, and generated/vendor-like files,
the inventory command emits every tracked path,
classifies every path,
emits stable Python symbol IDs for functions/classes/methods,
emits component-level Vue IDs with conservative component metadata,
emits config item IDs for parseable config surfaces,
emits evidence asset rows for test files,
emits artifact group rows for vendored/generated/static asset prefixes,
and keeps generated/ignored files in inventory with required reasons instead of silently dropping them.
```

Run:

```bash
uv run pytest tests/verification/test_generate_inventory.py -q
```

Expected before implementation: fail because tool does not exist.

- [ ] Implement `tools/verification/generate_inventory.py`.

Required CLI:

```bash
uv run python tools/verification/generate_inventory.py --output docs/verification/repo-verification-inventory.json
uv run python tools/verification/generate_inventory.py --check
```

Required output keys:

```text
version
repo_root: "." or repo-relative metadata only
items[].path
items[].kind
items[].classification
items[].ignore_reason optional
items[].generated_reason optional
items[].artifact_group_id optional
items[].symbols[].name
items[].symbols[].kind
items[].symbols[].stable_id
items[].symbols[].source_span
items[].symbols[].signature_hash optional
items[].symbols[].async optional
items[].symbols[].decorators optional
items[].flags.security_boundary
items[].flags.persistence_boundary
items[].flags.network_boundary
items[].flags.parser_boundary
items[].flags.renderer_boundary
items[].flags.concurrency_boundary
items[].flags.deploy_security_boundary
items[].flags.secret_boundary
items[].flags.runtime_config_boundary
items[].flags.release_publish_boundary
items[].flags.suspected_boundary_unclassified
config_items[].config_id
config_items[].path
config_items[].selector
config_items[].kind
config_items[].criticality
evidence_assets[].evidence_id
evidence_assets[].path
evidence_assets[].target_inventory_ids[]
evidence_assets[].evidence_class
evidence_assets[].command
evidence_assets[].black_box_contract
artifact_groups[].artifact_group_id
artifact_groups[].path_prefix
artifact_groups[].upstream_name
artifact_groups[].file_count
artifact_groups[].checksum_manifest
artifact_groups[].validation_command
```

Required behavior:

```text
Inventory source is `git ls-files`.
Release mode inventory source is `git ls-files`.
Dev/precommit mode inventory source is `git ls-files --cached --others --exclude-standard`.
Cache/build directories are ignored only when untracked or explicitly classified with reason.
Known tracked generated/asset files appear as generated/asset entries.
Root files such as AGENTS.md, CLAUDE.md, Makefile, package lockfiles, workflow files, scripts, and docs are included.
Boundary classification is conservative and based on path/import/name heuristics.
Workflow, Docker, compose, shell startup, nginx template, supervisor config, and docker ignore files are classified as deploy/release/supply-chain surfaces.
Tracked markdown outside docs, including openspec and agent docs, is included.
Tracked PDF.js/vendor/static trees are grouped as artifact groups unless intentionally expanded.
```

Temporary repo test fixture must include:

```text
.github/workflows/push-to-pypi.yml
.github/workflows/docker-images.yml
.dockerignore
scripts/docker/Dockerfile.base
scripts/docker/Dockerfile.deploy
scripts/docker/docker-compose.example.yml
scripts/docker/start-api.sh
scripts/docker/start-nginx.sh
scripts/docker/nginx.conf.http.template
scripts/docker/supervisord.conf
```

Assertions must verify these paths are inventoried and classified with one of:

```text
ci_workflow
release_publish_workflow
container_build
compose_config
startup_script
reverse_proxy_config
process_supervisor_config
docker_ignore
```

- [ ] Run generator.

```bash
uv run python tools/verification/generate_inventory.py --output docs/verification/repo-verification-inventory.json
```

Expected: JSON file created, deterministic sorted order.

## Task 2: Coverage Manifest Gate

- [ ] Write failing test for `tools/verification/check_manifest_coverage.py`.

Behavior:

```text
Given inventory item A and manifest missing A, checker exits non-zero.
Given inventory item A and manifest covering A, checker exits zero.
Given inventory symbol S and manifest missing S, checker exits non-zero.
Given inventory config item C and manifest missing C, checker exits non-zero.
Given generated/artifact item without justification, checker exits non-zero.
Given P0 item without formal model plus conformance or temporary gap, checker exits non-zero.
Given manifest evidence path that does not exist, checker exits non-zero.
Given duplicate inventory or manifest IDs, checker exits non-zero.
Given stale manifest item absent from inventory, checker exits non-zero unless explicitly marked retired.
```

Run:

```bash
uv run pytest tests/verification/test_check_manifest_coverage.py -q
```

Expected before implementation: fail.

- [ ] Implement manifest checker.

Required CLI:

```bash
uv run python tools/verification/check_manifest_coverage.py \
  --inventory docs/verification/repo-verification-inventory.json \
  --manifest docs/verification/repo-verification-manifest.yml
```

Failure output must list uncovered paths/symbols.

- [ ] Create initial `docs/verification/repo-verification-manifest.yml`.

Required first pass:

```text
Every current source/config/doc path appears.
P0/P1 items include real planned coverage entries.
Non-critical docs/configs include static/build/doc checks.
Any gap has reason + follow_up.
formal_status and evidence_status are separate.
Every PROPERTY_TEST/FUZZ_TEST/FAULT_INJECTION has observable_contract.
Every FORMAL_MODEL has checker_command and counterexample format.
Every P0 stateful FORMAL_MODEL has IMPLEMENTATION_REFINEMENT_CHECK or temporary_gap.
Config items are covered separately from file-level config coverage.
Evidence assets name target inventory IDs and command.
Artifact groups include upstream/source/license/file-count/checksum/validation metadata.
```

Required manifest fields:

```yaml
version: 1
items:
  - path: python/deepresearch_flow/paper/snapshot/auth.py
    classification: source
    criticality: P0
    owner: research-platform
    release_blocking: true
    flags:
      security_boundary: true
      persistence_boundary: true
    formal_status: finite_model
    evidence_status:
      - unit
      - fuzz
      - fault
      - conformance
    symbols:
      - stable_id: py:deepresearch_flow.paper.snapshot.auth:JsonOAuthClientCache.put@...
        observable_contract: "put/get/reopen preserves successfully persisted clients; failed writes are not durable"
        coverage:
          - kind: MODEL_PROOF
            path: tools/formal/check_oauth_client_cache_model.py
            checker_command: "uv run python tools/formal/check_oauth_client_cache_model.py --depth 6"
          - kind: IMPLEMENTATION_REFINEMENT_CHECK
            path: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_model_conformance.py
          - kind: FAULT_INJECTION
            path: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_faults.py
config_items:
  - config_id: toml:pyproject.toml:project.version
    path: pyproject.toml
    selector: project.version
    kind: version
    criticality: P1
    validation:
      command: "uv run python tools/verification/check_versions.py"
evidence_assets:
  - evidence_id: pytest:python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py
    path: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py
    target_inventory_ids:
      - py:deepresearch_flow.paper.snapshot.auth:JsonOAuthClientCache#...
    evidence_class: UNIT_BLACK_BOX
    command: "uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py -q"
    black_box_contract: "observes public cache behavior only"
artifact_groups:
  - artifact_group_id: vendor:pdfjs-web-static
    path_prefix: python/deepresearch_flow/paper/web/pdfjs
    upstream_name: pdfjs
    file_count: 370
    checksum_manifest: docs/verification/artifacts/pdfjs-web-static.sha256
    validation_command: "uv run python tools/verification/check_artifact_groups.py"
    justification: vendored static runtime asset bundle
```

Coverage checker must validate:

```text
referenced files exist
checker commands are syntactically runnable in dry-run/list mode where applicable
P0 does not rely only on MANUAL_GAP_EXPLAINED
formal_status is not inferred from unit/fuzz/build evidence
suspected_boundary_unclassified items fail
manual downgrade has reviewer, reason, and expiry/follow-up
generated/vendor asset entries have artifact group coverage or explicit reason
config_items and evidence_assets are cross-referenced by stable IDs
test evidence assets are not recursively required to have the same coverage as production code
```

## Task 3: Formal Model Harness

- [ ] Write failing test for `tools/formal/check_all_models.py` behavior.

Behavior:

```text
If one model checker exits non-zero, check_all_models exits non-zero.
If all configured model checkers exit zero, check_all_models exits zero.
```

- [ ] Implement `tools/formal/check_all_models.py`.

Required CLI:

```bash
uv run python tools/formal/check_all_models.py
uv run python tools/formal/check_all_models.py --deep
```

Required output:

```text
model name
state count or checked bound
invariants checked
negative-control status when requested
```

Required semantics:

```text
TLA+ files count as gated formal evidence only if TLC/Apalache command is configured and runnable.
If no TLA runner is available, the Python finite-state checker is the gated MODEL_PROOF and the TLA file is supporting documentation.
Negative controls must emit invariant name and counterexample trace, not just exit non-zero.
P0 deep profile must use documented finite domains and bound rationale.
```

## Task 4: P0 Formal Model - OAuth Client Cache / MCP Auth

- [ ] Create TLA+ spec files:

```text
formal/oauth_client_cache/OAuthClientCache.tla
formal/oauth_client_cache/OAuthClientCache.cfg
```

Required invariants:

```text
NoTokenWithoutRegisteredClient
FailedPersistNotDurable
CorruptCacheDoesNotGrant
RestartPreservesSuccessfulPersist
ResourceMismatchFailsClosed
DeleteRevokesFutureToken
```

- [ ] Create finite-state checker:

```text
tools/formal/check_oauth_client_cache_model.py
```

Required commands:

```bash
uv run python tools/formal/check_oauth_client_cache_model.py --depth 6
uv run python tools/formal/check_oauth_client_cache_model.py --inject-bug failed-persist-durable
```

Expected: normal command exits 0; injected bug exits non-zero with counterexample.

- [ ] Add model-to-implementation conformance tests.

Required behavior:

```text
Accepted model traces replay through public JsonOAuthClientCache methods and HTTP OAuth endpoints where applicable.
Rejected/counterexample traces become black-box regression tests when representable.
The conformance test uses observable state only: return values, persisted cache after reopen, HTTP status/headers/body.
```

- [ ] Add/update black-box fuzz/fault tests:

```text
python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_fuzz.py
python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_faults.py
python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py
```

Required behaviors:

```text
corrupt cache grants no client
write failure is not durable after reopen
unknown valid client can re-enter safe auth flow
malformed client fails closed
resource host mismatch fails closed
```

## Task 5: P0 Formal Model - Provider Routing / Active Windows

- [ ] Create:

```text
formal/provider_routing/ProviderRouting.tla
tools/formal/check_provider_routing_model.py
```

Required invariants:

```text
InactiveRouteNotSelected
CooldownRouteNotSelected
QuotaExhaustedRouteNotSelected
RetryBudgetFinite
PositiveWeightRequired
```

- [ ] Add property/fault tests covering:

```text
python/deepresearch_flow/paper/routing.py
python/deepresearch_flow/paper/active_window.py
python/deepresearch_flow/paper/config.py
python/deepresearch_flow/paper/providers/*.py
```

Required behaviors:

```text
Generated configs with invalid weights are rejected.
Generated active windows either include current time or are not selected.
Generated cooldown states never select cooled route.
Upstream 429 moves route to quota/cooldown state.
```

## Task 6: P0 Formal Model - Semantic Ingest / Vector Store

- [ ] Create:

```text
formal/semantic_ingest/SemanticIngest.tla
tools/formal/check_semantic_ingest_model.py
```

Required invariants:

```text
CommitOnlyAfterAllParts
FailedApplyDoesNotMarkComplete
ReingestUnchangedIsIdempotent
DeleteTouchesOnlyTargetGroup
NoDuplicateChunkIdsPerGroup
```

- [ ] Add property/fault tests covering:

```text
python/deepresearch_flow/paper/vector_store.py
python/deepresearch_flow/paper/snapshot/admin.py
python/deepresearch_flow/paper/snapshot/push_semantic.py
```

Required behaviors:

```text
random chunk groups generate stable IDs
partial staged ingest is not visible as committed
write failure leaves recoverable/error state
schema mismatch returns structured error
```

## Task 7: P1 Formal Model - Translation Scheduler

- [ ] Create:

```text
formal/translation_scheduler/TranslationScheduler.tla
tools/formal/check_translation_scheduler_model.py
```

Required invariants:

```text
CompletedSegmentNotLost
MissingSegmentNotMarkedComplete
RetryBudgetFinite
ProgressMonotonic
FailureReported
```

- [ ] Add fuzz/property tests for:

```text
python/deepresearch_flow/translator/segment.py
python/deepresearch_flow/translator/protector.py
python/deepresearch_flow/translator/fixers.py
python/deepresearch_flow/translator/scheduler.py
python/deepresearch_flow/translator/engine.py
```

Required behaviors:

```text
placeholder protection round-trips generated protected spans
malformed markdown fixer never crashes
segment merge preserves order
scheduler reports failed segment explicitly
```

## Task 8: Advanced Search Formal/Property Coverage

- [ ] Add finite-state/pipeline checker or property model for:

```text
python/deepresearch_flow/paper/snapshot/advanced/pipeline.py
python/deepresearch_flow/paper/snapshot/advanced/filters.py
python/deepresearch_flow/paper/snapshot/advanced/fusion.py
python/deepresearch_flow/paper/snapshot/advanced/mmr.py
python/deepresearch_flow/paper/snapshot/advanced/dedup.py
```

Required properties:

```text
dedup never increases result count
limit is respected
MMR output has unique paper IDs
adding filters cannot increase matching set for same corpus
empty/malformed inputs return structured errors
```

- [ ] Add request-spec fuzz tests.

Command:

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests -k 'advanced or search' -q
```

## Task 9: Snapshot/Web/API Path and Serialization Fuzz

- [ ] Add fuzz/property tests for:

```text
python/deepresearch_flow/paper/snapshot/bibtex_utils.py
python/deepresearch_flow/paper/snapshot/bibtex_match.py
python/deepresearch_flow/paper/snapshot/identity.py
python/deepresearch_flow/paper/snapshot/image_utils.py
python/deepresearch_flow/paper/snapshot/supplement.py
python/deepresearch_flow/paper/web/query.py
python/deepresearch_flow/paper/web/static_assets.py
python/deepresearch_flow/paper/web/markdown.py
```

Required behaviors:

```text
path normalization never escapes configured roots
identity keys are deterministic
bibtex malformed input returns structured failure or safe parse result
query parser never throws for generated strings
markdown renderer preserves protected regions or returns fallback
```

## Task 10: Frontend Repo-Wide Property/Fuzz Coverage

- [ ] Add `fast-check` dev dependency.

Files:

```text
frontend/package.json
frontend/package-lock.json
```

- [ ] Add property/fuzz tests for public utilities:

```text
frontend/src/lib/static-base.ts
frontend/src/lib/http.ts
frontend/src/lib/markdown-normalize.ts
frontend/src/lib/markdown-enhance.ts
frontend/src/lib/selected-export.ts
frontend/src/lib/selection-db.ts
frontend/src/lib/token-db.ts
frontend/src/lib/paper-content-cache.ts
frontend/src/lib/snippet.ts
frontend/src/lib/module-interop.ts
```

Required behaviors:

```text
URL normalization is deterministic
encoded query params preserve filters
markdown normalization does not throw for arbitrary strings
archive paths never include traversal
JSONL output parses per line
token DB does not return expired token as valid
content cache keys do not collide for different paper/template/version
```

## Task 11: Frontend Renderer Fault Injection

- [ ] Add component tests for:

```text
frontend/src/components/MarkdownContent.vue
frontend/src/components/MarkdownPanel.vue
frontend/src/components/RenderedMarkdown.vue
frontend/src/components/PdfViewer.vue
frontend/src/components/ErrorBoundary.vue
```

Required behaviors:

```text
KaTeX warning/error does not crash app
Mermaid load/render failure shows fallback
PDF.js missing/chunk failure shows fallback
ErrorBoundary catches thrown child render error
```

Run:

```bash
cd frontend && npm test -- --run
```

## Task 12: CLI/Config/OCR/Storage Fuzz and Fault Injection

- [ ] Add property/fault tests for:

```text
python/deepresearch_flow/ocr/config.py
python/deepresearch_flow/ocr/factory.py
python/deepresearch_flow/ocr/runner.py
python/deepresearch_flow/storage/config.py
python/deepresearch_flow/storage/factory.py
python/deepresearch_flow/storage/webdav.py
python/deepresearch_flow/recognize/*.py
```

Required behaviors:

```text
malformed config is rejected with structured error
missing OCR backend fails with structured error
storage upload failure is reported
generated remote paths are normalized safely
recognize math/mermaid extraction never crashes for arbitrary markdown
```

## Task 13: Documentation / Version / Secret Gates

- [ ] Implement:

```text
tools/verification/check_versions.py
tools/verification/check_doc_secrets.py
tools/verification/check_supply_chain.py
```

Required checks:

```text
Python and frontend package versions match release target when release mode is set.
Docs, examples, workflows, Docker files, and startup scripts do not contain real private hosts/tokens.
Example config keys match known config keys where feasible.
Local markdown links resolve.
```

Required secret scan contract:

```text
allowed placeholders: example.com, localhost, 127.0.0.1, YOUR_TOKEN, your-token, env:*
blocked private hosts: RFC1918, CGNAT, link-local, private IPv6, internal suffix denylist
blocked tokens: GitHub/provider API keys/cloud credentials/bearer/static token/high-entropy key-like strings
scan paths: docs, README, example config, scripts/docker, .github/workflows
failure output: redacted path:line
```

Required supply-chain checks:

```text
lock consistency for Python/root npm/frontend npm
Python known-vulnerability audit when scanner is installed/configured
root npm audit
frontend npm audit
GitHub Actions lint/pinning check when actionlint is installed/configured
Docker compose config validation
Dockerfile/base image pinning check
package build + twine check when release mode is enabled
```

- [ ] Add Makefile target.

```bash
make verify-docs
make verify-supply-chain
```

Expected: exits non-zero on version mismatch, secret-like examples, broken local links, invalid compose config, or required audit failure. Optional scanners that are unavailable must be reported as explicit gaps, not silently skipped.

## Task 14: Global Gates

- [ ] Add Makefile targets:

```text
verify-inventory
verify-formal
verify-fuzz-fast
verify-fault
verify-supply-chain
verify-new-tests
verify-known-baseline
verify-repo-strict
```

Required commands:

```bash
make verify-inventory
make verify-formal
make verify-fuzz-fast
make verify-fault
make verify-supply-chain
make verify-repo-strict
```

Expected:

```text
verify-inventory fails if any source/config/doc file or production symbol is uncovered.
verify-formal fails if any formal checker or negative-control expectation fails.
verify-fuzz-fast runs deterministic bounded fuzz/property tests.
verify-fault runs deterministic fault-injection tests.
verify-supply-chain runs lock/audit/workflow/container config checks and reports unavailable optional scanners as explicit gaps.
verify-new-tests runs only newly added verification tests and must be green independently of existing full-suite failures.
verify-known-baseline compares current full pytest failures against docs/verification/baseline-pytest-failures.json.
verify-repo-strict runs all release-blocking checks including full pytest.
```

## Task 15: Manifest Completion and Gap Accounting

- [ ] Populate `docs/verification/repo-verification-manifest.yml` from generated inventory.

Rules:

```text
Every path has at least one coverage class.
Every production symbol has coverage or explicit ignore reason.
Every config item has validation or explicit gap.
Every evidence asset names target IDs and command.
Every generated/vendor asset group has source/license/checksum/file-count/validation or explicit gap.
Every P0 item has formal/fuzz/fault coverage or explicit temporary gap.
Every gap has reason, risk, and follow-up.
```

- [ ] Run:

```bash
uv run python tools/verification/generate_inventory.py --check
uv run python tools/verification/check_manifest_coverage.py \
  --inventory docs/verification/repo-verification-inventory.json \
  --manifest docs/verification/repo-verification-manifest.yml
```

Expected: zero uncovered paths/symbols.

## Task 16: Final Verification Before Commit

- [ ] Run strict gate.

```bash
make verify-repo-strict
```

Expected: pass before any release claim. If full pytest still fails, do not tag/release.

- [ ] Review diff.

```bash
git diff --stat
git diff -- docs/verification docs/plans formal tools Makefile pyproject.toml requirements.txt frontend/package.json frontend/package-lock.json
```

- [ ] Commit only after fresh verification evidence.

Commit message format:

```bash
git add docs/verification docs/plans formal tools Makefile pyproject.toml requirements.txt uv.lock frontend/package.json frontend/package-lock.json python frontend

git commit -m "test: add repo-wide formal fuzz gates"
```

## Completion Criteria

This plan is complete only when:

1. generated inventory covers every tracked file in release mode and tracked+untracked-candidate files in dev mode,
2. generated inventory includes production symbols, config items, evidence assets, and artifact groups,
3. manifest coverage checker fails on any uncovered path/symbol/config item/evidence asset/artifact group,
4. at least four P0/P1 formal models exist and are executable,
5. each formal model has a negative-control failure mode and conformance path or explicit temporary gap,
6. Python fuzz/fault tests exist for critical parsers/protocol/persistence paths,
7. frontend fast-check/component fault tests cover renderer/export/persistence boundaries,
8. documentation/version/secret/supply-chain gates exist,
9. `make verify-repo-strict` is the release gate,
10. current full pytest failures are resolved or explicitly release-blocking.
