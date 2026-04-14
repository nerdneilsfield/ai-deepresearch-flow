# API Push Vector Sync Design Spec

**Date:** 2026-04-12
**Status:** Draft
**Scope:** Extend `paper db api push` so it can optionally push already-computed local vector chunks to the remote admin API. The remote side stores and deduplicates semantic chunks; the client does not upload the LanceDB directory itself.

## Overview

The project already has a local embedding pipeline:

1. `paper embed` reads extracted paper content.
2. It chunks documents and computes embeddings locally.
3. It stores chunk records and vectors in LanceDB.

Today `paper db api push` only pushes:

- paper metadata to the remote admin API
- static assets to remote storage

This spec adds a third optional channel:

- **semantic chunk sync** — push the already-computed chunk rows from the local vector index to the remote service

The core rule is simple:

- **Do not upload the LanceDB database directory itself.**
- **Do not recompute document embeddings during push.**
- **Do push the already-computed chunk records, including `vector`.**

This keeps local embedding cost amortized and lets the remote service answer semantic search queries by embedding only the query at request time.

## Goals

- Add optional semantic row sync to `paper db api push` without changing any existing local embedding, search, or build behavior.
- Reuse local `paper embed` output during remote sync.
- Make vector sync optional; existing `api push` users should not be affected.
- Client and server share the same LanceDB-based semantic storage architecture, but the wire protocol does not transmit LanceDB files. LanceDB is the shared storage engine, not a wire-level artifact.
- Let the server own deduplication, upsert, and deletion semantics.
- Align remote semantic data with local incremental update behavior.

## Non-Goals

- Uploading the raw LanceDB directory or files.
- Recomputing document embeddings during `api push`.
- Changing local `paper embed` pipeline behavior.
- Changing local `paper search` or `/api/papers/semantic` search/ranking behavior.
- Changing snapshot build outputs or `paper db snapshot build` flow.
- Making semantic sync mandatory for `api push` (only activates with `--embed-db`).
- Defining the remote query-time embedding provider selection logic.
- Full remote semantic search implementation details beyond the ingest contract.

## CLI Changes

### New optional flag

`paper db api push` gains a new optional flag:

```bash
--embed-db /path/to/paper_vectors
```

### Behavior

- If `--embed-db` is omitted:
  - push paper metadata as today
  - optionally push static assets as today
  - do not touch semantic chunk sync
- If `--embed-db` is provided:
  - validate that the directory exists
  - load local `index_meta.json`
  - read semantic chunk rows from LanceDB
  - push them to the remote semantic ingest API in batches

### Command examples

Metadata only:

```bash
uv run deepresearch-flow paper db api push   --snapshot-db ./paper_snapshot.db   --config remote.toml
```

Metadata + static assets + semantic chunks:

```bash
uv run deepresearch-flow paper db api push   --snapshot-db ./paper_snapshot.db   --static-export-dir ./static_export   --embed-db ./paper_vectors   --config remote.toml
```

Static-only remains unchanged via existing flags.

## Data Model

### Local source of truth

When `--embed-db` is provided, the client reads local semantic rows from LanceDB.

Each pushed record is derived from the existing chunk row schema:

- `id`
- `doc_id`
- `source_path`
- `template_tag`
- `chunk_type`
- `chunk_index`
- `field_name`
- `lang`
- `text`
- `content_hash`
- `vector_b64` (base64-encoded packed float32 bytes)
- `vector_dim` (integer, must match `index_meta.dimensions`)
- `title`
- `year`
- `authors`
- `venue`
- `tags`

### Index metadata

The client also pushes index metadata so the server can validate compatibility:

- `model`
- `dimensions`
- `normalized`
- `provider`
- `index_version`

This metadata is read from local `index_meta.json` and sent alongside semantic chunk batches.

### Vector serialization format

Vectors are transmitted as packed float32 bytes encoded in base64. Each chunk carries two fields instead of a raw float array:

- `vector_b64`: base64-encoded little-endian float32 bytes (e.g. 1024 floats = 4096 bytes → ~5.5 KB base64)
- `vector_dim`: integer, number of dimensions (must match `index_meta.dimensions`)

Client encoding: `base64.b64encode(struct.pack(f'<{n}f', *vector)).decode('ascii')`
Server decoding: `struct.unpack(f'<{dim}f', base64.b64decode(vector_b64))`

This reduces vector payload by ~75% compared to JSON float arrays (4 bytes/float binary vs ~8-12 bytes/float JSON text). The rest of the request body remains standard JSON.

