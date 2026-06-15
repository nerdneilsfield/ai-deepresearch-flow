# Repository-Wide Formal + Fuzz Verification Design

**Status:** Draft design for review. No implementation included.

**Scope:** The whole repository, not only MCP/OAuth. Every tracked source/config/build/documentation surface must be inventoried and mapped to a machine-verifiable obligation. Critical stateful/security/data-integrity paths get formal models. Pure functions get contracts/properties. Parsers/renderers/protocol boundaries get fuzzing. Persistence/concurrency paths get fault injection. Files that cannot be formally modeled must still have an explicit machine-checkable coverage class and an explicit gap entry.

**Naming constraint:** This is a repository-wide verification program, not a claim that the entire repository is formally proven. Formal coverage and non-formal machine evidence must be reported separately. Unit tests, fuzz tests, build checks, audits, and doc checks are machine-verifiable evidence, but they are not formal proofs.

## Core Requirement

The repository must have a generated verification inventory. A file, function, endpoint, CLI command, frontend component, or config surface is not allowed to be silently uncovered.

```text
repo source graph
  -> generated inventory
  -> verification manifest
  -> formal/spec/property/fuzz/fault/static/build obligations
  -> machine gate fails if inventory item has no obligation
```

This is the key change from the previous OAuth-only draft: **coverage is enforced from the repository inventory outward**, not by manually listing favorite subsystems.

## Review-Driven Hard Requirements

The adversarial review found that implementation must not begin from a weak inventory/gate schema. These are hard requirements for Task 1/2:

1. Inventory starts from `git ls-files`, not from a hand-written whitelist.
2. Every tracked file is classified as one of: `source`, `test`, `config`, `build`, `workflow`, `script`, `doc`, `asset`, `generated`, or `ignored`.
3. `ignored` and `generated` entries still appear in the inventory and must include a machine-checkable reason.
4. Every production Python function/method uses a stable symbol ID, not a bare name.
5. Every frontend exported symbol/component/store/composable uses a stable symbol ID, not a bare file path.
6. Formal coverage and ordinary verification coverage are separate fields and are not mixed in coverage percentages.
7. P0/P1 stateful systems require both a model proof/check and an implementation conformance check; the strict local inventory gate rejects temporary gap records.
8. Boundary/criticality classification is conservative: suspected auth/token/cache/db/network/path/rendering code is promoted to review rather than silently downgraded.
9. Tests for fuzz/property/fault coverage must obey `AGENTS.md` black-box rules.
10. Existing full-suite failures are tracked in a baseline ledger so new verification gates can go green without hiding the release blocker.
11. Vendored/generated/static asset trees are represented by artifact groups with checksum/license/source metadata, not hundreds of fake per-symbol obligations.
12. Config files are inventoried at config-item granularity where practical.
13. Test files are evidence assets, not recursively treated as production code requiring equivalent formal proof.

## Scale Reality Check

This design document is not the coverage artifact. A few thousand hand-written lines are not enough for this repository.

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

Therefore the required deliverable is not a static plan with several thousand lines. The required deliverable is:

```text
1. a generator that enumerates every relevant repo node,
2. a generated inventory artifact,
3. a manifest that maps every inventory item to verification obligations,
4. a gate that fails on any uncovered file/symbol/component/config/doc,
5. formal/fuzz/fault/static checks referenced by that manifest.
```

The generated manifest may be much larger than this design. That is expected. Manual prose is only the architecture; the generated inventory/manifest is the coverage source of truth.


## Adversarial State/Fault Discovery Layer

Formal models must not be written only from the current implementation. Before a
state machine is treated as meaningful, the repository must maintain an
independent finite state/fault obligation catalog derived from:

1. official protocol specifications for protocol states,
2. generic storage/network/runtime/browser fault taxonomies,
3. production incident classes already seen by the project, and
4. deliberately adversarial edge cases that are not represented by current code branches.

The catalog lives at:

```text
docs/verification/state-space-obligations.yml
```

The discovery tool lives at:

```text
tools/formal/discover_state_gaps.py
```

It enumerates the bounded cross-product of each subsystem's declared dimensions
and classifies every generated state as one of:

