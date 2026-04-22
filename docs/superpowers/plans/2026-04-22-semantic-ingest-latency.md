# Semantic Ingest Latency Implementation Plan

**Goal**

Reduce semantic admin ingest latency on the remote snapshot API by replacing expensive pre-write LanceDB reads with at most two admin-specific narrow probes, ensuring scalar indexes for the main filter columns, and skipping no-op deletes when the target group is empty.

**Spec**

`docs/superpowers/specs/2026-04-22-semantic-ingest-latency-design.md`

**Current Symptoms**

- `read_existing_state` can take 20s to 30s even when `existing_rows=0`
- `delete_groups` can take seconds even when `deleted_rows=0`
- the global ingest lock causes later requests to queue behind one slow request

**Dependencies**

- Existing semantic ingest endpoint in `python/deepresearch_flow/paper/snapshot/admin.py`
- LanceDB-backed helpers in `python/deepresearch_flow/paper/vector_store.py`
- Existing semantic admin tests in `python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py`

**Out of Scope**

- Lock sharding or removing the global semantic ingest lock
- Any client-side wire protocol change
- Rebuilding or migrating existing remote vector stores

---

## Step 1: Add focused failing tests for the intended fast path

**Action**

Add tests that express the target behavior before implementation:

- vector-store helper tests for:
  - `read_admin_ingest_state(...)` contract on:
    - cold document
    - empty target group with other templates on the same document
    - existing target group
  - ensuring/checking scalar indexes for `doc_id` and `template_tag`
- admin semantic tests for:
  - empty-existing-group path skipping the expensive delete
  - metadata count correctness after moving from group-hash semantics to template-membership semantics

**Validation Standard**

- New tests fail against the current implementation for the right reason
- Existing semantic ingest tests still pass before code changes

**Estimated Effort**

Medium

---

## Step 2: Add scalar index support and an admin-ingest helper with at most two narrow probes in `vector_store.py`

**Action**

Implement the admin-ingest primitives in one place:

- an idempotent scalar-index ensure/check path for:
  - `doc_id`
  - `template_tag`
- one admin helper such as `read_admin_ingest_state(...)` that uses:
  - one `doc_id = ?` probe for document membership and exact-group state
  - one `template_tag = ? AND doc_id != ? LIMIT 1` probe for `had_template_elsewhere`

Wire the index ensure path into the CLI serve path for the resolved LanceDB directory so older remote databases are upgraded in place before the server starts handling requests.

Implementation constraints for this step:

- treat "once" concretely as once per process per resolved `vector_dir`
- treat the process cache as advisory only and re-check actual index presence before skipping
- run ensure before `uvicorn.run(...)`, not in ASGI lifespan/startup hooks
- if `_CHUNKS_TABLE` does not exist, startup ensure is a no-op
- when `write_chunks(...)` creates `_CHUNKS_TABLE` for the first time, ensure the scalar indexes immediately after table creation
- prefer `list_indices()` (or equivalent existence checks) before index creation instead of relying on broad exception swallowing for idempotency
- default startup wait timeout to 30 minutes and allow `SEMANTIC_INDEX_BUILD_TIMEOUT` to override it in seconds
- on timeout, surface a CLI error with guidance to raise `SEMANTIC_INDEX_BUILD_TIMEOUT`

Prefer helper names that make the intended admin-only use obvious.

**Validation Standard**

- Helper/index tests pass
- startup on an older DB without the scalar indexes builds them automatically
- startup waits for index creation before serving requests
- startup on an empty vector directory does not fail just because the table does not exist yet
- The helper does not reconstruct group hashes
- The helper does not require three separate filtered scans for document/group/template state
- The helper does not use one broad `doc_id = ? OR template_tag = ?` probe across a hot template
- logs and notes make it clear that Step 2 speedups from scalar indexes do not remove the need for Step 3 query-shape cleanup

Interpretation note:

- after Step 2 lands, production logs may already become noticeably faster because the old filtered reads can benefit from scalar indexes
- this does **not** make Step 3 optional; Step 3 removes redundant overlapping scans and group-hash reconstruction on top of the scalar-index gain

**Estimated Effort**

Medium

---

## Step 3: Replace expensive admin read path in `admin.py`

**Action**

Refactor `_apply_semantic_chunk_batch(...)` and `_update_index_meta_after_group_write(...)` so they use:

- previous template membership semantics, not `template_tag -> group_hash`
- exact-group `{id: content_hash}` state from the merged helper
- an explicit cold-document shortest path

Keep the change scoped to the admin path. Tests should assert the membership behavior, not require a broad public signature refactor.

**Validation Standard**

- Existing semantic ingest behavior remains identical from the API caller's perspective
- `doc_count`, `template_count`, and `chunk_count` transition tests pass

**Estimated Effort**

Medium

---

## Step 4: Skip no-op deletes for empty groups

**Action**

When the target group has no existing rows:

- do not call `delete_groups(...)`
- emit a `delete_groups` phase log that clearly indicates the delete was skipped

**Validation Standard**

- Empty-group semantic ingest no longer executes the expensive delete path
- Logs remain easy to compare before/after
- Result counters remain unchanged

**Estimated Effort**

Small

---

## Step 5: Preserve and tighten observability

**Action**

Keep the existing semantic phase logs, but update details so they reflect the cheaper path:

- `read_existing_state`: log `existing_rows`, `doc_templates`, `doc_had_any_rows`, `had_template_elsewhere`
- `delete_groups`: log whether delete was skipped
- `write_chunks`: keep timing visible and treat it as a required comparison metric

Avoid changing the top-level log names so production comparisons stay simple.

**Validation Standard**

- Existing operational logs remain recognizable
- New log details are sufficient to confirm the optimization path was taken

**Estimated Effort**

Small

---

## Step 6: Run targeted verification and compare phase timings

**Action**

Run the relevant test suites and then validate with a representative semantic push against the remote deployment or a staging-equivalent environment.

Suggested verification commands:

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py -q
uv run pytest python/deepresearch_flow/paper/tests/test_vector_store*.py -q
```

If the repo layout changes the final test file names, adapt the command to the actual touched files.

**Validation Standard**

- All targeted tests pass
- Phase logs show a clear drop in:
  - `read_existing_state`
  - `delete_groups` for empty groups
- `write_chunks` remains separately observable so residual latency is attributable

**Estimated Effort**

Medium

---

## Checkpoints

### Checkpoint 1: After Step 1

Confirm the failing tests accurately describe the desired optimization and do not accidentally encode implementation details that are too specific.

### Checkpoint 2: After Step 4

Review the updated logs and ensure the no-op delete path is visible and understandable in production output.

### Checkpoint 3: After Step 6

Compare before/after timings from real semantic ingest logs and decide whether a second iteration on lock granularity is still needed.

---

## Risks and Mitigations

**Risk: LanceDB helper changes still use a slow internal path**

Mitigation:

- benchmark the helper implementation choices
- if needed, switch to a lower-level Arrow projection path in the same iteration

**Risk: Scalar index creation adds one-time startup or first-write cost**

Mitigation:

- make index ensure idempotent
- log when index creation/check occurs
- evaluate steady-state timings separately from first-run index build

**Risk: Count bookkeeping subtly regresses**

Mitigation:

- add transition-focused tests around doc/template presence before and after writes

**Risk: Query optimization helps single-request latency but queueing remains high**

Mitigation:

- explicitly defer lock redesign
- only open the next optimization round after fresh measurements

---

## Expected Outcome

After this plan:

- semantic ingest for empty/new groups should no longer spend tens of seconds proving emptiness
- no-op deletes should be effectively free
- logs should make the remaining cost centers obvious
- we will have enough evidence to judge whether lock sharding is still worth doing
