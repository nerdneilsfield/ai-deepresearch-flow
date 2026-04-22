# Semantic Ingest Latency Design Spec

**Date:** 2026-04-22  
**Status:** Draft  
**Scope:** Reduce end-to-end latency of `POST /api/v1/admin/semantic/chunks/batch` on the remote snapshot admin API, especially when the target `(doc_id, template_tag)` group does not already exist.

## Overview

The current semantic ingest path is functionally correct but materially slower than expected for modest batch sizes. Production logs show examples such as:

- `phase=read_existing_state elapsed_ms=26319.7 existing_rows=0 doc_groups=0 had_template_elsewhere=0`
- `phase=delete_groups elapsed_ms=8363.9 deleted_rows=0`
- `semantic batch done ... elapsed_ms=34995.2 inserted=42`

This is slow enough to:

- make `paper db api push --embed-db` appear hung even with small chunk counts
- serialize all later semantic requests behind one slow request because ingest currently uses a single async lock
- increase the chance of client-side disconnects or reverse-proxy timeouts

The design goal is to reduce the steady-state cost of semantic ingest without changing the external API contract or the correctness of semantic upsert semantics.

## Problem Statement

The current ingest implementation in `paper/snapshot/admin.py` does three expensive LanceDB reads before any write:

1. `read_group_hashes_for_doc(db, doc_id)`
2. `read_chunks_for_group(db, doc_id, template_tag)`
3. `has_rows_for_template(db, template_tag, exclude_doc_id=doc_id)`

It then always executes:

4. `delete_groups(db, [(doc_id, template_tag)])`

Even when:

- the target group is known to be empty
- the document has no other semantic groups
- no rows need to be deleted

In addition, the current `vector_store` read helpers are built on `table.search().where(...).to_list()`. On the currently used `lancedb 0.30.x` stack, that path is substantially more expensive than needed for pure scalar existence / projection checks, and can still materialize vector-related data even when the caller only needs narrow metadata such as `id`, `content_hash`, or `template_tag`.

## Goals

- Reduce `read_existing_state` latency by avoiding expensive full-group hash reconstruction and unnecessary wide reads.
- Reduce `delete_groups` latency by skipping deletes when the target group is known to be empty.
- Keep current admin API request and response schemas unchanged.
- Preserve correctness for:
  - inserted / updated / skipped / deleted counters
  - `doc_count`, `template_count`, and `chunk_count` bookkeeping
  - multi-part staging semantics
- Keep the existing single-lock ingest behavior in this iteration unless evidence later shows lock sharding is required.

## Non-Goals

- Changing the client-side semantic push wire protocol.
- Changing LanceDB storage format or rebuilding existing indexes.
- Parallelizing semantic ingest requests in this iteration.
- Redesigning semantic stats semantics.
- Introducing a separate SQLite metadata mirror for vector rows.

## Bottleneck Analysis

### Narrow projection alone is unlikely to be sufficient

The current logs already show `read_existing_state` taking tens of seconds even when:

- `existing_rows=0`
- `doc_groups=0`
- `had_template_elsewhere=0`

This strongly suggests the dominant cost is not just row width. The more likely root cause is that the current LanceDB access pattern:

- `table.search().where(...).to_list()`

is itself expensive for scalar filtering on `doc_id` / `template_tag`, especially when `_CHUNKS_TABLE` does not have scalar indexes for those fields.

Therefore, this design does **not** assume that replacing full-row reads with narrower projections is enough by itself. Query-shape changes and scalar indexing are part of the same fix.

### Current read path does more work than needed

`_update_index_meta_after_group_write(...)` only needs:

- whether the document had any groups before the write
- whether the document had other template groups besides the current one
- whether the target template existed elsewhere before the write

It does **not** need a full `template_tag -> group_hash` map. However, `read_group_hashes_for_doc(...)` reconstructs those hashes by reading all `content_hash` values for the document and hashing them again.

