# Advanced Search on `paper db api serve`

**Date:** 2026-04-17
**Status:** Draft for review (rev 5, review feedback applied)
**Supersedes:** Secondary-parity Step 3 of `docs/superpowers/plans/2026-04-11-embedding-rerank-hybrid-search.md` (deferred since 2026-04-11, now obsolete)

---

## 1. Goals

Add a new `GET /api/v1/search/advanced` endpoint plus a companion `POST /api/v1/search/advanced/verify-token` endpoint to the snapshot JSON API, so that the hosted paper database gains a token-gated semantic + keyword hybrid search capability on top of the deployed `snapshot.db` + `LanceDB` artifacts, without any rebuild, re-embed, or re-chunk.

### Frontend UX (contract)

- **Basic search box**: default-expanded, always usable, hits the existing `/api/v1/search`.
- **Advanced search panel**: collapsed by default; when expanded it contains two independent controls that are separate features, not a mode toggle:
  1. A **token input** with its own **Verify Token** button. Clicking it posts to `/api/v1/search/advanced/verify-token`.
  2. An **advanced search input** with its own **Advanced Search** button. Clicking it calls `/api/v1/search/advanced`.
- **Token storage**: IndexedDB, database `deepresearch_flow` version 1, object store `settings`, key `search_access_token`. This triple is shared between the Vue `frontend/` and the legacy `paper/web/` UI (`paper/web/static/js/index.js:10-12`) so that a user who has entered the token in either UI sees it pre-filled in the other on the same origin. Value format matches the legacy writer: object `{token: string, saved_at: ISO 8601 string}`. Readers in both UIs MUST accept either the object form or a bare string form (for forward-compat with future simplified writes).
- **Token lifecycle**:
  - On page load with a stored token → fire a verify request. 200 → show "verified"; 401 → clear the stored token and show "not verified".
  - On a user submitting a new token → verify first. Only persist and enable the advanced search button on success.
  - On an advanced search returning 401 → clear the stored token and flip UI back to "not verified".

### Backend contract

- `POST /api/v1/search/advanced/verify-token` — token check only, never runs retrieval.
- `GET /api/v1/search/advanced` — hybrid retrieval with bearer-token gate.
- `/api/v1/search` is **unchanged**.

### Data

Reuses the deployed `snapshot.db` + `LanceDB` read-only. No rebuild, no re-embed, no re-chunk.

### Retrieval granularity (critical)

- `paper_fts` in the current snapshot is **paper-level**: one row per paper, columns `title / summary / source / translated / metadata` (`paper/snapshot/schema.py:197`, `paper/snapshot/builder.py:1141`).
- LanceDB is **chunk-level**: one row per chunk with `paper_id / template_tag / chunk_type / chunk_index / text / vector / content_hash`.

Consequence: sparse retrieval produces `paper_id` rankings; dense retrieval produces chunk rankings. Fusion happens at paper level; chunk selection happens after fusion (see §4). The response is chunk-shaped, but the `scores.sparse` field on a result reflects **the paper's** sparse score, not a per-chunk BM25 score.

---

## 2. Non-Goals

### A. No changes to deployed data artifacts (hard constraint)

**A1.** No changes to the `paper_snapshot.db` schema (no `CREATE` / `ALTER` / `DROP` on any table or column). `paper`, `paper_fts`, `paper_fts_trigram`, `facet_*`, `paper_author`, etc. remain as-is. The live database is already in production; any schema change requires coordinated migration across all deployers (remote API, local dev, push pipeline), a risk out of proportion to this feature.

**A2.** No re-embedding, re-chunking, or overwriting of the existing LanceDB index. ~500k chunks at bge-m3 dimensions have a real cost already paid; rebuilding is pure waste. The advanced endpoint can operate directly on the existing vectors.

**A3.** No Contextual Retrieval (Anthropic 2024/09 style per-chunk context prefix). It would require re-embedding — violates A2.

**A4.** No section-aware re-chunking. Same reason as A2.

### B. No changes to shared core primitives (blast-radius control)

**B1.** Public interfaces of `paper/vector_store.py` (`open_store`, `write_chunks`, `delete_groups`, `query_vector`, `load_index_meta`, `validate_index_meta`, `encode_vector_b64`, `decode_vector_b64`) are unchanged. These are used by `paper embed`, `paper search`, `paper db api push`, and `paper/web/app.py`; altering signatures means editing four code paths at once.

**B2.** Public interfaces of `paper/embedding.py`, `paper/reranker.py`, `paper/routing.py`, `paper/search.py`, and `paper/chunker.py` are unchanged. The advanced module composes and decorates these primitives; it does not modify them.