```text
handled / fail_closed / degrade_safely / retry_or_recover / known_gap / uncovered
```

States marked `known_gap` are not success. They are machine-discovered work
items. `make discover-state-gaps` reports them without failing so maintainers can
inspect the search space. `make verify-state-gaps` fails until known/uncovered
gaps are removed or converted into executable handling evidence. Neither target
is part of CI/CD or default `make check`; it is a local robustness tool.

This layer is intentionally different from model checking:

- TLC exhausts reachable states of a finite model.
- Z3 checks finite-universe logical obligations and negative controls.
- State/fault discovery asks whether the model itself omitted externally relevant
  states before the model is trusted.

Acceptance rule: a P0/P1 state machine cannot be called robust merely because its
current TLA+/SMT model passes. It must also have no untriaged discovery gaps for
the finite state/fault catalog that applies to that subsystem.

## Definitions

### Formal artifact

A formal artifact is executable or mechanically checkable and contains:

1. state variables or typed function contract,
2. initial state or preconditions,
3. transition relation or postconditions,
4. invariants/properties,
5. checker command,
6. bounded/unbounded assumptions,
7. at least one negative-control check proving the checker catches a modeled bug.

Examples:

- TLA+ state machine + TLC/Apalache command.
- Python finite-state model checker with exhaustive bounded exploration.
- CrossHair/icontract/deal-style symbolic contract checking for pure Python functions.
- TypeScript property specs with fast-check for pure utilities.

Formal artifacts must have a `checker_command`, expected exit behavior, and counterexample format. A checked-in TLA+ file that is never executed by a gate is documentation, not gated formal evidence.

### Implementation conformance artifact

A formal model proves only the model. For stateful production code, the repository also needs conformance evidence linking the model to implementation behavior.

Each P0/P1 stateful model must define:

1. public implementation operations that correspond to model actions,
2. an abstraction function from observable implementation state to model state,
3. accepted trace replay tests against public APIs,
4. rejected/counterexample trace replay tests where feasible,
5. a command that runs the conformance check.

Manifest entries must distinguish:

```text
MODEL_PROOF
IMPLEMENTATION_REFINEMENT_CHECK
```

P0 stateful items cannot count as formally covered unless both are present. A temporary gap is a blocker, not coverage.

### Fuzz/property artifact

A fuzz/property artifact generates many inputs and asserts behavior-level properties against the real implementation. It is not a proof, but it is required for parsers, normalizers, protocol payloads, file paths, rendering inputs, and serialization formats.

### Fault-injection artifact

A fault-injection artifact forces environmental or persistence failures and asserts fail-closed / recoverable behavior.

Examples:

- write failure,
- partial file,
- corrupted JSON,
- dependency import failure,
- timeout,
- concurrency interleaving,
- restart with stale state.

### Coverage class

Every inventory row must have one or more coverage classes:

```text
FORMAL_MODEL
SYMBOLIC_CONTRACT
PROPERTY_TEST
FUZZ_TEST
FAULT_INJECTION
TYPE_STATIC
LINT_STATIC
UNIT_BLACK_BOX
INTEGRATION_BLACK_BOX
BUILD_CHECK
AUDIT_CHECK
DOC_LINK_CHECK
MANUAL_GAP_EXPLAINED
```

`MANUAL_GAP_EXPLAINED` is allowed only in design notes and non-release exploratory reports. The checked repository manifest must not contain temporary gap records.

### Formal status and evidence status

Coverage classes are not interchangeable. The manifest must track formal status separately:

```text
formal_status:
  none | contract | finite_model | tla_checked | theorem

evidence_status:
  static | unit | integration | property | fuzz | fault | conformance | build | audit | doc
```

The gate must report at least:

```text
total inventory items
items with any machine evidence
items with formal_status != none
P0 items with model + conformance
P0 items with missing executable evidence
uncovered items
```

It must never report unit/fuzz/build coverage as formal coverage.

## Black-Box Property/Fuzz/Fault Rules

All verification tests must follow `AGENTS.md`:

