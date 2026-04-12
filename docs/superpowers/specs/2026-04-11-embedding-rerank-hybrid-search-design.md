# Embedding, Rerank & Hybrid Search Design Spec

**Date:** 2026-04-11
**Status:** Draft
**Scope:** Add LanceDB-backed vector indexing, hybrid (BM25 + vector) search, cloud reranking, and token-gated frontend access to the paper search pipeline.

## Overview

This change adds three capabilities to deepresearch-flow:

1. **Embedding pipeline** — `paper embed` converts extracted paper JSON into vector chunks stored in LanceDB.
2. **Hybrid search** — `paper search` and `/api/papers/semantic` combine BM25 keyword recall with vector recall, then rerank via cloud API.
3. **Token-gated frontend** — Web UI users enter an access token (stored in IndexedDB) to unlock semantic search; unauthenticated users fall back to keyword search.

## Goals

- Upgrade paper search from keyword-only to semantic + keyword hybrid.
- Use a single embedding model (bge-m3) across local Ollama and cloud API.
- Store vectors in an embedded database (LanceDB) with no external services.
- Prevent index corruption when switching embedding providers.
- Keep hybrid search behind an optional access token to prevent abuse.

## Non-Goals

- Multi-user authentication or RBAC.
- Local reranking (cloud-only in v1).
- Automatic embedding after `paper extract` (separate command in v1).
- Sparse/ColBERT retrieval from bge-m3 (dense vectors only in v1).

## Configuration

### New config sections

Embedding and rerank have their own independent provider lists, completely separate from the chat completion `[[providers]]`. This avoids mixing concerns and lets each model carry its own parameters (dimensions, max_context, etc.).

```toml
[embedding]
default_model = "Qwen3-Embedding-4B"
default_provider = "ollama"
dimensions = 1024                       # output dimensions (can be overridden per model)
normalized = true
batch_size = 32
chunk_max_tokens = 512
chunk_overlap_tokens = 64

[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
models = [
  { model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 32768 },
  { model_name = "bge-m3", dimensions = 1024, max_context = 8192 }
]

[[embedding.providers]]
name = "siliconflow"
type = "openai_compatible"
base_url = "https://api.siliconflow.cn/v1"
api_key = "env:SF_API_KEY"
models = [
  { model_name = "Qwen/Qwen3-Embedding-4B", dimensions = 2560, max_context = 32768 },
  { model_name = "bge-m3", dimensions = 1024, max_context = 8192 }
]

[rerank]
enabled = true
default_model = "BAAI/bge-reranker-v2-m3"
default_provider = "siliconflow"
top_n = 10

[[rerank.providers]]
name = "siliconflow"
type = "openai_compatible"
base_url = "https://api.siliconflow.cn/v1"
api_key = "env:SF_API_KEY"
models = [
  { model_name = "BAAI/bge-reranker-v2-m3", max_context = 8192, max_chunks_per_doc = 1024 },
  { model_name = "Qwen/Qwen3-Reranker-8B", max_context = 32768, instruction = "Rerank documents by relevance" }
]

[search]
vector_dir = "paper_vectors"
vector_top_k = 50
keyword_top_k = 30
hybrid = true
access_token = "env:SEARCH_ACCESS_TOKEN"   # optional; omit to allow open access
```

### Design decisions

**Embedding and rerank providers are independent from chat completion `[[providers]]`.** The chat completion providers carry weighted `base[]` with `key[]` pools, `structured_mode` capabilities, and `RoutePool` semantics. Embedding and rerank are structurally different:

- Embedding providers have a flat `base_url` + `api_key` (no weighted multi-endpoint routing needed for batch offline work).
- Each model carries its own parameters: `dimensions`, `max_context` for embedding; `max_context`, `max_chunks_per_doc`, `instruction` for rerank.
- No `is_support_embedding` / `is_support_rerank` flags on `ModelCapability`. The capability is implicit: if a model is declared under `[[embedding.providers]]`, it supports embedding.

**`default_model` + `default_provider` specify the active configuration.** CLI can override with `--model` and `--provider`.

**Per-model `dimensions` overrides the top-level `[embedding].dimensions`.** The top-level value is the index-level dimension used by LanceDB and validated by `index_meta.json`. The per-model value is what gets sent to the API. They must match for the active model; validation fails at startup if they diverge.

**`max_context` semantics:**