**B3.** Existing `paper/config.py` dataclass field semantics are unchanged. New fields are **appended** to `SearchConfig` with the `advanced_*` prefix (additive, non-breaking).

### C. No changes to sibling surfaces (isolation)

**C1.** Existing endpoints on `paper/snapshot/api.py` (`/api/v1/search`, `/api/v1/papers/*`, `/api/v1/facets/*`, `/api/v1/stats`, `/api/v1/config`, `/api/v1/papers/match-bibtex`) are untouched.

**C2.** `paper/snapshot/admin.py`, `schema.py`, `migrate.py` are untouched. Admin is the write side, advanced is the read side; schema is the direct consequence of A1.

**C3.** Nothing under `paper/web/` is modified. The local-dev UI for `paper db serve` is a separate surface; `/api/papers/semantic` there is the web-UI semantic endpoint and is not touched.

**C4.** The existing basic search UI and behavior in the Vue frontend (`frontend/src/views/SearchView.vue`) are unchanged. Advanced is a new collapsible panel sitting next to basic, not a replacement.

### D. Capability boundary

**D1.** No LLM answer generation. This is a **retrieval** endpoint, not the G in RAG. Generation is the concern of downstream agents / chat UIs. Mixing generation in would change the response shape from "list of chunks" to "streaming tokens" and introduce hallucination liability.

**D2.** No generative LLM calls of any kind in this spec (deferred to later increments):
- No query rewriting (any form)
- No HyDE
- No multi-query expansion
- No LLM-as-judge evaluation

Embedding models and reranker models are **not** in this list — they are encoder / scoring models, not generative LLMs.

**D3.** No user account system. The token is a deployment-level shared secret (ops configures one), not per-user. A user-account system would require a `user` table (violates A1), password/OAuth infrastructure, session management, and an admin surface — an independent system out of scope here.

---

## 3. Endpoint contract

### 3.1 `POST /api/v1/search/advanced/verify-token`

**Request**
```
POST /api/v1/search/advanced/verify-token
Headers:
  Authorization: Bearer <token>
```
No body.

**Response**
- `200 {"valid": true}` — token matches the configured `search_access_token`
- `401 {"valid": false, "reason": "missing"}` — no `Authorization` header, or the prefix isn't `Bearer `
- `401 {"valid": false, "reason": "invalid"}` — token does not match

Runs no retrieval, consumes no RoutePool quota. Comparison uses `hmac.compare_digest` for constant-time equality.

### 3.2 `GET /api/v1/search/advanced`

**Request**
```
GET /api/v1/search/advanced
  ?q=<str>                      required, 1..advanced_max_query_length (default 500)
  &top_n=<int>                  default 10, max advanced_top_n_max (default 50)
  &filters.year=<int|range>     optional; range syntax "2020..2023"
  &filters.venue=<str>          optional; repeatable
  &filters.authors=<str>        optional; repeatable (normalized name)
  &filters.keywords=<str>       optional; repeatable
  &filters.tags=<str>           optional; repeatable
  &filters.lang=<str>           optional
  &mmr_lambda=<float>           default 0.6, [0,1]
  &rerank=<auto|always|never>   default auto

Headers:
  Authorization: Bearer <token>           required
  X-Request-Id: <uuid>                    optional; server generates if absent; echoed in response
```

**Successful response `200`**
```json
{
  "success": true,
  "trace_id": "01JZ...",
  "query": {
    "raw": "vision transformer pretraining",
    "normalized": "vision transformer pretraining",
    "applied_filters": {"year": {"min": 2020, "max": 2023}}
  },
  "results": [
    {
      "chunk_id": "p_abc_simple_content_0",
      "paper_id": "p_abc",
      "paper": {
        "title": "An Image is Worth 16x16 Words",
        "authors": ["Dosovitskiy A.", "..."],
        "year": "2020",
        "venue": "ICLR",
        "doi": "...",
        "source_hash": "abc123"
      },
      "chunk": {
        "text": "...",
        "field_name": "simple/content",
        "template_tag": "simple",
        "chunk_type": "content",
        "chunk_index": 0,
        "lang": "en"
      },
      "scores": {
        "dense": 0.8421,
        "sparse": 12.37,
        "fused": 0.0164,
        "reranker": 0.912,
        "final": 0.912
      }
    }
  ],
  "metadata": {
    "counts": {
      "dense_papers": 37, "sparse_papers": 28, "fused_papers": 48,
      "selected_chunks": 48, "deduped": 42, "reranked": 20, "returned": 10
    },
    "fusion": "rrf",
    "reranker": {"applied": true, "model": "bge-reranker-v2-m3"},
    "mmr": {"applied": true, "lambda": 0.6},
    "embedding": {"model": "bge-m3", "dimensions": 1024},
    "latency_ms": {
      "total": 847, "embed": 42, "dense": 38, "sparse": 18,
      "fusion": 3, "chunk_select": 12, "dedup": 2, "rerank": 612, "mmr": 5
    }
  },
  "degraded": false,
  "degradation": null
}
```