### Remote vector storage

The remote service stores semantic chunks in LanceDB, the same engine used locally. The server decodes `vector_b64` back to a float list and writes via `vector_store.write_chunks(db, rows, dimensions=...)`. This means `vector_store.py` is shared code between local embedding and remote ingest.

## Remote API Contract

### New admin endpoint

The remote admin API adds a semantic ingest endpoint, for example:

```text
POST /api/admin/semantic/chunks/batch
```

The exact URL may vary with the existing admin API layout, but it must be an authenticated admin-only endpoint parallel to the current paper push API.

### Request body

Each request sends semantic chunk records for exactly one logical group plus index metadata.

A **group** is all chunk rows for one `(doc_id, template_tag)` pair. A large group may be split across multiple requests when needed for network safety, but every request carrying a fragment of that group must include group-part metadata so the server can stage the fragments and reconcile only after the full group has arrived.

`group_hash` is computed using the same shared algorithm as the local incremental update: `SHA-256 of sorted content_hash values in the group` (see `vector_store.compute_group_hash()`). Client and server must use this identical definition; the server uses it as the staging key to match parts of the same group version.

In v1, one request belongs to exactly one `(doc_id, template_tag, group_hash)` group upload. The request may contain the full group or one part of that group:


```json
{
  "index_meta": {
    "model": "Qwen3-Embedding-4B",
    "dimensions": 1024,
    "normalized": true,
    "provider": "ollama",
    "index_version": 1
  },
  "group": {
    "doc_id": "paper-1",
    "template_tag": "",
    "group_hash": "sha256:abcd...",
    "part_index": 0,
    "part_count": 3,
    "is_final_part": false
  },
  "chunks": [
    {
      "id": "paper-1__shared_title_0",
      "doc_id": "paper-1",
      "source_path": "papers/a.md",
      "template_tag": "",
      "chunk_type": "title",
      "chunk_index": 0,
      "field_name": "title",
      "lang": "",
      "text": "Attention Is All You Need",
      "content_hash": "abc123",
      "vector_dim": 1024,
      "vector_b64": "AACAPwAAgD8AAIA/AACAPw==...",
      "title": "Attention Is All You Need",
      "year": 2017,
      "authors": "Vaswani et al.",
      "venue": "NeurIPS",
      "tags": "transformer,attention"
    }
  ]
}
```

### Authentication

This endpoint uses the same admin token mechanism as existing `paper db api push` metadata sync.

### Response body

The server returns a batch summary:

```json
{
  "received": 100,
  "inserted": 70,
  "updated": 20,
  "skipped": 10,
  "deleted": 0
}
```

`deleted` is reported only when the server has received a complete logical group and performed reconciliation for that group. Fragment uploads that are still waiting for additional parts may report `deleted = 0` until reconciliation completes.

## Server-side Storage Semantics

The server stores semantic chunks in LanceDB (same engine as local). The admin handler decodes `vector_b64` to float lists and writes via `vector_store.write_chunks()` / `vector_store.delete_groups()`, sharing the same code path as local embedding. The server is responsible for deduplication, update, and cleanup. The client should not try to pre-resolve remote state.

### Unique identity

The semantic chunk uniqueness key is:

- `(doc_id, template_tag, chunk_type, chunk_index)`

This matches the logical identity of a chunk group already used locally.

### Upsert rules

For each incoming chunk record:

1. If no existing record has the same unique key:
   - insert it
2. If an existing record has the same unique key and the same `content_hash`:
   - skip it
3. If an existing record has the same unique key but a different `content_hash`:
   - update text, vector, and metadata fields in place

### Cleanup rules

Cleanup is performed **per logical group after the server has received the full current chunk set for that group**.

Cleanup granularity is:

- `(doc_id, template_tag)`

That means:

- the client may send a group in one request or multiple parts
- the server stages incoming parts in a lightweight SQLite staging table keyed by `(doc_id, template_tag, group_hash, part_index)`
- the server must not reconcile or delete old chunks for that group until all `part_count` parts are present
- once the group is complete, the server decodes all `vector_b64` fields, reconstructs `ChunkRow` objects, and writes to LanceDB via `vector_store.delete_groups()` + `vector_store.write_chunks()`
- any older remote chunk in that group whose unique key is absent from the reconstructed group payload must be deleted

This mirrors the local incremental embedding behavior while avoiding accidental deletion of untouched documents during partial or incremental pushes.

### Compatibility validation

The server must reject incompatible vector payloads before ingesting rows.