- Embedding: `chunk_max_tokens` must not exceed the active model's `max_context`. Validated at startup; violation is a fatal error.
- Rerank: document text passed to the rerank API is truncated to `max_context` tokens. Prevents API rejection for long chunks.

### Chat completion `[[providers]]` and `ModelCapability` are unchanged

No new fields on `ModelCapability`. The existing `[[providers]]` structure continues to serve chat completion only. Embedding and rerank are fully self-contained under `[embedding]` and `[rerank]`.

## Embedding pipeline

### Embedding API contract

All embedding providers (Ollama, DashScope, SiliconFlow) use the OpenAI-compatible `/v1/embeddings` endpoint.

Request:

```json
{
  "model": "bge-m3",
  "input": ["text1", "text2"],
  "dimensions": 1024
}
```

Response:

```json
{
  "data": [
    { "embedding": [0.1, 0.2, "..."], "index": 0 },
    { "embedding": [0.3, 0.4, "..."], "index": 1 }
  ],
  "usage": { "prompt_tokens": 42 }
}
```

Ollama confirmed: supports `/v1/embeddings` with `model`, `input` (string or array of strings), `encoding_format`, and `dimensions` fields.

### Embedding provider resolution

Embedding uses its own `[[embedding.providers]]` list, not the chat completion `[[providers]]`. At startup, `default_provider` + `default_model` are resolved to a `(base_url, api_key, model_name, dimensions, max_context)` tuple. No `RoutePool` or weighted selection — embedding providers have a flat `base_url` + `api_key`, suitable for batch offline work.

### Data sources

`paper embed` accepts two mutually exclusive data source modes:

1. **paper_infos JSON files** (`-i`, repeatable) — one or more extraction output files. The same paper may appear in multiple files extracted by different templates (e.g. `paper_infos_simple.json` and `paper_infos_deep_read.json`). All inputs are loaded and grouped by resolved paper identity (doc_id). Each template's extraction for a given document is embedded separately, tagged by `template_tag`.
2. **Snapshot DB + static export dir** (`--snapshot-db` + `--static-export-dir`) — an existing snapshot SQLite database plus its companion static export directory. The embed command reads paper metadata from the `paper` table (title, year, venue, authors, tags via join tables), queries `paper_summary` for available template tags, and loads summary content from `static_export/summary/{paper_id}/{template_tag}.json` for each template. Source markdown is loaded from `static_export/md/{source_md_content_hash}.md`, translated markdown from `static_export/md_translate/{lang}/{md_content_hash}.md`.

Both paths produce the same intermediate representation: a list of per-document records, each carrying metadata, template-specific structured fields, and optionally source/translated markdown. From that point, the chunking, embedding, and vector store layers are identical regardless of data source.

### Document identity (`doc_id`)

`doc_id` is the paper-level identity key, not a file path hash. The same paper appearing in multiple JSON files or with different source paths must resolve to the same `doc_id`.

- **Snapshot mode:** `doc_id` = `paper_id` from the database (already a stable identity).
- **JSON mode:** `doc_id` is resolved using the same identity logic as snapshot build — prefer DOI if present, then BibTeX key, then metadata fingerprint (title + authors + year), then fall back to SHA-256 of source_path. The resolution function is shared with `snapshot/builder.py` to guarantee consistency.

This prevents duplicate embeddings when the same paper appears under different file paths across multiple `-i` inputs.

### Chunking strategy

Documents are split into chunks before embedding. Because the project supports multiple extraction templates (simple, simple_phi, deep_read, etc.) with heterogeneous output shapes, chunking is driven by a **template adapter** that extracts canonical searchable fields from each record.

### Template adapter

Each adapter implements a function:

```python
def extract_searchable_fields(record: dict, template_tag: str) -> list[SearchableField]

@dataclass(frozen=True)
class SearchableField:
    field_name: str        # e.g. "title", "summary", "qa[0]"
    chunk_type: str        # title / abstract / content / qa / source_md / translated_md
    text: str
    template_tag: str      # e.g. "simple", "deep_read", or "" for template-independent fields
    lang: str              # language code, e.g. "en", "zh"; empty for non-translation fields
```

The adapter examines the record's actual keys and extracts what is present. Missing fields are silently skipped (not all templates produce all field types).

v1 ships adapters for `simple` and `simple_phi` templates. A fallback adapter handles unknown templates by scanning all string-valued top-level fields as `content` type.

