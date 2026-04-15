# Embedding / Rerank Provider Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `embedding` and `rerank` use the exact same provider routing semantics as the main provider stack while keeping them in independent config sections and removing all legacy compatibility paths.

**Architecture:** Keep `embedding` and `rerank` config blocks independent, but replace their simplified scalar endpoint fields with the same nested `base[] + key[]` route structure used by the top-level providers. Resolve one active provider/model via defaults or CLI override, then expand that provider into weighted runtime candidates using the same cooldown and quota-aware routing behavior as the main provider path.

**Tech Stack:** Python, Click, dataclasses, existing route-pool runtime, pytest, TOML config parsing.

---

### Task 1: Update embedding/rerank config schema

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py`
- Test: `python/deepresearch_flow/paper/tests/test_embedding_config.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- embedding config accepts `base = [{ url, weight, key = [...] }]`
- rerank config accepts the same nested route structure
- legacy `base_url` / `api_key` config is rejected
- existing old-shape tests are updated so prior scalar-field acceptance now fails fast

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embedding_config.py`

Expected: failures showing the parser still expects the old simplified fields or still accepts legacy fields.

**Step 3: Write minimal config changes**

Update `EmbeddingProviderConfig` and `RerankProviderConfig` to use `base: list[BaseConfig]`, then update parsing to:
- require `base`
- reject `base_url`
- reject `api_key`
- keep `default_provider` / `default_model`

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embedding_config.py`

Expected: PASS

### Task 2: Add runtime route-pool entrypoints for embedding and rerank

**Files:**
- Modify: `python/deepresearch_flow/paper/routing.py`
- Create or modify: `python/deepresearch_flow/paper/tests/test_embedding_routing.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- embedding resolves one provider/model, then expands weighted routes from that provider's `base × key`
- rerank does the same
- cooldown/quota handling chooses another route when available

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embedding_routing.py`

Expected: failures because no embedding/rerank runtime route entrypoint exists yet.

**Step 3: Write minimal routing code**

Add reusable helpers that:
- accept a resolved embedding or rerank provider/model
- build runtime candidates exactly like the main route pool
- expose identical cooldown/quota selection behavior

Implementation choice:
- extract the existing candidate-expansion logic into a shared private helper
- keep `RoutePool.from_selector()` on top of that helper for main models
- add dedicated `RoutePool.from_embedding_provider(...)` / `RoutePool.from_rerank_provider(...)` entrypoints that call the same helper

Pool lifecycle:
- `paper embed`: one embedding pool for the full batch job
- `paper search`: one embedding pool and one rerank pool per command invocation
- web/API semantic search: app-scoped pools reused across requests

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embedding_routing.py`

Expected: PASS

### Task 3: Wire embedding pipeline to routed endpoints

**Files:**
- Modify: `python/deepresearch_flow/paper/embed_pipeline.py`
- Test: `python/deepresearch_flow/paper/tests/test_embed_pipeline.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `paper embed` uses the active embedding provider/model but can fail over across weighted routes
- one failing route cools down and another route continues the batch

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_pipeline.py`

Expected: failures because the pipeline still reads a single `base_url/api_key`.

**Step 3: Write minimal implementation**

Refactor the embed pipeline to:
- resolve the active provider/model
- obtain a routed runtime endpoint
- call `call_embedding()` with the selected concrete route
- mark route cooldown/quota state on errors using the shared routing behavior

`python/deepresearch_flow/paper/embedding.py` should stay unchanged unless a small signature cleanup is truly necessary; the current intent is to keep it as a simple concrete-route request helper.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_pipeline.py`

Expected: PASS

### Task 4: Wire semantic search rerank path to routed endpoints

**Files:**
- Modify: `python/deepresearch_flow/paper/cli.py`
- Modify: `python/deepresearch_flow/paper/web/handlers/api.py`
- Modify: `python/deepresearch_flow/paper/reranker.py`
- Modify: `python/deepresearch_flow/paper/tests/test_embed_cli.py`
- Modify: `python/deepresearch_flow/paper/tests/test_semantic_api.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- semantic embedding query uses routed embedding endpoints
- rerank path uses routed rerank endpoints
- fallback occurs when one route is temporarily unavailable

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_cli.py python/deepresearch_flow/paper/tests/test_semantic_api.py`