At minimum, it validates:

- `index_meta.dimensions` matches the server-side semantic store configuration
- `index_meta.normalized` matches expected scoring assumptions
- `index_meta.index_version` is supported

The server may also validate `model` and `provider` if remote policy requires a canonical embedding model.

## Push Flow

### Client-side sequence

When `--embed-db` is provided, `paper db api push` runs these steps:

1. Validate local LanceDB directory exists.
2. Load local `index_meta.json`.
3. Read all semantic chunk rows from LanceDB.
4. Group rows by `(doc_id, template_tag)`.
5. For each group, estimate payload size and either keep it whole or split it into numbered parts.
6. Build one semantic ingest request per whole group or group part.
7. Push each request to the remote semantic ingest endpoint.
8. Report cumulative semantic ingest stats.

This semantic phase is in addition to existing metadata and static push phases.

### Batch sizing

Default semantic request limits are:

- `max_rows = 100` chunk rows per request
- `max_payload_bytes = 16MB` estimated JSON request body size

Rules:

- one request carries only one logical group or one part of one logical group
- if a full `(doc_id, template_tag)` group fits within both limits, it may be sent whole in one request
- if a group exceeds either limit, the client should split that group into multiple numbered parts
- each part must carry the same `(doc_id, template_tag, group_hash, part_count)` metadata and its own `part_index`
- the server may enforce a higher hard cap such as `32MB`; requests beyond that cap should return `413 Payload Too Large`

This keeps request bodies bounded while preserving group-level reconciliation semantics.

### Ordering

Recommended order:

1. metadata push
2. static push (if enabled)
3. semantic chunk push (if `--embed-db` is provided)

Why:

- semantic chunks refer to `doc_id` and metadata fields that should already exist remotely
- static assets are independent, but keeping metadata first makes remote state easier to reason about

## Failure Behavior

### Default behavior

- If metadata push fails: abort the command as today
- If static push fails for some files: keep existing per-file failure behavior
- If semantic push fails for one semantic ingest request: fail the command and report the failing group or group part summary

### Partial batch semantics

The server should process semantic ingest batches transactionally where feasible:

- either the whole batch is committed
- or none of it is committed

If the storage layer cannot provide full transactionality, the server must at least ensure idempotent retries by honoring the upsert rules above.

### Retry safety

Semantic batch push must be retry-safe:

- re-sending the same batch or group part must not create duplicates
- unchanged chunks must remain `skipped`
- changed chunks must remain `updated`
- group-level cleanup must only happen after a full group has been reconstructed server-side

## Stats and Output

### New semantic push stats

Client-side stats should include:

- `received`
- `inserted`
- `updated`
- `skipped`
- `deleted`
- `batches_sent`
- `errors`

These can be a new `PushSemanticStats` dataclass, separate from existing metadata and static push stats.

### CLI summary

At the end of `paper db api push`, if semantic sync was enabled, print an extra semantic section such as:

- semantic batches sent
- semantic inserted
- semantic updated
- semantic skipped
- semantic deleted

If `--embed-db` was omitted, this section is omitted entirely.

## Integration with Query-time Search

This spec assumes the remote service will use the stored chunk vectors for document retrieval.

At query time:

1. the remote service embeds the query text
2. it searches the stored semantic vectors
3. it optionally combines vector hits with keyword hits
4. it optionally reranks results

The important cost-saving property is:

- **document embeddings are computed offline once, locally**
- **query embeddings are computed online per request**

## Design Decisions

### Why not upload LanceDB directly?

Because LanceDB is a local storage implementation detail, not an API contract.

Uploading the raw database would:

- couple client and server to the same storage engine
- make version compatibility fragile
- leak local on-disk layout into the network protocol
- make server-side deduplication and validation harder to reason about

### Why not recompute embeddings during push?

Because local `paper embed` has already paid that cost.

Recomputing at push time would:

- waste money and latency
- risk mismatch between local and remote embeddings
- duplicate logic already present in the local embedding pipeline

### Why is vector push optional?

Because some users may want:

- metadata-only remote sync
- static-only remote sync
- local-only semantic search

Making semantic sync opt-in preserves current workflows.

## Open Questions

These are intentionally deferred from this spec and can be settled in implementation planning:

1. Whether semantic push uses one new endpoint or separate `upsert` + `cleanup` endpoints.
2. Whether semantic ingest is added only to `paper db serve` admin API, or also to other deployment targets.
3. How long the server should retain incomplete staged group parts before garbage-collecting them.