### Multi-template handling

A single document may have been extracted by multiple templates. Each template's structured fields are embedded independently with its own `template_tag`. Template-independent content (title, source markdown, translated markdown) is embedded once with `template_tag = ""` to avoid duplication.

### Chunk types

| chunk_type | Typical source fields | Template-scoped | Strategy |
|-----------|----------------------|----------------|----------|
| `title` | `title` | No (shared) | Single chunk, no splitting |
| `abstract` | `abstract`, `summary` | Yes (per template) | Paragraph-first split (see below) |
| `content` | Other text fields (e.g. `findings`, `methodology`, `contributions`) | Yes (per template) | Paragraph-first split |
| `qa` | Q&A pairs (if present) | Yes (per template) | Each Q+A concatenated as one chunk, no splitting |
| `source_md` | Source markdown file | No (shared) | Paragraph-first split |
| `translated_md` | Translated markdown file | No (shared, per lang) | Paragraph-first split, `lang` field set to language code |

Each chunk carries a `field_name` (e.g. `deep_read/findings`, `simple/summary`, `qa[0]`) for result attribution. For template-scoped chunks, `field_name` is prefixed with the template tag.

### Paragraph-first chunking strategy

Chunks are built by accumulating complete paragraphs, not by sliding a fixed-size window over raw text. This preserves the natural semantic boundaries of academic writing.

Algorithm:

1. Split text into paragraphs by double newline (`\n\n`).
2. Initialize an empty accumulator.
3. For each paragraph:
   - If appending this paragraph keeps the accumulator within `chunk_max_tokens`, append it.
   - If appending would exceed the limit, flush the accumulator as one chunk and start a new accumulator with this paragraph.
   - If a single paragraph alone exceeds `chunk_max_tokens`, fall back to sliding window split for that paragraph only (`chunk_max_tokens` window, `chunk_overlap_tokens` overlap), then continue accumulating.
4. Flush any remaining accumulator as the final chunk.

This means:
- Most chunks are one or more complete paragraphs.
- `matched_chunk` in search results returns coherent, readable text.
- Only rare ultra-long paragraphs (e.g. a massive table or code block) hit the sliding window fallback.
- `chunk_overlap_tokens` only applies to the sliding window fallback, not to paragraph boundaries (paragraphs are self-contained; no overlap needed).

### Token counting

Token counting uses `tiktoken` (cl100k_base) as an approximate heuristic. The actual embedding model tokenizer may differ (e.g. XLM-R for bge-m3, Qwen tokenizer for Qwen3-Embedding). The `chunk_max_tokens` setting is a soft budget with ~20% margin to absorb tokenizer divergence. The paragraph-first strategy further reduces sensitivity to exact token counts since boundaries are structural, not positional.

## Vector store

### LanceDB

LanceDB is an embedded vector database. Storage is a single directory (default `paper_vectors/`), no daemon process. Installed via `pip install lancedb`.

### Capacity estimate

For a 4,000-paper corpus with multi-template extraction, source markdown, and one translation language (dimensions = 1024):

| Content | Chunks per paper | Total chunks | Storage |
|---------|-----------------|-------------|---------|
| Structured fields (per template, ~2 templates avg) | ~15 | 60K | ~400 MB |
| Source markdown | ~25 | 100K | ~600 MB |
| Translated markdown (1 lang) | ~25 | 100K | ~600 MB |
| **Total** | **~65** | **~260K** | **~1.6 GB** |

Storage scales linearly with `dimensions`: at 2560 dimensions the vector portion is ~2.5x larger (~4 GB total). At 260K rows, brute-force vector scan completes in 50-100ms regardless of dimension. No ANN index needed at this scale.

### Table schema

Table name: `paper_chunks`