### Exact-group state read is wider than necessary

To compute inserted / updated / skipped / deleted counts, the admin path only needs:

- `id`
- `content_hash`

for the exact `(doc_id, template_tag)` group. The current helper returns the full row payload.

### Delete is unconditional

The current flow always calls `delete_groups(...)`, even when `existing_rows` is empty. This imposes a costly filtered delete/commit path on LanceDB for a known no-op case.

### Three overlapping reads multiply the same table-scan cost

The current admin path performs three overlapping filtered reads against the same table:

1. document-level state
2. exact-group state
3. template-level existence elsewhere

Even if each query becomes narrower, this is still effectively `3 x N` work on a cold table. The design should prefer at most two admin-ingest-specific narrow probes:

1. one `doc_id = ?` read for document membership and exact-group `{id, content_hash}` state
2. one `template_tag = ? AND doc_id != ? LIMIT 1` existence probe for `had_template_elsewhere`

### Global lock amplifies the impact

The current `semantic_ingest_lock` is request-global. This means a single slow `read_existing_state` or `delete_groups` call blocks all subsequent semantic ingest requests, even for unrelated documents or templates.

This spec does not remove the lock, but it treats lock contention as a secondary effect of the primary problem: overly expensive per-request store operations.

## Approaches Considered

### Option A: Minimal query-path optimization inside the current architecture

Make the admin path cheaper by:

- ensuring scalar indexes exist for `doc_id` and `template_tag` on `_CHUNKS_TABLE`
- replacing the three overlapping reads with at most two admin-ingest-specific narrow probes
- replacing full-row exact-group reads with a narrow `{id, content_hash}` projection inside the document probe
- skipping `delete_groups(...)` when `existing_rows` is empty

**Pros**

- Smallest code change
- Preserves current API and lock model
- Targets the exact slow phases shown in logs

**Cons**

- Does not remove global serialization
- Still depends on LanceDB query behavior for scalar reads

### Option B: Introduce a lightweight per-group metadata index

Maintain a side table or sidecar file that tracks:

- document -> template key membership
- template existence across docs
- group row counts

This would avoid most LanceDB reads during ingest.

**Pros**

- Potentially largest ingest speedup
- Less repeated store probing

**Cons**

- Adds a second source of truth
- Higher correctness and migration risk
- Much larger implementation surface

### Option C: Lock sharding / optimistic writes first

Keep current reads but reduce contention by replacing the global lock with per-group locking.

**Pros**

- Helps throughput under concurrency

**Cons**

- Does not fix the 20s to 30s single-request latency shown in logs
- Makes correctness reasoning harder before the slow query path is understood

## Recommendation

Choose **Option A** for this iteration.

It directly addresses the observed latency sources with the smallest change set and the lowest correctness risk. Once we have fresh phase timings after this optimization, we can re-evaluate whether lock sharding is still necessary.

## Proposed Design

### 1. Ensure scalar indexes for admin-ingest filters

The admin ingest path should explicitly check and, if necessary, build scalar indexes for the fields that dominate its filtered reads:

- `doc_id`
- `template_tag`

This is part of the latency fix, not a follow-up optimization. If these indexes are absent, helper-level refactors alone may still leave cold-document ingest in the multi-second range.

Preferred behavior:

- when `paper db api serve` starts with `--embed-db` (or `config.search.vector_dir`), treat index ensure as part of startup
- if an older LanceDB directory is missing either scalar index, build it during startup before serving requests
- for this iteration, define "once" concretely as **once per process per resolved `vector_dir`**
- treat that process cache as advisory only: before skipping, re-check that both scalar indexes still exist on the current table handle
- avoid rebuilding them on every request
- wait for index creation to complete before the API starts accepting semantic ingest traffic
- log whether index creation/check ran so first-deploy timings can be interpreted correctly

Startup placement is explicit:

- run index ensure in the CLI serve path before `uvicorn.run(...)`
- do **not** defer it to ASGI lifespan/startup hooks

