# Embedding / Rerank Provider Routing Design

## Context

The paper pipeline currently treats `embedding` and `rerank` as simplified single-endpoint integrations. Each provider is configured with one `base_url` and one `api_key`, then selected by `default_provider` + `default_model`.

That is inconsistent with the main model routing stack, which already supports:

- multiple base URLs
- multiple API keys
- weights on bases and keys
- weighted route selection
- cooldown after errors
- quota-aware backoff

The goal of this change is to make `embedding` and `rerank` use the same provider semantics as the main provider stack, without collapsing them into the top-level `[[providers]]` section.

## Requirements

- `[[embedding.providers]]` and `[[rerank.providers]]` remain independent config sections.
- Their structure must be fully aligned with the top-level provider structure.
- Runtime behavior must match the main provider routing behavior exactly.
- There must be no compatibility path for legacy `base_url` / `api_key` fields.
- `default_provider` + `default_model` remain the selection entrypoints.
- CLI must allow overriding the active embedding/rerank model with `provider/model` syntax, aligned with `extract`.

## Non-Goals

- Reusing top-level `[[providers]]` for embedding or rerank.
- Supporting multiple embedding models at once in a single index build.
- Supporting multiple rerank models at once in a single search request.
- Preserving or auto-migrating old simplified config fields.

## Configuration Shape

### Embedding

`embedding.providers` will move from:

```toml
[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
models = [{ model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 8192 }]
```

to:

```toml
[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base = [
  { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }
]
models = [{ model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 8192 }]
```

### Rerank

`rerank.providers` will use the same structure:

```toml
[[rerank.providers]]
name = "siliconflow"
type = "openai_compatible"
base = [
  { url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "env:SILICONFLOW_API_KEY", weight = 1 }] }
]
models = [{ model_name = "BAAI/bge-reranker-v2-m3", max_context = 8192 }]
```

### Validation Rules

- `base_url` and `api_key` are invalid and must raise clear config errors.
- `type` remains explicitly declared by the user, matching the main provider config shape.
- `base` must be non-empty.
- Each `base` entry must contain `url`, positive `weight`, and non-empty `key`.
- Each `key` entry must contain `value` and positive `weight`.
- `default_provider` must resolve to a declared provider.
- `default_model` must resolve inside the selected provider.
- Existing embedding dimension and max-context checks remain in place.

## Runtime Model

### Selection

Each embedding/rerank request proceeds in two phases:

1. Choose the active provider/model using `default_provider` + `default_model`, optionally overridden by CLI.
2. Expand the selected provider's `base[]` and `key[]` into weighted runtime candidates.

This mirrors the main route pool behavior, except there is no main-model pool for embedding/rerank. Each request family works against one chosen provider/model, with routing variation only inside that provider.

### Routing Semantics

Embedding and rerank runtime behavior must match the main provider routing stack:

- weighted selection over base/key candidates
- identical cooldown behavior after route errors
- identical quota handling and pause-until-reset behavior
- identical waiting behavior when all routes are temporarily unavailable

The implementation should reuse the existing route-pool machinery instead of duplicating routing logic.

### RoutePool Adaptation

`RoutePool.from_selector()` cannot be reused directly because it expects top-level `ProviderConfig` objects and `ParsedModelSelector` / `MainModelConfig`. The adaptation strategy is:

- extract the candidate-expansion logic in `routing.py` into a shared internal helper that accepts a provider-like object with `base[]`, one selected model, and a stable route-id prefix
- keep `RoutePool.from_selector()` for the main model path, rewritten to call that shared helper
- add explicit embedding/rerank entrypoints such as `RoutePool.from_embedding_provider(...)` and `RoutePool.from_rerank_provider(...)`, which call the same shared helper

This keeps runtime semantics identical while avoiding a fake conversion into top-level `ProviderConfig`.

### Pool Lifecycle

Pool lifetime differs by command type:

- `paper embed`: create one embedding route pool per command invocation and reuse it for the full batch job so cooldown/quota state survives across batches
- `paper search`: create pools once per command invocation; this is sufficient because the command is single-shot, but the pool still spans the full embedding + rerank flow within that invocation
- long-lived web/API semantic search: create embedding and rerank pools once during app startup (or first config load) and store them on app state so cooldown/quota state persists across requests

## CLI Surface

CLI overrides use the same `provider/model` shape as `extract`:

- `--embedding provider/model`
- `--rerank provider/model`

These overrides change only the active provider/model selection. They do not change the provider's internal route pool.

Expected command coverage:

- `paper embed --embedding provider/model`
- `paper search --embedding provider/model --rerank provider/model`
- long-lived web or API entrypoints that already consume paper config should use the same provider/model resolution and app-scoped route pools where applicable

## Code-Level Design

### Config Layer

Modify the embedding/rerank config dataclasses so their providers use the same nested route structure as the top-level provider config:

- embedding provider config gains `base: list[BaseConfig]`
- rerank provider config gains `base: list[BaseConfig]`
- old scalar fields are removed

`resolve_active()` should continue to resolve only provider/model. Route expansion belongs in runtime routing code, not in config parsing.
The existing `type` behavior remains unchanged: it stays part of the user-facing config shape and examples, but this design does not introduce any new `type` validation beyond the current parser behavior.

### Runtime Layer

Add route-pool entrypoints for embedding and rerank that:

- accept the resolved provider/model pair
- expand all `base × key` combinations
- produce weighted runtime candidates
- reuse existing cooldown/quota handling

The embedding pipeline and semantic search paths should consume those runtime routes instead of directly calling `base_url` / `api_key`.

### Request Layer

`call_embedding()` and rerank execution stay simple request functions. They should operate on resolved route inputs:

- one concrete URL
- one concrete key
- one concrete model

Route selection and retry/cooldown policy remain outside the request helpers.

For rerank specifically, the current fixed-endpoint constructor pattern is no longer the right lifecycle boundary. The routed call path should select one concrete route per request (or retry attempt), then instantiate a lightweight reranker client for that concrete route.

The important constraint is that reranker instances must no longer own global route state; route pools do.

## Testing Strategy

Tests remain black-box:

- config accepts only the new shape
- old shape fails with clear validation errors
- embedding route selection honors CLI override and default selection
- rerank route selection honors CLI override and default selection
- weighted candidate expansion, cooldown, and quota pause behavior match the provider router
- embedding pipeline and semantic search recover when one route cools down and another route is available

Coverage areas:

- `config.py`
- routing helpers
- `embed_pipeline.py`
- semantic search / rerank path
- CLI parsing
- docs/examples

## Migration Impact

This is a breaking config change.

- users must replace `base_url` / `api_key` with `base = [{ url, weight, key = [...] }]`
- there is no fallback parsing
- examples and README must be updated in both English and Chinese

## Success Criteria

- embedding and rerank provider config is structurally identical to the main provider stack
- runtime route behavior is identical to the main route pool semantics
- CLI override syntax uses `provider/model`
- old simplified config fails fast
- docs and examples reflect only the new structure