| Column | Type | Description |
|--------|------|-------------|
| `id` | string | `{doc_id}_{template_key}_{chunk_type}_{chunk_index}` (unique row key; see ID serialization below) |
| `doc_id` | string | Paper-level identity (see Document identity section) |
| `source_path` | string | Original file path (empty for snapshot-only papers) |
| `template_tag` | string | Extraction template (e.g. `simple`, `deep_read`); empty for shared chunks |
| `chunk_type` | string | title / abstract / content / qa / source_md / translated_md |
| `chunk_index` | int | Chunk sequence number within this type+template |
| `field_name` | string | Source field (e.g. `deep_read/findings`, `simple/summary`, `qa[0]`) |
| `lang` | string | Language code for translated_md chunks (e.g. `zh`); empty otherwise |
| `text` | string | Chunk text |
| `content_hash` | string | SHA-256 of chunk text |
| `vector` | vector[N] | Embedding vector; N = `[embedding].dimensions` from config (e.g. 1024, 2560) |
| `title` | string | Document title (denormalized for display) |
| `year` | int | Publication year |
| `authors` | string | Comma-separated |
| `venue` | string | Publication venue |
| `tags` | string | Comma-separated |

Metadata columns are denormalized so LanceDB can filter directly (e.g. `where year = 2024 AND tags LIKE '%NLP%'`) without joining back to source data.

### Chunk ID serialization

The `id` column uses `_` as separator: `{doc_id}_{template_key}_{chunk_type}_{chunk_index}`.

`template_key` is derived from `template_tag`:
- Template-scoped chunks: `template_key` = `template_tag` (e.g. `simple`, `deep_read`).
- Shared chunks (title, source_md, translated_md): `template_key` = `_shared`.

The literal `_shared` is a reserved value. Template tags must not use this name.

For translated_md chunks, the language code is appended to chunk_type: `translated_md_zh`, `translated_md_en`. This keeps `id` globally unique without adding another separator dimension.

Examples:
- `abc123_simple_abstract_0`
- `abc123_deep_read_content_3`
- `abc123__shared_title_0`
- `abc123__shared_source_md_7`
- `abc123__shared_translated_md_zh_2`

### Index metadata (hard validation)

A file `index_meta.json` is stored in the vector directory:

```json
{
  "model": "Qwen3-Embedding-4B",
  "dimensions": 1024,
  "normalized": true,
  "provider": "ollama",
  "index_version": 1
}
```

Validation rules:

- `paper embed` reads `index_meta.json` on startup.
- If `model`, `dimensions`, or `normalized` do not match current config, the command fails immediately with an explicit error. This prevents silent corruption when switching providers or models.
- If `index_meta.json` does not exist (first run), it is created.
- `index_version` is a format version number. Schema changes increment it. Mismatch is a fatal error.

**`dimensions` drives the LanceDB vector column width.** The `paper_chunks` table's `vector` column is created with `pa.list_(pa.float32(), dimensions)` on first run, where `dimensions` comes from `index_meta.json`. This value is never hardcoded. Changing dimensions requires `--force` rebuild.

### Incremental update

`paper embed` runs incrementally by default. The update unit is a **group** defined as `(doc_id, template_key)`. Each group is tracked and rebuilt independently:

- **Shared group** `(doc_id, _shared)`: contains title, source_md, translated_md chunks.
- **Template group** `(doc_id, <template_tag>)`: contains abstract, content, qa chunks for that template.

A change in one group does not trigger rebuild of other groups for the same document. For example, re-extracting a paper with `deep_read` only rebuilds the `(doc_id, deep_read)` group; the `_shared` group and `(doc_id, simple)` group are untouched if their hashes still match.

Update procedure:

1. Read data source (JSON files or snapshot), generate chunks grouped by `(doc_id, template_key)`.
2. For each chunk, compute `content_hash` = SHA-256(text).
3. For each group, compute `group_hash` = SHA-256 of sorted `content_hash` values in that group.
4. Query LanceDB for existing `(doc_id, template_key) -> group_hash` (stored in a lightweight `_group_meta` table or derived from the chunks).
5. **Group hash matches** -> skip entirely.
6. **Group hash differs or new group** -> delete all chunks with matching `(doc_id, template_key)`, re-embed, write.
7. **Orphan groups** (in LanceDB but not in source data) -> delete all their chunks.
8. Update `index_meta.json` statistics (doc_count, template_count, chunk_count, last_updated).

### Force rebuild

`--force` deletes the entire vector directory (including `index_meta.json`) and rebuilds from scratch. This handles three scenarios:

- Re-embedding all documents after content changes.
- Switching to a different embedding model (index_meta model mismatch would otherwise be a fatal error).
- Schema version upgrade (index_meta `index_version` mismatch).

The command prints a warning line before deleting: `Removing existing vector index at <path> (--force)`. No interactive confirmation (CLI tool convention).

## Rerank

### Provider abstraction

Rerank is implemented behind a Protocol interface from the start:

```python
class RerankProvider(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        client: httpx.AsyncClient,
    ) -> RerankResult: ...

@dataclass(frozen=True)
class RerankResult:
    indices: list[int]
    scores: list[float]
```

The first implementation is `OpenAICompatibleReranker`, covering providers that expose a `/v1/rerank` endpoint (SiliconFlow, Jina, etc.).

### Rerank API contract

The reranker implementation sends a `POST /v1/rerank` request. The model name comes from `rerank.default_model` and is resolved against `[[rerank.providers]]` at startup. The active provider is determined by `rerank.default_provider`. Per-model parameters (`max_context`, `max_chunks_per_doc`, `instruction`) are read from the model entry and passed to the API call if present. No model name is hardcoded in the implementation.

Request (minimum required fields):

```json
{
  "model": "<from config>",
  "query": "...",
  "documents": ["doc1", "doc2", ...],
  "top_n": 10,
  "return_documents": false
}
```

The implementation extracts only `results[].index` and `results[].relevance_score` from the response. All other response fields (token usage, metadata, document text) are treated as optional and provider-specific. The implementation must tolerate missing or differently-shaped metadata fields across providers.

Provider-specific optional request fields (e.g. `instruction` for Qwen3-Reranker, `max_chunks_per_doc` for bge-reranker) are not sent by default. They can be added via provider-level config extension if needed in the future.

The rerank model and endpoint format should be verified against the provider's current API documentation before implementation, as providers update available models and response shapes frequently. The Protocol abstraction ensures new provider formats can be added without changing consumers.

### Rerank input

Rerank receives the best-matching chunk text per document, not the full document. This keeps latency and cost predictable.

### Degradation

If `rerank.enabled = false` or the rerank API call fails, the pipeline returns results sorted by vector similarity score. The system does not fail because rerank is unavailable.

## Search pipeline

### Query flow

```
User query
    |
    +-- embed query (bge-m3) -> LanceDB vector search (top vector_top_k)
    |                           optional metadata filter: year, venue, tags
    |
    +-- BM25/FTS keyword search (top keyword_top_k)
    |   (uses existing PaperIndex keyword matching)
    |
    v
Aggregate by doc_id: take highest chunk score per doc
Deduplicate and merge both result sets
    |
    v
Rerank (cloud API, bge-reranker-v2-m3)
    input: query + best chunk text per doc
    output: relevance_score ordering
    |
    v
Return top rerank.top_n results
```

### doc_id aggregation

Vector search returns chunk-level results. A single document may have chunks from multiple templates, source markdown, and translations. All chunks are aggregated to **document level by `doc_id`** — not by `(doc_id, template_tag)`. The goal is one result row per paper.

For each `doc_id`, the aggregation selects the single best chunk (highest cosine similarity). That chunk's text is sent to the reranker. The response includes attribution metadata from the winning chunk:

- `matched_chunk`: the chunk text
- `matched_field`: the field name (e.g. `deep_read/findings`, `simple/summary`)
- `matched_template`: the template tag (e.g. `deep_read`, or `_shared` for source_md/title)
- `matched_chunk_type`: e.g. `content`, `source_md`, `translated_md`
- `matched_lang`: language code if translated_md, empty otherwise

This means a search for a Chinese phrase might surface a hit via `translated_md` chunk, while a search for a technical term might surface a hit via `deep_read/methodology` chunk — both returning the same paper as one result row.

BM25/keyword results carry a keyword rank position but no vector score. Vector results carry a vector score but may have no keyword rank.

### Hybrid fusion: Reciprocal Rank Fusion (RRF)

When both vector and keyword results are present, they are merged using RRF:

```
rrf_score(doc) = sum over each list L where doc appears: 1 / (k + rank_in_L)
```

where `k = 60` (standard RRF constant). Each retrieval path (vector, keyword) is one list. Documents appearing in both lists get contributions from both ranks.

RRF produces a unified score for every candidate regardless of which retrieval path found it. This score is used for pre-rerank ordering and as the final score when rerank is disabled or fails.

### Modes

- `hybrid = true` (default): both vector and keyword recall, merged via RRF, reranked.
- `--no-hybrid`: vector-only recall, no RRF needed, still reranked.
- `--no-rerank`: vector + keyword recall (if hybrid), sorted by RRF score, no rerank API call.

### Score semantics in response