This keeps health checks honest: the process only starts serving after index ensure is complete.

Timeout behavior is part of the operator experience:

- default startup wait timeout is 30 minutes
- `SEMANTIC_INDEX_BUILD_TIMEOUT` may override that timeout in seconds
- timeout failures should surface as a CLI error with guidance to increase `SEMANTIC_INDEX_BUILD_TIMEOUT`, not as a raw traceback

This is the compatibility plan for existing remote databases: no manual migration step, no rebuild of the vector store, and no client-side change. Older databases are upgraded in place during API startup.

Cold-table compatibility is also explicit:

- if `_CHUNKS_TABLE` does not exist yet, startup index ensure is a no-op
- when `write_chunks(...)` creates the table for the first time, it must immediately ensure the scalar indexes before returning

This keeps first deploys compatible with empty vector directories while still converging old and new databases to the indexed shape.

Operational note:

- the very first semantic ingest that creates `_CHUNKS_TABLE` will also pay the one-time cost of scalar index creation
- that first request may therefore show an unusually high `elapsed_ms` without indicating a regression in the steady-state path

### 2. Add one admin-ingest state reader with at most two narrow projections

Instead of three separate helpers, add one admin-path helper in `paper/vector_store.py`, for example:

- `read_admin_ingest_state(db, doc_id, template_tag) -> AdminIngestState`

where `AdminIngestState` includes exactly what the admin write path needs:

- `existing_by_id: dict[str, str]`
- `previous_template_keys: set[str]`
- `had_template_elsewhere: bool`
- `doc_had_any_rows: bool`

This helper should be implemented as at most two narrow scalar probes, not as three separate `search().where(...).to_list()` calls and not as one broad `doc_id = ? OR template_tag = ?` read:

- document probe: `doc_id = ?` with a narrow projection sufficient to compute:
  - `previous_template_keys`
  - `doc_had_any_rows`
  - `existing_by_id`
- template-existence probe: `template_tag = ? AND doc_id != ? LIMIT 1` with a minimal projection (for example `id`) to compute:
  - `had_template_elsewhere`

With scalar indexes on both filtered columns, this keeps the helper on index-friendly point-lookups instead of asking LanceDB to plan a broader `OR` filter across a hot template.

### 3. Give cold documents a dedicated shortest path

The helper should explicitly detect the "first ingest for this document" case:

- no rows for `doc_id`

This must be treated separately from:

- "the exact target group is empty, but the document already has other templates"

Why this matters:

- a cold document lets us short-circuit both document-level and group-level historical reasoning
- that case is likely common during bulk initial semantic sync

The shortest path for a cold document should directly produce:

- `existing_by_id = {}`
- `previous_template_keys = set()`
- `doc_had_any_rows = False`

However, cold-document short-circuiting does **not** eliminate the need to determine:

- `had_template_elsewhere`

That value still needs one separate, narrow existence probe across other documents for the same `template_tag`. The cold-doc fast path only removes repeated document-scoped history reads.

### 4. Keep meta bookkeeping based on template membership, not group hashes

`_update_index_meta_after_group_write(...)` only needs template membership semantics, not reconstructed group hashes.

To minimize signature churn and test fallout, the spec does **not** require any broad public contract change. Instead:

- treat this as an internal admin-path refactor
- write tests around before/after template membership behavior
- let the exact helper signature evolve in the smallest way that supports the new state object

The key requirement is behavioral:

- doc/template counts must be computed from membership transitions, not from content-hash map reconstruction

### 5. Narrow exact-group state reads inside the merged helper

Within `read_admin_ingest_state(...)`, compute exact-group state using only:

- `id`
- `content_hash`

for the target `(doc_id, template_tag)` group.

This replaces the current full-row group read:

- old:
  - `existing_rows = read_chunks_for_group(...)`
  - `existing_by_id = {row["id"]: row["content_hash"] for row in existing_rows}`