1. Provide only module path, function name, parameter types, return types, and plain-language expected behavior.
2. Assertions are input/output only.
3. Internal/private helpers are tested through their signature and behavior, not implementation steps.
4. Do not assert internal branches, private fields, call counts, prompt text, regex implementation, temp file names, or ordering of internal calls.
5. Fault tests assert public/observable behavior: return value, exception type, persisted state after reopen, HTTP/CLI response, filesystem result, or visible UI fallback.
6. Symbolic contracts must be derived from external behavior specs, not reverse-engineered from current implementation logic.

Each manifest entry for `PROPERTY_TEST`, `FUZZ_TEST`, or `FAULT_INJECTION` must include an `observable_contract` field.

## Repository Inventory Dimensions

### Python source inventory

Generated from `git ls-files` first, then parsed with AST for Python files. Test files are inventoried separately as verification assets, not silently excluded.

Rows:

```text
file_path
module_name
class_name optional
function_name optional
stable_symbol_id
async bool
public/private
signature_hash
decorators
lineno
end_lineno
cyclomatic or branch proxy
io_boundary bool
security_boundary bool
persistence_boundary bool
network_boundary bool
parser_boundary bool
concurrency_boundary bool
```

Stable Python symbol ID:

Primary IDs must not depend on line numbers because harmless comment insertions would churn the manifest. The stable ID form is:

```text
py:<module>:<qualname>#<signature_hash>
```

Line spans are metadata only. Nested/local symbols use lexical qualname plus an occurrence hash when needed.

### Frontend source inventory

Generated from `git ls-files` first, then parsed for `frontend/src/**/*.{ts,vue}`. Vue files must be represented as components even if symbol extraction is conservative.

Rows:

```text
file_path
symbol/component/export name optional
stable_symbol_id
source_span optional
export_mode optional
component bool
composable bool
store bool
router bool
network_boundary bool
renderer_boundary bool
persistence_boundary bool
parser_boundary bool
```

Stable frontend symbol ID:

Primary frontend IDs must not depend on source spans. The stable ID form is:

```text
fe:<path>:<export_or_component_name>#<signature_or_export_hash>
```

If a `.vue` file cannot be parsed precisely, the component-level ID is still required:

```text
fe:<path>:component@file
```

Vue component inventory must also record conservative metadata:

```text
component_id
props[]
emits[]
slots_unknown bool
uses_store bool
uses_router bool
uses_network bool
uses_storage bool
uses_rendering bool
needs_manual_symbol_review bool
```

### Config/build/documentation inventory

Generated from all tracked files. The following are examples, not the complete source of truth:

```text
pyproject.toml
requirements.txt
uv.lock
package.json
package-lock.json
frontend/package.json
frontend/package-lock.json
Makefile
README*.md
docs/**/*.md
config.example.toml
ocr.example.toml
remote.example.toml
.github/**/*.yml
scripts/**/*.sh
Dockerfile
Dockerfile*
scripts/docker/**
docker-compose*.yml
.dockerignore
nginx*.template
supervisord.conf
AGENTS.md
CLAUDE.md
openspec/**/*.md
```

Rows:

```text
file_path
kind
required validation command
secret-leak scan bool
version consistency bool
link/reference check bool
deploy_security_boundary bool
secret_boundary bool
runtime_config_boundary bool
release_publish_boundary bool
```

Config item rows:

```text
config_id
path
selector / JSON pointer / TOML dotted key / YAML path
kind
criticality
validation
owner optional
gap optional
```

Evidence asset rows:

```text
evidence_id
path
target_inventory_ids[]
evidence_class
command
black_box_contract
deterministic_mode
deep_mode optional
owner optional
```

Artifact group rows for vendored/generated/static assets:

```text
artifact_group_id
path_prefix
upstream_name
upstream_version optional
license optional
source_url optional
file_count
checksum_manifest
validation_command
owner
justification
```

The two tracked PDF.js/static trees must be represented as artifact groups unless intentionally expanded.

## Boundary and Criticality Classification

The generator must classify boundaries conservatively. It must flag suspected boundary code using path, imports, decorators, symbol names, and config keys.

Minimum promotion rules:

```text
auth/token/oauth/bearer/jwt/client/cache/session -> security_boundary, P0 review
db/sql/lance/vector/store/migrate/schema -> persistence_boundary, P0/P1 review
http/api/request/response/route/fastmcp/starlette/uvicorn -> network_boundary
path/file/fs/temp/zip/static/export/upload -> io_boundary and path_safety review
markdown/latex/katex/mermaid/pdf/html/render -> renderer_boundary/parser_boundary
async/thread/lock/concurrent/scheduler/retry/cooldown/quota -> concurrency_boundary
.github/workflows/push/publish/release/pypi/docker -> release_publish_boundary, supply_chain review
Dockerfile/docker-compose/nginx/supervisor/start-api/start-nginx -> deploy_security_boundary, runtime_config_boundary
secret/token/key/password/env/cors/origin/issuer/public_base -> secret_boundary or runtime_config_boundary
```

Manual downgrades require:

```text
reviewer
reason
expiry date or follow-up issue
```

The gate must fail on `suspected_boundary_unclassified` items.

## Fuzz Reproducibility and Baseline Policy

Fuzz and property tests must be deterministic in fast gates and exploratory in deep gates.

Required Python pytest markers:

```text
fuzz_fast
fuzz_deep
fault
slow
no_network
```

Required reproducibility metadata:

```text
Hypothesis profile
HYPOTHESIS_SEED or pytest --hypothesis-seed when used
failing example repr/json
dependency lock hash
platform and Python/Node versions
```

Required frontend metadata:

```text
fast-check seed
fast-check path
numRuns
failing input JSON when serializable
```

Existing full-suite failures must be kept in a machine-readable baseline ledger. New verification gates may run independently, but release gates remain blocked until full-suite failures are resolved or explicitly marked release-blocking.

## Deployment and Supply-Chain Boundaries

Deployment and release files are first-class verification targets, not documentation.

Minimum inventory kinds:

```text
ci_workflow
release_publish_workflow
container_build
compose_config
startup_script
reverse_proxy_config
process_supervisor_config
docker_ignore
runtime_example_config
```

Minimum tracked surfaces:

```text
.github/workflows/**/*.yml
.dockerignore
scripts/docker/Dockerfile*
scripts/docker/docker-compose*.yml
scripts/docker/*.sh
scripts/docker/nginx*.template
scripts/docker/supervisord.conf
scripts/docker/robots.txt
```

Minimum supply-chain checks:

```text
Python lock consistency
Python known-vulnerability audit
root npm audit
frontend npm audit
GitHub Actions lint/pinning check
Docker compose config validation
Dockerfile/base image pinning check
package build check
twine check for distributions
```

Optional but release-mode required when publishing artifacts:

```text
SBOM generation for Python/root npm/frontend npm/container image
container scan with trivy or grype
provenance generation for Docker images
cosign sign/attest if publishing signed images
```

Boundary statement:

```text
Dependency audit detects known advisories only.
SBOM/provenance proves traceability, not dependency correctness.
Pinned locks do not prove already-pinned packages are benign.
Container scans detect known CVEs only, not all runtime compromise.
No claim is made that browsers, npm/PyPI packages, base images, compilers, or GitHub Actions are correct beyond explicit audit/provenance evidence.
```

## Secret and Private Host Scan Policy

Secret scanning must be allowlist-oriented for examples and denylist/entropy-oriented for likely secrets.

Required config shape:

```yaml
allowed_placeholders:
  - example.com
  - localhost
  - 127.0.0.1
  - YOUR_TOKEN
  - your-token
  - env:*
blocked_secret_patterns:
  - github tokens
  - openai/anthropic/provider api keys
  - aws/gcp/azure credentials
  - bearer/static tokens
  - high-entropy quoted strings near token/key/password/secret
blocked_private_hosts:
  - RFC1918 IPv4 unless allowlisted
  - 100.64.0.0/10
  - 169.254.0.0/16
  - private IPv6 ranges
  - internal DNS suffix denylist
scan_paths:
  - docs/**
  - README*
  - *.example.toml
  - scripts/docker/**
  - .github/workflows/**
```

Failure output must redact values and include path/line.

## Repo-Wide Component Map and Required Verification

### 1. CLI Entrypoints

Files:

```text
python/deepresearch_flow/cli.py
python/deepresearch_flow/__main__.py
python/deepresearch_flow/paper/cli.py
python/deepresearch_flow/recognize/cli.py
python/deepresearch_flow/translator/cli.py
python/deepresearch_flow/utils/cli.py
```

Required coverage:

- CLI argument fuzz/property tests for option combinations.
- Snapshot tests for help output shape.
- Fault injection for missing files, invalid config, unwritable outputs.
- Static type/lint.

Formal target:

- CLI option state model for mutually exclusive/required options where commands mutate state.

### 2. Config Loading and Routing

Files:

```text
python/deepresearch_flow/paper/config.py
python/deepresearch_flow/paper/routing.py
python/deepresearch_flow/paper/active_window.py
python/deepresearch_flow/ocr/config.py
python/deepresearch_flow/storage/config.py
python/deepresearch_flow/translator/config.py
frontend/src/lib/config.ts
frontend/src/stores/runtime-config.ts
```

Required coverage:

- Symbolic/property tests for parse/merge/validation functions.
- Fuzz malformed TOML/JSON/env values.
- Formal model for provider routing / active window selection.
- Fault injection for missing env vars and bad paths.

Formal properties:

```text
No inactive route is selected.
Zero/negative weight is rejected.
Unknown model/provider references are rejected.
Env secret values are not printed unmasked.
Config merge is deterministic.
```

### 3. LLM Provider, Embedding, Reranking, Scheduling

Files:

```text
python/deepresearch_flow/paper/llm.py
python/deepresearch_flow/paper/providers/*.py
python/deepresearch_flow/paper/embedding.py
python/deepresearch_flow/paper/reranker.py
python/deepresearch_flow/paper/embed_pipeline.py
python/deepresearch_flow/paper/embed_source.py
python/deepresearch_flow/translator/scheduler.py
python/deepresearch_flow/translator/engine.py
```

Required coverage:

- Formal model for retry/cooldown/quota routing.
- Property tests for deterministic route selection under seeded randomness.
- Fault injection for upstream 429/500/timeouts/malformed responses.
- Fuzz prompt/response parsing where structured output is expected.

Formal properties:

```text
A route in cooldown is not selected before recovery time.
Quota-exhausted route is not selected until reset.
Retry budget is finite.
Scheduler does not lose completed segments.
Partial translation failure is reported, not silently marked complete.
```

### 4. Paper DB, Search, Vector Store

Files:

```text
python/deepresearch_flow/paper/db.py
python/deepresearch_flow/paper/db_ops.py
python/deepresearch_flow/paper/search.py
python/deepresearch_flow/paper/vector_store.py
python/deepresearch_flow/paper/chunker.py
python/deepresearch_flow/paper/schema.py
python/deepresearch_flow/paper/schemas/*.py
```

Required coverage:

- Formal model for vector ingest idempotency and delete/update semantics.
- Property tests for chunk IDs, hashes, query filters, pagination.
- Fuzz text chunking and search query parsing.
- Fault injection for LanceDB/open failures, partial writes, schema mismatch.

Formal properties:

```text
Chunk IDs are deterministic for same document/template/chunk.
Re-ingesting unchanged chunks is idempotent.
Deleting a group removes only that group.
Query pagination never duplicates or skips within stable sorted input.
Schema migration is monotonic.
```

### 5. Snapshot API, Admin, Push, Static Export

Files:

```text
python/deepresearch_flow/paper/snapshot/api.py
python/deepresearch_flow/paper/snapshot/admin.py
python/deepresearch_flow/paper/snapshot/push.py
python/deepresearch_flow/paper/snapshot/push_semantic.py
python/deepresearch_flow/paper/snapshot/push_static.py
python/deepresearch_flow/paper/snapshot/builder.py
python/deepresearch_flow/paper/snapshot/migrate.py
python/deepresearch_flow/paper/snapshot/schema.py
```

Required coverage:

- Formal model for semantic ingest multi-part staging and commit.
- Property tests for API schemas and serializability.
- Fault injection for failed batch, restart after partial staging, duplicate push.
- Fuzz JSON payloads and export paths.

Formal properties:

```text
Multi-part ingest commits only after all required parts are present.
Failed final apply leaves staged data recoverable or explicitly discarded.
Admin mutation requires auth.
Static export never writes outside target root.
Duplicate push does not create duplicate logical paper entries.
```