- When rerank succeeds: `score` = reranker's `relevance_score` (0-1 range, provider-defined).
- When rerank is disabled or fails: `score` = RRF score (hybrid mode) or cosine similarity (vector-only mode).
- The `score_type` field in the response indicates which: `"rerank"`, `"rrf"`, or `"cosine"`.

## Token-gated frontend access

### Backend

`search.access_token` in config is optional. When set:

- `/api/papers/semantic` requires `Authorization: Bearer <token>` header.
- Token is compared to config value (resolved via `resolve_key_value` for `env:` support).
- Mismatch or missing header returns `403 Forbidden`.
- The existing `/api/papers` keyword search endpoint is unaffected.

When `access_token` is not configured, `/api/papers/semantic` is open to all requests.

### Frontend

**No-token state:**

- Search bar works normally with keyword search.
- A lock icon button appears beside the search bar. Hover tooltip: "Unlock semantic search".
- Clicking opens a DaisyUI modal/popover with:
  - Input field, placeholder "Enter access token".
  - "Unlock" button.
  - On submit: sends a probe request to `/api/papers/semantic?q=test&top_n=1`.
  - Success: token saved to IndexedDB, lock icon changes to unlocked state, search switches to hybrid mode.
  - Failure: red inline error "Invalid token".

**Token state:**

- Page load reads token from IndexedDB.
- Search bar operates in hybrid mode. A small "Semantic" badge or icon change indicates the active mode.
- Every `/api/papers/semantic` request includes the `Authorization` header.
- If any request returns 403 (token revoked), IndexedDB is cleared, UI reverts to lock state, search falls back to keyword mode.

**IndexedDB schema:**

- Database name: `deepresearch_flow`
- Object store: `settings`
- Key: `search_access_token`
- Value: `{ token: "...", saved_at: "2026-04-11T..." }`

## CLI commands

### `paper embed`

Two data source modes (mutually exclusive):

```bash
# From one or more paper_infos JSON files (same paper, different templates)
deepresearch-flow paper embed \
  -c config.toml \
  -i paper_infos_simple.json \
  -i paper_infos_deep_read.json \
  --output-embed-db ./embed_vectors

# Optionally include source markdown and translated markdown directories
deepresearch-flow paper embed \
  -c config.toml \
  -i paper_infos_simple.json \
  --md-root ./markdowns \
  --md-translated-root ./translated_md \
  --output-embed-db ./embed_vectors

# From existing snapshot DB (when original JSON is lost)
# Source markdown and translations are read from static export automatically
deepresearch-flow paper embed \
  -c config.toml \
  --snapshot-db snapshot.db \
  --static-export-dir ./static_export \
  --output-embed-db ./embed_vectors

# Force full rebuild (deletes existing vector index first)
deepresearch-flow paper embed \
  -c config.toml \
  --snapshot-db snapshot.db \
  --static-export-dir ./static_export \
  --output-embed-db ./embed_vectors \
  --force
```

`-i` is repeatable. Multiple JSON files are merged by resolved paper identity (doc_id).

`template_tag` resolution for each JSON input follows a strict priority:

1. **CLI override** (`--template-tag <tag>`): if provided, applies to all `-i` inputs that don't self-declare. When multiple `-i` files are passed with different templates, use one `--template-tag` per `-i` pair is not supported; use option 2 or 3 instead.
2. **Explicit field in JSON**: if the top-level object or each record contains a `template_tag` or `prompt_template` field, that value is used.
3. **Fallback error**: if neither CLI nor JSON provides a template tag, the command fails with an explicit error listing which input files are missing template identification.

No filename-convention guessing. Template identity must be explicit.

`--md-root` and `--md-translated-root` are optional for JSON mode. When provided, source/translated markdown files are matched to documents by source_hash and embedded as `source_md` / `translated_md` chunks. For snapshot mode, these files are loaded from the static export directory automatically.

`--output-embed-db` specifies the LanceDB output directory. Falls back to `search.vector_dir` from config if omitted.

### `paper db snapshot build` integration

Snapshot build can optionally generate the vector index in the same pass:

```bash
deepresearch-flow paper db snapshot build \
  --input paper_infos.json \
  --output-db snapshot.db \
  --static-export-dir ./static_export \
  --output-embed-db ./embed_vectors
```