Expected: failures because CLI/API paths still depend on scalar endpoint config.

**Step 3: Write minimal implementation**

Update CLI and web semantic search code to:
- resolve active embedding/rerank provider/model
- request concrete routes from the shared route runtime
- call request helpers with concrete route parameters

Reranker lifecycle choice:
- route pools own cooldown/quota state
- reranker instances must not own route state
- for each concrete routed request, instantiate a lightweight reranker client for that concrete route

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_cli.py python/deepresearch_flow/paper/tests/test_semantic_api.py`

Expected: PASS

### Task 5: Add CLI override flags

**Files:**
- Modify: `python/deepresearch_flow/paper/cli.py`
- Modify: `python/deepresearch_flow/paper/tests/test_embed_cli.py`

**Dependency note:**
- Task 5 is sequentially after Task 4 because both change `cli.py` and `test_embed_cli.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `paper embed --embedding provider/model`
- `paper search --embedding provider/model`
- `paper search --rerank provider/model`
- invalid `provider/model` override is rejected

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_cli.py`

Expected: failures because the new flags are not parsed yet.

**Step 3: Write minimal implementation**

Add CLI options:
- `--embedding`
- `--rerank`

Each override should:
- require `provider/model` syntax
- override the active config selection only
- leave provider-internal routing unchanged

**Step 4: Run tests to verify they pass**

Run: `uv run pytest -q python/deepresearch_flow/paper/tests/test_embed_cli.py`

Expected: PASS

### Task 6: Update examples and docs

**Files:**
- Modify: `config.example.toml` (if present)
- Modify: `README.md`
- Modify: `README_ZH.md`
- Inspect: `AGENTS.md` or other agent-facing docs if they mention embedding/rerank config fields

**Step 1: Write the failing checks**

Add or update lightweight doc/config checks if the repo already validates examples. Otherwise use manual verification only.

**Step 2: Run checks to verify current docs are outdated**

Run the smallest available config/doc validation command, or manually inspect the affected examples.

Expected: current examples still show legacy `base_url` / `api_key` style.

**Step 3: Write minimal documentation updates**

Update examples so `embedding` and `rerank` show only the new provider structure. Document:
- independent config sections
- identical routing semantics to main providers
- CLI overrides with `provider/model`
- explicit breaking change
- `type` remains part of the user-facing config shape, with parser behavior unchanged aside from the route-structure migration

**Step 4: Run checks to verify docs are aligned**

Run any available validation or manually inspect the updated examples.

Expected: examples and docs consistently use the new shape.

### Task 7: Run focused verification

**Files:**
- Verify only

**Step 1: Run focused test suite**

Run:

```bash
uv run pytest -q \
  python/deepresearch_flow/paper/tests/test_embedding_config.py \
  python/deepresearch_flow/paper/tests/test_embedding_routing.py \
  python/deepresearch_flow/paper/tests/test_embed_pipeline.py \
  python/deepresearch_flow/paper/tests/test_embed_cli.py \
  python/deepresearch_flow/paper/tests/test_semantic_api.py
```

Expected: PASS

**Step 2: Run targeted help checks**

Run:

```bash
uv run deepresearch-flow paper embed --help
uv run deepresearch-flow paper search --help
```

Expected: new `--embedding` and `--rerank` flags appear where intended.

**Step 3: Commit**

Commit focused changes in small slices, preferably:

```bash
git add python/deepresearch_flow/paper/config.py python/deepresearch_flow/paper/tests/test_embedding_config.py
git commit -m "refactor(paper): align embedding and rerank provider config"

git add python/deepresearch_flow/paper/routing.py python/deepresearch_flow/paper/tests/test_embedding_routing.py
git commit -m "feat(paper): route embedding and rerank through weighted providers"

git add python/deepresearch_flow/paper/embed_pipeline.py python/deepresearch_flow/paper/reranker.py python/deepresearch_flow/paper/cli.py python/deepresearch_flow/paper/web/handlers/api.py python/deepresearch_flow/paper/tests/test_embed_pipeline.py python/deepresearch_flow/paper/tests/test_embed_cli.py python/deepresearch_flow/paper/tests/test_semantic_api.py
git commit -m "feat(paper): wire embedding and rerank through routed endpoints"

git add README.md README_ZH.md config.example.toml
git commit -m "docs(paper): document routed embedding and rerank config"
```