Notes on score semantics:
- `scores.dense` is the per-chunk cosine score from LanceDB. Absent when the chunk came from sparse-only selection (paper had no dense hit).
- `scores.sparse` is the **paper-level** BM25 score the chunk's paper received (inherited; all chunks from the same paper in the same response share this value). Absent when the paper had no sparse hit.
- `scores.fused` is the paper-level RRF score the chunk's paper received. Always present on non-degraded responses.
- `scores.reranker` is the per-chunk rerank score. Absent if rerank was skipped or failed.
- `scores.final` is the **relevance** score (= `reranker` if applied, else `fused`). It is not guaranteed to be monotonically decreasing in the returned array. When MMR runs (`mmr_lambda < 1.0`, default), the array is ordered by MMR selection sequence, which deliberately trades some relevance for diversity. Clients that want a strict relevance-ordered list must sort by `scores.final` themselves or set `mmr_lambda=1.0` on the request. When MMR is effectively disabled (`mmr_lambda == 1.0` or Stage 7 was skipped), array order equals `scores.final` descending.

**Degraded response (still `200`)**
`degraded: true` with `degradation.reason ∈ {"reranker_failed", "fts_unavailable", "embedding_failed"}`. Scores associated with the skipped stage are absent. See §4 for the exact behavior per reason.

**Error response**
```json
{
  "success": false,
  "trace_id": "...",
  "error": {
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

| HTTP | `error.code` | Triggering condition |
|---|---|---|
| 400 | `INVALID_QUERY` | `q` is empty or exceeds length limit |
| 400 | `INVALID_FILTER` | A filter value fails validation (illegal venue chars, unparseable year range, etc.) |
| 401 | `UNAUTHORIZED` | No `Authorization` header, malformed `Bearer ` prefix, or token does not match. The `error.details.reason ∈ {"missing", "invalid"}` distinguishes the two. |
| 503 | `VECTOR_STORE_UNAVAILABLE` | LanceDB directory cannot be opened or scanned. The advanced endpoint fundamentally needs chunk data from LanceDB to shape its response, so this is a hard failure, not a degradation. |
| 503 | `TOTAL_FAILURE` | Both sparse and dense branches raised, or the embedding call raised **and** sparse raised simultaneously. |

Note: `INDEX_MISMATCH` is **not** a runtime error. The index metadata check is a startup-time fail-fast (see §4 "Startup validation"). If the server is running, the index is already validated; a runtime mismatch is not possible unless an operator rebuilt the index under a running server, which is an unsupported operation.

Note: **401 instead of 403** for invalid tokens. HTTP 401 "Unauthorized" means "the credentials presented are not valid; re-authenticate," which matches bearer-token semantics. HTTP 403 is reserved for authenticated-but-forbidden, which does not occur here. The frontend contract (§9) therefore only needs to react to 401.

---

## 4. Retrieval pipeline

```
query
 │
 ├──▶ Stage 0   Ingress       auth + param validation → RequestSpec
 │
 ├──▶ Stage 1   Normalize     NFC + whitespace collapse + rough language detection
 │                            sparse branch reuses snapshot/text.py::rewrite_search_query
 │
 ├──▶ Stage 2   Filter        parse filters.* → (sql_where, lance_where)
 │                            filters.venue reuses search.validate_venue_filter
 │
 ├──▶ Stage 3a  Dense   ─┐
 │    Stage 3b  Sparse  ─┤──▶ asyncio.gather(return_exceptions=True)
 │                            Dense: call_embedding_with_route_pool → query_vector
 │                              → list of chunk hits {paper_id, chunk_id, dense_score, chunk_meta}
 │                            Sparse: paper_fts MATCH + bm25(...) + filter pushdown
 │                              → list of paper hits {paper_id, sparse_score}
 │                              (paper-level; one score per paper)
 │
 ├──▶ Stage 4   Fuse at       aggregate dense chunks to paper-level via max(dense_score)
 │              paper level   → paper_dense[paper_id] = max(dense_score of its chunks)
 │                            RRF(paper_dense.ranked_list, paper_sparse.ranked_list, k=60)
 │                            → paper_fused[paper_id] = fused_score
 │
 ├──▶ Stage 4.5 Chunk         for each paper in paper_fused (top-50 papers):
 │              selection       - if dense has chunks for this paper: pick best-dense chunk
 │                              - else (sparse-only paper): query LanceDB by paper_id,
 │                                prefer chunks with chunk_type in {abstract, title};
 │                                fall back to chunk_index 0
 │                            → list of (paper_id, chunk, fused_score) tuples
 │                            This stage always needs LanceDB; failure → VECTOR_STORE_UNAVAILABLE
 │
 ├──▶ Stage 5   Dedup         operates on selected chunks:
 │                            (1) content_hash dedup → keep highest fused score
 │                            (2) within top-50, cosine ≥ 0.95 on chunk vectors collapses
 │                                → keep highest fused score
 │                            (Stage 4.5 emits at most one chunk per paper, so no
 │                            explicit paper-level cap is needed in this iteration;
 │                            dedup here catches cases where two different papers
 │                            happen to share a content_hash.)
 │
 ├──▶ Stage 6   Rerank        RoutedReranker.rerank(query, chunks, top_n=advanced_rerank_top_n)
 │                            timeout=advanced_rerank_timeout_ms; failure → skip,
 │                            degraded.reason=reranker_failed
 │                            rerank=never skips; rerank=always bypasses skip heuristics
 │
 ├──▶ Stage 7   MMR           λ=mmr_lambda, select top-top_n from top-K
 │                            similarity = cosine over existing chunk vectors from LanceDB
 │
 └──▶ Stage 8   Assemble      SELECT paper metadata (authors/venue via facet joins);
                              build response payload