When `--output-embed-db` is specified, the build command runs embedding after snapshot construction, using the freshly built snapshot as data source. Without this flag, no embedding step runs (existing behavior unchanged).

### `paper search`

```bash
# Hybrid search with rerank
deepresearch-flow paper search \
  -c config.toml \
  --embed-db ./embed_vectors \
  -q "attention mechanism in transformer" --top-n 10

# With metadata filters
deepresearch-flow paper search -c config.toml --embed-db ./embed_vectors -q "..." --year 2024 --venue NeurIPS

# Without rerank
deepresearch-flow paper search -c config.toml --embed-db ./embed_vectors -q "..." --no-rerank

# Vector-only (no keyword recall)
deepresearch-flow paper search -c config.toml --embed-db ./embed_vectors -q "..." --no-hybrid
```

`--embed-db` specifies the LanceDB directory to query. Falls back to `search.vector_dir` from config if omitted.

## Web API

### New endpoint

```
GET /api/papers/semantic?q=<query>&top_n=10&year=2024&venue=NeurIPS&rerank=true
```

Requires `Authorization: Bearer <token>` header when `search.access_token` is configured.

Response format matches existing `/api/papers`, with additional fields per result:

- `score`: relevance score (see Score semantics section for interpretation)
- `score_type`: one of `"rerank"`, `"rrf"`, or `"cosine"` — indicates what `score` represents
- `matched_chunk`: the chunk text that matched best for this document
- `matched_field`: source field name (e.g. `deep_read/findings`, `simple/summary`)
- `matched_template`: template tag of the winning chunk (e.g. `deep_read`, `_shared`)
- `matched_chunk_type`: chunk type (e.g. `content`, `source_md`, `translated_md`)
- `matched_lang`: language code if `translated_md`, empty string otherwise

### Existing endpoint unchanged

`/api/papers` keyword search continues to work without authentication.

### Web server: loading the vector index

```bash
deepresearch-flow paper db api serve \
  --snapshot-db snapshot.db \
  --static-export-dir ./static_export \
  --embed-db ./embed_vectors
```

When `--embed-db` is provided, the server loads the LanceDB index and enables `/api/papers/semantic`. When omitted, only keyword search is available (current behavior, no breakage).

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/embedding.py` | Create | `/v1/embeddings` call, batch embedding |
| `python/deepresearch_flow/paper/reranker.py` | Create | RerankProvider Protocol, OpenAICompatibleReranker |
| `python/deepresearch_flow/paper/vector_store.py` | Create | LanceDB read/write, schema definition, incremental logic, index_meta validation |
| `python/deepresearch_flow/paper/chunker.py` | Create | Document chunking strategies, template adapters |
| `python/deepresearch_flow/paper/embed_source.py` | Create | Unified data source abstraction: load from JSON or snapshot DB + static export |
| `python/deepresearch_flow/paper/search.py` | Create | Hybrid search pipeline (embed query, retrieve, merge via RRF, rerank, aggregate) |
| `python/deepresearch_flow/paper/config.py` | Modify | Add `EmbeddingConfig` (with `EmbeddingProviderConfig`, `EmbeddingModelConfig`), `RerankConfig` (with `RerankProviderConfig`, `RerankModelConfig`), `SearchConfig` dataclasses. No changes to `ModelCapability`. |
| `python/deepresearch_flow/paper/cli.py` | Modify | Add `paper embed` and `paper search` commands with `--output-embed-db` / `--embed-db` / `--snapshot-db` |
| `python/deepresearch_flow/paper/snapshot/builder.py` | Modify | Add optional `--output-embed-db` to snapshot build |
| `python/deepresearch_flow/paper/db.py` | Modify | Register `--embed-db` on `api serve` command |
| `python/deepresearch_flow/paper/web/app.py` | Modify | Load LanceDB index when `embed_db` path is provided |
| `python/deepresearch_flow/paper/web/handlers/api.py` | Modify | Add `/api/papers/semantic` endpoint with token validation |
| Frontend search component | Modify | Token input UI, IndexedDB read/write, search mode switching |
| Tests | Create | Unit tests for each new module |

## New dependencies

```
lancedb        # embedded vector database
pyarrow        # LanceDB underlying format
tiktoken       # approximate token counting for chunk splitting (not exact bge-m3 tokenizer)
```

No `torch`, `sentence-transformers`, or other heavy ML dependencies. Local embedding runs entirely via Ollama HTTP API.