- new:
  - `existing_by_id = state.existing_by_id`

The `previous_group_count` value for meta bookkeeping becomes `len(existing_by_id)`.

### 6. Skip no-op deletes for empty groups

If `existing_by_id` is empty, do not call `delete_groups(...)`.

Behavior:

- log a `delete_groups` phase with `elapsed_ms=0.0` and `deleted_rows=0`
- include an explicit marker such as `skipped_delete=1`
- proceed directly to `write_chunks(...)`

This preserves log shape while avoiding an expensive no-op delete.

### 7. Keep the lock unchanged in this iteration

The request-global `semantic_ingest_lock` remains in place.

Rationale:

- it is not the root cause of the slow single-request timings
- changing concurrency semantics at the same time as query semantics would make regression analysis harder

### 8. Improve observability for the optimized phases

Keep the existing phase logs, but make them reflect the new internal work and preserve the data needed for the next optimization round:

- `read_existing_state`
  - log `existing_rows`, `doc_templates`, `doc_had_any_rows`, and `had_template_elsewhere`

- `delete_groups`
  - log `skipped_delete=1` when `existing_by_id` is empty

- `write_chunks`
  - keep the existing `write_chunks` phase timing and treat it as an explicit comparison metric in validation

This will let us compare before/after timings directly in production logs and determine whether any remaining cost has shifted into writes.

## File-Level Impact

### `python/deepresearch_flow/paper/vector_store.py`

- Add scalar-index ensure/check support for admin-ingest filters
- Add one merged admin-ingest state helper
- Keep existing public helpers untouched unless the optimized path can safely replace them

### `python/deepresearch_flow/paper/snapshot/admin.py`

- Update `_update_index_meta_after_group_write(...)` to use template membership semantics instead of group hashes
- Switch `_apply_semantic_chunk_batch(...)` to the new merged state helper
- Add explicit cold-document short-circuit handling
- Skip no-op deletes
- Update logging details accordingly

### Tests

- Add vector-store helper/index tests
- Add admin semantic tests covering:
  - scalar index ensure/check behavior for admin-ingest fields
  - cold-document shortest path
  - empty-existing-group fast path
  - template-count/doc-count correctness after helper changes
  - no-op delete path still logs and returns correct stats

## Correctness Constraints

The optimized path must preserve:

- identical response payloads for semantic ingest
- identical insert/update/skip/delete counters for the same input
- correct `doc_count` updates when:
  - a document gets its first semantic group
  - a document loses its last semantic group
- correct `template_count` updates when:
  - a non-shared template first appears anywhere
  - a non-shared template disappears everywhere

## Validation Plan

We will treat the change as successful when:

- targeted unit/integration tests pass
- production-style phase logs show materially reduced timings for:
  - `read_existing_state`
  - `delete_groups` on empty groups
- `write_chunks` timing remains visible so residual cost can be compared
- semantic push can complete without previously observed long pauses for small batches on the same deployment

## Risks

### LanceDB query API may still materialize more data than expected

Mitigation:

- benchmark helper choices locally
- if the query-builder path remains too expensive, fall back to a lower-level narrow Arrow scan

### Scalar index creation may have an upfront one-time cost

Mitigation:

- make index ensure idempotent
- log whether index creation ran
- evaluate steady-state ingest timings separately from first-run index build cost

### Meta bookkeeping regression

Mitigation:

- explicitly test `doc_count` / `template_count` transitions
- keep the old semantics visible in the spec and plan so behavior can be compared line-by-line

### Lock contention still dominates after query optimization

Mitigation:

- defer lock redesign until we have post-optimization timings
- if needed, open a follow-up design for group-level locking based on measured evidence

## Rollout

This is a server-side internal optimization with no API schema change. Rollout is:

1. ship behind the existing endpoint
2. observe semantic phase logs on the remote deployment
3. compare before/after timings for the same workload
4. only then decide whether a second iteration is needed for lock sharding