### 6. MCP/OAuth/Auth

Files:

```text
python/deepresearch_flow/paper/snapshot/auth.py
python/deepresearch_flow/paper/snapshot/advanced/auth.py
python/deepresearch_flow/paper/snapshot/mcp_server.py
python/deepresearch_flow/paper/snapshot/mcp_content.py
```

Required coverage:

- TLA+/finite-state model for OAuth client registry/cache/resource/token state.
- Fuzz request parameters and bearer headers.
- Fault injection for cache corruption/write failure/restart/dependency import drift.
- Integration tests for bearer endpoints and OAuth endpoints.

Formal properties:

```text
No token without registered/recovered client.
Failed persist is not durable.
Corrupt cache does not grant.
Resource mismatch fails closed.
Static bearer token compare is exact and constant-time API is used.
```

### 7. Advanced Search Pipeline

Files:

```text
python/deepresearch_flow/paper/snapshot/advanced/*.py
frontend/src/components/AdvancedSearchPanel.vue
frontend/src/components/AdvancedSearchResults.vue
frontend/src/lib/advanced-search.ts
frontend/src/composables/useAdvancedSearchToken.ts
```

Required coverage:

- Formal pipeline model for retrieve/fuse/rerank/limit stages.
- Property tests for filters, normalization, dedup, MMR, fusion.
- Fuzz advanced-search request specs.
- Fault injection for timeout, empty result, malformed vector result.

Formal properties:

```text
Dedup never increases result count.
Limit is respected after fusion/rerank.
Filter predicates are monotonic when additional constraints are added.
MMR output contains no duplicate paper IDs.
Timeout returns structured recoverable error.
```

### 8. Web Backend Rendering and Static Assets

Files:

```text
python/deepresearch_flow/paper/web/*.py
python/deepresearch_flow/paper/web/handlers/*.py
python/deepresearch_flow/paper/render.py
```

Required coverage:

- Fuzz markdown/html/math/table/image normalization.
- Property tests for URL/path rewriting and query parsing.
- Fault injection for missing static assets and PDF.js assets.
- Build/static export checks.

Formal properties:

```text
Static URL resolver never emits path traversal.
Markdown normalization preserves protected code/math regions by contract.
Missing asset reports fallback, not crash.
```

### 9. Frontend App, Stores, API Client, Persistence

Files:

```text
frontend/src/lib/*.ts
frontend/src/composables/*.ts
frontend/src/stores/*.ts
frontend/src/router/index.ts
frontend/src/types/*.ts
frontend/src/views/*.vue
```

Required coverage:

- fast-check properties for pure lib utilities.
- Component black-box tests for visible behavior.
- Fault injection/mocks at public network boundary for HTTP failures.
- IndexedDB/localStorage recovery tests.
- TypeScript build.

Formal/property targets:

```text
Selection store set operations are idempotent.
Token DB never returns expired token as valid.
Paper content cache returns only matching paper/template/version.
Static base URL normalization is deterministic.
Query client encodes params without dropping filters.
```

### 10. Frontend Renderers: Markdown, Mermaid, KaTeX, PDF

Files:

```text
frontend/src/components/MarkdownContent.vue
frontend/src/components/MarkdownPanel.vue
frontend/src/components/RenderedMarkdown.vue
frontend/src/components/PdfViewer.vue
frontend/src/lib/markdown*.ts
frontend/src/lib/module-interop.ts
```

Required coverage:

- Fuzz malformed markdown/LaTeX/Mermaid/html.
- Fault injection for dynamic import/chunk load failure.
- Component tests proving visible fallback instead of app crash.
- Regression corpus from real console errors.

Formal/property targets:

```text
Renderer function never throws to caller for arbitrary string input.
Unsupported LaTeX emits warning/fallback, not fatal UI failure.
Mermaid load/render failure is contained.
PDF viewer missing asset is contained.
```

### 11. Selected Export, JSONL, ZIP

Files:

```text
frontend/src/lib/selected-export.ts
frontend/src/views/SelectedView.vue
python/deepresearch_flow/paper/web/static_assets.py
```