```

### Key parameters and rationale

| Parameter | Default | Rationale |
|---|---|---|
| BM25 weights `(5.0, 3.0, 1.0, 1.0, 2.0)` | Already used by `/api/v1/search` | Reuse the existing baseline; heavy title weight is standard for academic search |
| RRF `k` | 60 | Cormack 2009 original value; Azure / MongoDB / OpenSearch defaults; k ∈ {10,30,60,100} differ by < 2% |
| Dense top-k (chunks) | 50 | Enough for paper-level aggregation; > 100 blows up rerank cost |
| Sparse top-k (papers) | 30 | Sparse is high-precision / low-recall, 30 papers is enough |
| Post-fusion paper top-K | 50 | Papers carried into chunk selection + rerank |
| Dedup cosine threshold | 0.95 | Standard near-duplicate threshold; < 0.9 starts dropping distinct-but-related chunks |
| Rerank top-N | 20 | Feed top-50 from dedup into rerank, emit 20 to MMR |
| MMR λ | 0.6 | Academic search: relevance-dominated (> 0.5) but reserves some diversity |
| Rerank timeout | 1500ms | bge-reranker-v2-m3 on CPU, 20-doc batch p95 ≈ 600-800ms; 1500ms leaves headroom |

### Failure paths

Two categories: **degraded** (still HTTP 200, partial pipeline, `degraded: true` + `degradation.reason`) and **hard failures** (non-2xx, see §3 error table).

**Degraded responses (HTTP 200, `degraded: true`)**

| Trigger | `degradation.reason` | Behavior |
|---|---|---|
| Stage 3b sparse raises | `fts_unavailable` | Use dense-only ranking; paper-level ranks come directly from paper_dense; no RRF needed; Stage 4.5 / 5 / 6 / 7 proceed normally (dense chunks all carry vectors) |
| Stage 3a dense raises because embedding call raised | `embedding_failed` | Use sparse-only ranking; for each paper in sparse top-K, query LanceDB by `paper_id` filter to pull a representative chunk (chunks still have vectors, so MMR + cosine dedup still work); reranker still runs on the selected chunks |
| Stage 6 rerank times out or raises | `reranker_failed` | Skip rerank; order by fused score; MMR still runs |

**Hard failures (non-2xx)**

| Trigger | Status + code | Reasoning |
|---|---|---|
| Both Stage 3a dense and Stage 3b sparse raise | 503 `TOTAL_FAILURE` | No candidates at all; cannot honor contract |
| Stage 4.5 cannot open or query LanceDB at all | 503 `VECTOR_STORE_UNAVAILABLE` | LanceDB is required to produce chunk-shaped responses; the contract cannot be honored without it |

### Startup validation (fail-fast)

When `api serve` starts with `--embed-db` and `config.search.advanced_enabled == True`:
1. Open the LanceDB directory; read `index_meta.json`.
2. Resolve active embedding via `paper_config.embedding.resolve_active()`.
3. Assert `index.model == embedding.default_model` (or `canonical_model` match), `index.dimensions == embedding.dimensions`, `index.provider == embedding.default_provider`, `index.normalized == embedding.normalized`.
4. Any mismatch → CLI exits with `ClickException` **before** `uvicorn` binds. The process does not enter request-serving state with a known-bad index.

### LanceDB location resolution (precedence)

The LanceDB directory can come from two places. Precedence at startup is:

1. **CLI `--embed-db <path>`** (highest).
2. **`config.search.vector_dir`** from the paper config TOML.
3. Neither provided → if `config.search.advanced_enabled == True`, startup fails with `ClickException("Advanced search requires --embed-db or config.search.vector_dir")`. If `advanced_enabled == False`, nothing is required and the advanced routes are simply not mounted.

If both are provided and they differ, CLI wins; a warning is logged identifying both paths so operators notice the mismatch in their config.

---

## 5. File map

### New backend files

```
python/deepresearch_flow/paper/snapshot/advanced/
  __init__.py              # exports: create_advanced_routes, AdvancedSearchContext
  config.py                # AdvancedSearchContext dataclass
  handler.py               # Starlette handlers: _api_search_advanced, _api_verify_token
  auth.py                  # bearer token extraction + constant-time compare
  pipeline.py              # orchestrator: stage sequence + failure paths
  normalize.py             # Stage 1: query normalization + language detection
  filters.py               # Stage 2: parse filters.* → (sql_where, lance_where)
  retrieve_dense.py        # Stage 3a: embed + LanceDB query → chunk hits
  retrieve_sparse.py       # Stage 3b: paper_fts MATCH + BM25 → paper hits
  fusion.py                # Stage 4: paper-level RRF
  chunk_select.py          # Stage 4.5: per-paper representative chunk selection
  dedup.py                 # Stage 5: content_hash + cosine
  mmr.py                   # Stage 7
  rerank_adapter.py        # Stage 6: RoutedReranker wrapper with timeout
  response.py              # Stage 8: response assembly
  errors.py                # error codes + typed exceptions
  tests/
    test_advanced_normalize.py
    test_advanced_filters.py
    test_advanced_fusion.py
    test_advanced_chunk_select.py
    test_advanced_dedup.py
    test_advanced_mmr.py
    test_advanced_rerank_adapter.py
    test_advanced_auth.py
    test_advanced_retrieve_dense.py
    test_advanced_retrieve_sparse.py
    test_advanced_pipeline.py
    test_advanced_api.py   # end-to-end integration