Required coverage:

- fast-check for path/name sanitization.
- Property tests for JSONL parseability.
- Fault injection for missing assets and partial network failures.
- ZIP content assertions by public archive entries.

Formal/property targets:

```text
Generated archive paths never escape archive root.
Every JSONL line parses independently.
Partial failures are reported in manifest/errors.
Requested content set determines output entries.
```

### 12. Translator / Recognize / OCR / Storage

Files:

```text
python/deepresearch_flow/translator/*.py
python/deepresearch_flow/recognize/*.py
python/deepresearch_flow/ocr/**/*.py
python/deepresearch_flow/storage/*.py
```

Required coverage:

- Fuzz markdown protector/fixer/segmenter.
- Formal model for translation segment scheduling and retry state.
- Fault injection for OCR backend failure and WebDAV upload failure.
- Property tests for path mapping, progress accounting, placeholder preservation.

Formal properties:

```text
Placeholder protection round-trips protected spans.
Segment scheduler never marks missing segment complete.
Progress percentage is monotonic within a run.
Storage upload either succeeds with expected remote path or reports failure.
OCR missing backend fails with structured error.
```

### 13. Documentation, Examples, Deployment Files

Files:

```text
README.md
README_ZH.md
docs/**/*.md
config.example.toml
ocr.example.toml
remote.example.toml
.github/workflows/**/*.yml
.dockerignore
scripts/docker/**
docker-compose*.yml
```

Required coverage:

- Secret/host leakage scan.
- Link/reference scan for local docs.
- Version consistency scan.
- Command snippet smoke checks where safe.

Properties:

```text
No real private host/token appears in committed examples.
Documented version equals package versions where applicable.
Documented config keys exist in code or schema.
```

## Global Machine Gates

### Inventory gate

```bash
uv run python tools/verification/generate_inventory.py --check
```

Fails if any tracked source/config/doc file is missing from `docs/verification/repo-verification-manifest.yml`.

### Formal gate

```bash
uv run python tools/formal/check_all_models.py
```

Runs all finite-state/symbolic checks available without external services.

### Fuzz fast gate

```bash
uv run pytest -m fuzz_fast
cd frontend && npm test -- --run --project fuzz
```

Uses deterministic seeds and bounded examples.

### Fault gate

```bash
uv run pytest -m fault
```

Runs deterministic fault injection.

### Strict gate

```bash
rtk make check
uv run python -m compileall -q python tests
uv run pytest
cd frontend && npm test -- --run
cd frontend && npm run build
cd frontend && npm audit --audit-level=high
uv run python tools/verification/generate_inventory.py --check
uv run python tools/formal/check_all_models.py
```

Release is blocked if strict gate fails.

## Inventory Manifest Schema

`docs/verification/repo-verification-manifest.yml`:

```yaml
version: 1
items:
  - path: python/deepresearch_flow/paper/snapshot/auth.py
    kind: python_module
    symbols:
      - name: JsonOAuthClientCache
        coverage:
          - FORMAL_MODEL: formal/oauth_client_cache/OAuthClientCache.tla
          - FUZZ_TEST: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_fuzz.py
          - FAULT_INJECTION: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache_faults.py
          - UNIT_BLACK_BOX: python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py
        criticality: P0
        gaps: []
```

Generated inventory compares actual files/symbols with this manifest. New files/symbols fail the check until mapped.

## Current Known Blocker

Earlier work recorded a historical full-suite blocker. The current evidence snapshot is maintained in `docs/verification/machine-verifiable-model.md`; any future full-suite failure becomes release-blocking again. Repo-wide formal/fuzz work must not hide it.

## Acceptance Criteria

1. Every tracked source/config/doc file appears in the verification inventory.
2. Every Python production function and frontend exported symbol/component appears in the generated inventory or is explicitly ignored with reason.
3. P0/P1 modeled subsystems have executable formal models plus implementation conformance evidence; temporary gap records fail the strict local gate.
4. Fuzz/fault tests are deterministic in fast mode.
5. At least one negative-control formal check exists per formal model.
6. Strict gate blocks release on missing inventory, failing model, failing fuzz/fault, build failure, audit failure, or full-test failure.