```

**Hard rule**: nothing under `snapshot/advanced/` may import from `paper/web/`. Only these are importable: `paper/vector_store`, `embedding`, `reranker`, `routing`, `search`, `config`, `utils`, `snapshot/common`, `snapshot/text`.

### New frontend files (this repo, Vue + Vite)

Test files follow the existing convention in `frontend/src/__tests__/*.test.ts` (e.g. `useSearchState.test.ts`, `useFacetStats.test.ts`). Vitest is the only frontend test runner used in this spec; no Playwright is introduced. End-to-end scenarios are executed via Vitest component/integration tests with a stubbed `fetch` layer — this avoids adding Playwright to `frontend/package.json` and keeps this spec implementable against the current repo's dev dependencies (`vitest`, `@vue/test-utils`, `jsdom`).

```
frontend/src/lib/
  token-db.ts              # IndexedDB wrapper: get/set/clear search_access_token under
                           # DB "deepresearch_flow", store "settings", key "search_access_token"
                           # Write format matches paper/web/static/js/index.js:writeToken
                           # Read accepts both object {token, saved_at} and bare string forms
  advanced-search.ts       # client: verifyToken(token), advancedSearch(params, token)
                           # hits /api/v1/search/advanced/verify-token and /api/v1/search/advanced
frontend/src/components/
  AdvancedSearchPanel.vue  # collapsible panel: token input + verify button + advanced
                           # query input + search button + state indicator
  AdvancedSearchResults.vue  # renders the advanced endpoint's chunk-shaped result schema
                           # (distinct from basic search results which are paper-shaped)
frontend/src/composables/
  useAdvancedSearchToken.ts  # Vue 3 composable wrapping token state machine
                           # exposes: state, verify(token), clear(), onAuthFailure()
frontend/src/__tests__/
  tokenDb.test.ts
  advancedSearch.test.ts
  useAdvancedSearchToken.test.ts
  AdvancedSearchPanel.test.ts
  advancedSearchFlow.test.ts    # integration: full verify + search flow with stubbed fetch
```

### Modified backend files

| File | Change | Size |
|---|---|---|
| `paper/snapshot/api.py` | Register new routes; wire `app.state.advanced` from CLI params | ~20 lines |
| `paper/db.py` | Add `--embed-db` / `--search-access-token` / `--config` to `api_serve`; resolve LanceDB path per §4 precedence | ~40 lines |
| `paper/config.py` | Append `advanced_*` fields to `SearchConfig` | ~10 lines |

### Modified frontend files

| File | Change |
|---|---|
| `frontend/src/views/SearchView.vue` | Mount the `AdvancedSearchPanel` below the existing basic search; no changes to existing basic search elements, state, or URL handling |
| `frontend/src/lib/api.ts` | Re-export `advancedSearch` and `verifyToken` from `advanced-search.ts` so callers have a single import surface (`import { advancedSearch, verifyToken } from '@/lib/api'`) |

### Untouched

`paper/web/**`, `paper/snapshot/admin.py`, `paper/snapshot/schema.py`, `paper/snapshot/migrate.py`, `paper/vector_store.py`, `paper/embedding.py`, `paper/reranker.py`, `paper/routing.py`, `paper/search.py`, `paper/chunker.py`, `paper/db_ops.py`, and all existing `frontend/src/views/*.vue` and `frontend/src/lib/*.ts` files other than those listed above.

---

## 6. Configuration

### Append to `SearchConfig` (additive, no semantic changes to existing fields)

```python
@dataclass(frozen=True)
class SearchConfig:
    # existing
    vector_dir: str
    vector_top_k: int
    keyword_top_k: int
    hybrid: bool
    access_token: str | None = None

    # new advanced endpoint fields
    advanced_enabled: bool = False           # master switch; routes are not mounted when False
    advanced_rrf_k: int = 60
    advanced_dense_top_k: int = 50
    advanced_sparse_top_k: int = 30
    advanced_post_fusion_top_k: int = 50
    advanced_dedup_cosine_threshold: float = 0.95
    advanced_rerank_top_n: int = 20
    advanced_mmr_lambda_default: float = 0.6
    advanced_rerank_timeout_ms: int = 1500
    advanced_top_n_max: int = 50
    advanced_max_query_length: int = 500
```

### `config.toml` example

```toml
[search]
vector_dir = "./embed_db"
vector_top_k = 50
keyword_top_k = 30
hybrid = true
access_token = "env:SEARCH_ACCESS_TOKEN"

advanced_enabled = true
# other advanced_* fields use defaults
```

---

## 7. CLI and integration

### New CLI flags on `paper db api serve`

```
--embed-db PATH              LanceDB directory; overrides config.search.vector_dir
--config PATH                paper config TOML; default ./config.toml
--search-access-token STR    bearer token for advanced endpoint
                             envvar: SEARCH_ACCESS_TOKEN (same name as the existing
                             flag on `paper db serve`; a token set once in the
                             operator's environment works for both commands)
```

### Startup sequence

1. Parse CLI + load `paper_config`.
2. If `config.search.advanced_enabled == True`:
   - **Resolve the LanceDB directory** per precedence: `--embed-db` if given, else `config.search.vector_dir`, else `ClickException`. If CLI and config both present and they differ, CLI wins; a warning is logged naming both paths.
   - **Resolve the search access token** per precedence: `--search-access-token` if given, else env `SEARCH_ACCESS_TOKEN` if set, else `config.search.access_token` (which itself supports the existing `env:VAR_NAME` indirection via `resolve_key_value`), else `ClickException("Advanced search requires a token via --search-access-token, SEARCH_ACCESS_TOKEN, or config.search.access_token")`. The env var name `SEARCH_ACCESS_TOKEN` is deliberately the same one already used by `paper db serve` (`paper/db.py:1622`) so that an operator who has configured the token once for the local web UI gets it picked up automatically by `paper db api serve`. No migration shim is needed because there is no prior advanced-search env var to migrate from.
   - Open LanceDB, read `index_meta.json`.
   - Validate `index_meta` against `config.embedding.resolve_active()`.
   - Any mismatch → `ClickException`; do not start the server.
   - Build `RoutePool.from_embedding_provider(config.embedding)`. If `config.rerank.enabled`, also build `RoutePool.from_rerank_provider(config.rerank)`.
   - Assemble `AdvancedSearchContext` with the resolved path and token.
3. If `config.search.advanced_enabled == False`, advanced routes are not mounted; the server runs as before (existing behavior unchanged). Neither `--embed-db` nor `--search-access-token` is required in this mode.
4. Pass the optional `AdvancedSearchContext` into `create_app(..., advanced_config=ctx)`.
5. `uvicorn.run(app, ...)`.

### Extend `snapshot/api.py::create_app` signature

```python
def create_app(
    *,
    snapshot_db: Path,
    static_base_url: str,
    cors_allowed_origins: list[str] | None = None,
    limits: ApiLimits | None = None,
    admin_token: str | None = None,
    # new
    advanced_config: AdvancedSearchContext | None = None,
) -> Starlette:
```

```python
@dataclass(frozen=True)
class AdvancedSearchContext:
    embed_db_path: Path
    lance_db: Any                    # lancedb.DBConnection
    paper_config: PaperConfig
    embedding_route_pool: RoutePool
    rerank_route_pool: RoutePool | None
    search_access_token: str
    search_config: SearchConfig      # exposes advanced_* params
```

Route registration (about 10 added lines in api.py):

```python
if advanced_config is not None:
    from deepresearch_flow.paper.snapshot.advanced import create_advanced_routes
    app.routes.extend(create_advanced_routes(advanced_config))
    app.state.advanced = advanced_config
```

`create_advanced_routes(ctx)` returns:

```python
[
    Route("/api/v1/search/advanced", _api_search_advanced, methods=["GET"]),
    Route("/api/v1/search/advanced/verify-token", _api_verify_token, methods=["POST"]),
]
```

Existing routes remain bit-for-bit unchanged: `_api_search`, `_api_stats`, `_api_paper_detail`, `_api_facet_*`. The admin sub-app mount logic is not touched.

---

## 8. Backend tests

Follows the `CLAUDE.md` black-box policy: every test sees only the interface contract, never implementation.

| Module | Test file | Contract covered |
|---|---|---|
| `normalize.py` | `test_advanced_normalize.py` | NFC normalization, whitespace compaction, CJK ratio detection, `rewrite_search_query` produces valid FTS syntax |
| `filters.py` | `test_advanced_filters.py` | Year-range parsing, venue illegal-char rejection, composite filters emit correct SQL WHERE and LanceDB where |
| `fusion.py` | `test_advanced_fusion.py` | Paper-level RRF deterministic on fixed inputs; empty channels handled; tied ranks stable; scores preserved on both channels |
| `chunk_select.py` | `test_advanced_chunk_select.py` | Paper with dense hits → best-dense chunk selected; paper without dense hits → LanceDB queried, abstract/title chunks preferred; fallback to chunk_index 0 |
| `dedup.py` | `test_advanced_dedup.py` | content_hash dedup keeps the higher-scored chunk; cosine ≥ 0.95 collapses |
| `mmr.py` | `test_advanced_mmr.py` | λ = 0 pure diversity; λ = 1 pure relevance; stable tie-break |
| `rerank_adapter.py` | `test_advanced_rerank_adapter.py` | Timeout triggers degradation; reranker exception triggers degradation; happy path returns top_n |
| `auth.py` | `test_advanced_auth.py` | No header / malformed Bearer / token mismatch / token match — all four, all producing the correct status + reason |
| `retrieve_dense.py` | `test_advanced_retrieve_dense.py` | LanceDB where string is assembled correctly; missing embed_db raises a typed error; returns chunk-level hits |
| `retrieve_sparse.py` | `test_advanced_retrieve_sparse.py` | MATCH expression is assembled correctly; filter pushdown to WHERE; zh queries fall back to trigram; returns paper-level hits |
| `pipeline.py` | `test_advanced_pipeline.py` | Dense raises → sparse-only with `embedding_failed`; sparse raises → dense-only with `fts_unavailable`; both raise → 503 `TOTAL_FAILURE`; LanceDB unavailable at Stage 4.5 → 503 `VECTOR_STORE_UNAVAILABLE`; rerank fails → skipped with `reranker_failed` |
| End-to-end | `test_advanced_api.py` | Small in-memory snapshot + tiny LanceDB fixture; happy path; 401 (both `missing` and `invalid`); 400 paths; 503 paths; startup `INDEX_MISMATCH` via CLI fixture |

**Coverage**: ≥ 80% line coverage under `snapshot/advanced/`; 100% on `auth.py` and `chunk_select.py` (logic-sensitive).

---

## 9. Frontend tests

The frontend lives in this repo at `frontend/` (Vue 3 + Vite + TypeScript). Tests live under `frontend/src/__tests__/*.test.ts`, matching the existing convention (`useSearchState.test.ts`, `useFacetStats.test.ts`, etc.). Only Vitest is used; no new dev-tooling is introduced to `frontend/package.json` (which currently ships `vitest`, `@vue/test-utils`, `jsdom`, and no Playwright). "Integration" scenarios run inside Vitest with a stubbed `fetch`; we do not set up real-browser E2E in this spec.

### Test tiers

| Tier | Tooling | Coverage |
|---|---|---|
| Unit | Vitest + jsdom | `token-db.ts`, `advanced-search.ts`, `useAdvancedSearchToken` composable |
| Component | Vitest + `@vue/test-utils` | `AdvancedSearchPanel.vue`, `AdvancedSearchResults.vue` |
| Integration | Vitest with stubbed `fetch` (e.g. `vi.fn()` on `globalThis.fetch` or MSW if added later) | Full verify + advanced-search flow end-to-end within jsdom |

### Required contract scenarios

**IndexedDB token storage (`token-db.ts`)**
- Uses database `deepresearch_flow` version 1, object store `settings`, key `search_access_token`. Triple is shared with `paper/web/static/js/index.js` so a token stored by either UI is visible to the other on the same origin.
- **Write format**: object `{token: string, saved_at: ISO 8601 string}` (matches the legacy writer at `paper/web/static/js/index.js:writeToken`).
- **Read format**: MUST accept both forms, mirroring the legacy reader at `paper/web/static/js/index.js:readToken`:
  - object with a `token` string field → returns `token`
  - bare string → returns the string
  - anything else or absent → returns `null`
- `setToken(t)`: writes the object form; subsequent `getToken()` returns `t`
- `getToken()`: returns the stored token, or `null` if unset; never throws
- `clearToken()`: deletes the key; a subsequent `getToken()` returns `null`
- Concurrent reads/writes do not corrupt state
- A token written by `paper/web/` is readable here; a token written here is readable by `paper/web/` (bidirectional same-origin compat is a required test assertion)

**Token state machine (`useAdvancedSearchToken`)**
- Page load, no token in IndexedDB → state: `"not-verified"`
- Page load, token in IndexedDB, verify returns 200 → state: `"verified"`
- Page load, token in IndexedDB, verify returns 401 → state: `"not-verified"` AND IndexedDB is cleared
- User submits a new token, verify 200 → state: `"verified"` AND IndexedDB holds the new token
- User submits a new token, verify 401 → state: `"not-verified"` AND IndexedDB is cleared (any prior token is also treated as invalid)
- Advanced search returns 401 mid-session → state flips to `"not-verified"`, IndexedDB cleared, a toast prompts the user to re-verify

**UI components**
- `AdvancedSearchPanel.vue`: default collapsed; expanding reveals the four sub-controls (token input, Verify button, advanced search input, advanced Search button)
- In `"not-verified"` state: the advanced search input and button are disabled
- In `"verified"` state: both are enabled
- While verifying: the Verify button shows a loading indicator
- While searching: the advanced Search button shows a loading indicator
- After Verify completes: the UI shows the outcome (`✓ verified` / `✗ invalid`) regardless of success
- `AdvancedSearchResults.vue` renders the chunk-shaped result schema distinct from basic search's paper-shaped results

**Network layer (`advanced-search.ts`)**
- `verifyToken(token)`: POSTs to `/api/v1/search/advanced/verify-token`, no body, `Authorization: Bearer <token>`; maps 200 → `{valid: true}`, 401 → `{valid: false, reason}`
- `advancedSearch(params, token)`: GETs `/api/v1/search/advanced` with the `Authorization` header and a query string built from `params` (q, top_n, filters.*, mmr_lambda, rerank); returns typed response
- All non-2xx responses surface a user-visible error (never silently swallowed)

**Integration scenarios (Vitest, `fetch` stubbed)**
- New user: first render → expand advanced panel → enter token → click Verify → see `✓ verified` → type query → click Search → chunk-shaped results render
- Returning user: IndexedDB pre-populated with a token → mount app → advanced panel shows `✓ verified` without user input → search directly
- Token revoked: returning user searches, stubbed response returns 401 → UI immediately flips to `not-verified`, IndexedDB cleared, toast surfaced
- Invalid token: user enters a wrong token → UI shows `✗ invalid`, IndexedDB not written, advanced search button stays disabled
- Basic regression: whatever happens on the advanced panel, the basic search input, button, and result rendering behave exactly as before (basic-search paths exercised against the stubbed fetch the same way)

### Coverage

- `token-db.ts` and token state machine: **100%** (security-sensitive, many edges)
- UI components: ≥ 80%
- Integration: all five scenarios above must pass
