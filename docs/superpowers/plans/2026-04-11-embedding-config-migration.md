# Embedding Config Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the existing embedding/rerank implementation with the updated spec: independent provider lists, paragraph-first chunking, dynamic vector dimensions.

**Architecture:** Four targeted refactors on already-working code. Each task is independently testable. Config schema changes propagate to all consumers.

**Tech Stack:** Python 3.14, click, tomllib, LanceDB, tiktoken, pytest via `uv run pytest`

**Spec:** `docs/superpowers/specs/2026-04-11-embedding-rerank-hybrid-search-design.md` (sections: Configuration, Paragraph-first chunking, Index metadata)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/config.py` | Modify | Replace `EmbeddingConfig.provider: str` / `RerankConfig.provider: str` with nested `EmbeddingProviderConfig` / `RerankProviderConfig` lists; remove `is_support_embedding` / `is_support_rerank` from `ModelCapability`; add `EmbeddingModelConfig` / `RerankModelConfig` with per-model params |
| `python/deepresearch_flow/paper/chunker.py` | Modify | Replace sliding window with paragraph-first algorithm |
| `python/deepresearch_flow/paper/vector_store.py` | Modify | `_chunks_schema()` takes `dimensions` param; `open_store` / `write_chunks` pass dynamic dimension |
| `python/deepresearch_flow/paper/embed_pipeline.py` | Modify | Resolve embedding provider from `config.embedding.providers` instead of `select_runtime_route` |
| `python/deepresearch_flow/paper/web/handlers/api.py` | Modify | `_embed_query` resolves from `config.embedding.providers`; reranker construction in `api_papers_semantic` resolves from `config.rerank.providers` |
| `python/deepresearch_flow/paper/cli.py` | Modify | Update `paper embed` / `paper search` to use new config shape |
| `python/deepresearch_flow/paper/reranker.py` | Modify | Accept per-model params (`max_context`, `max_chunks_per_doc`, `instruction`) from config |
| `python/deepresearch_flow/paper/tests/test_embedding_config.py` | Modify | Test new config shape |
| `python/deepresearch_flow/paper/tests/test_chunker.py` | Modify | Test paragraph-first behavior |
| `python/deepresearch_flow/paper/tests/test_vector_store.py` | Modify | Test dynamic dimensions |
| `python/deepresearch_flow/paper/tests/test_embed_pipeline.py` | Modify | Test new provider resolution |
| `python/deepresearch_flow/paper/tests/test_embed_cli.py` | Modify | Update TOML fixtures to `[[embedding.providers]]` syntax |
| `python/deepresearch_flow/paper/tests/test_semantic_api.py` | Modify | Update app fixtures, pass dimensions to write_chunks |
| `python/deepresearch_flow/paper/tests/test_reranker.py` | Modify | Update if it references RerankConfig |

---

### Task 1: Config Schema — Independent Embedding/Rerank Providers

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py`
- Modify: `python/deepresearch_flow/paper/tests/test_embedding_config.py`

- [ ] **Step 1: Write failing tests for new config shape**

Update `python/deepresearch_flow/paper/tests/test_embedding_config.py` — replace existing embedding config tests with:

```python
def _full_config_v2(tmp_path: Path, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [embedding]
        default_model = "Qwen3-Embedding-4B"
        default_provider = "ollama"
        dimensions = 1024
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
        api_key = "env:SF_KEY"
        models = [
          { model_name = "Qwen/Qwen3-Embedding-4B", dimensions = 2560, max_context = 32768 }
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
        api_key = "env:SF_KEY"
        models = [
          { model_name = "BAAI/bge-reranker-v2-m3", max_context = 8192, max_chunks_per_doc = 1024 },
          { model_name = "Qwen/Qwen3-Reranker-8B", max_context = 32768, instruction = "Rerank by relevance" }
        ]

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
        """
        + extra,
        encoding="utf-8",
    )
    return config_path


def test_loads_embedding_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    assert config.embedding is not None
    assert config.embedding.default_model == "Qwen3-Embedding-4B"
    assert config.embedding.default_provider == "ollama"
    assert len(config.embedding.providers) == 2
    ollama = config.embedding.providers[0]
    assert ollama.name == "ollama"
    assert ollama.base_url == "http://localhost:11434/v1"
    assert len(ollama.models) == 2
    assert ollama.models[0].model_name == "Qwen3-Embedding-4B"
    assert ollama.models[0].dimensions == 1024
    assert ollama.models[0].max_context == 32768


def test_loads_rerank_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    assert config.rerank is not None
    assert config.rerank.default_model == "BAAI/bge-reranker-v2-m3"
    assert len(config.rerank.providers) == 1
    sf = config.rerank.providers[0]
    assert sf.models[0].max_context == 8192
    assert sf.models[0].max_chunks_per_doc == 1024
    assert sf.models[1].instruction == "Rerank by relevance"


def test_resolves_embedding_provider_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    provider, model = config.embedding.resolve_active()
    assert provider.name == "ollama"
    assert model.model_name == "Qwen3-Embedding-4B"
    assert model.dimensions == 1024


def test_embedding_dimensions_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    config = load_config(str(_full_config_v2(tmp_path)))
    # top-level dimensions=1024 but siliconflow model has dimensions=2560
    # Switching to siliconflow should detect the mismatch
    config_sf = replace(config.embedding, default_provider="siliconflow", default_model="Qwen/Qwen3-Embedding-4B")
    with pytest.raises(ValueError, match="dimensions"):
        config_sf.resolve_active()


def test_chunk_max_tokens_exceeds_max_context_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_KEY", "test-sf-key")
    extra = ""
    # Use a model with max_context=100 but chunk_max_tokens=512
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]
        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [embedding]
        default_model = "tiny"
        default_provider = "local"
        dimensions = 128
        normalized = true
        chunk_max_tokens = 512

        [[embedding.providers]]
        name = "local"
        type = "openai_compatible"
        base_url = "http://localhost/v1"
        api_key = "k"
        models = [{ model_name = "tiny", dimensions = 128, max_context = 100 }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    with pytest.raises(ValueError, match="max_context"):
        config.embedding.resolve_active()


def test_model_capability_has_no_embedding_rerank_flags(tmp_path: Path) -> None:
    """ModelCapability no longer carries is_support_embedding / is_support_rerank."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]
        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "k", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    model = config.providers[0].models[0]
    assert not hasattr(model, "is_support_embedding")
    assert not hasattr(model, "is_support_rerank")
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding_config.py -v`

Expected: FAIL

- [ ] **Step 3: Add new dataclasses**

In `python/deepresearch_flow/paper/config.py`, add:

```python
@dataclass(frozen=True)
class EmbeddingModelConfig:
    model_name: str
    dimensions: int
    max_context: int


@dataclass(frozen=True)
class EmbeddingProviderConfig:
    name: str
    type: str
    base_url: str
    api_key: str
    models: list[EmbeddingModelConfig]


@dataclass(frozen=True)
class RerankModelConfig:
    model_name: str
    max_context: int
    max_chunks_per_doc: int | None = None
    instruction: str | None = None


@dataclass(frozen=True)
class RerankProviderConfig:
    name: str
    type: str
    base_url: str
    api_key: str
    models: list[RerankModelConfig]
```

- [ ] **Step 4: Rewrite `EmbeddingConfig` and `RerankConfig`**

```python
@dataclass(frozen=True)
class EmbeddingConfig:
    default_model: str
    default_provider: str
    dimensions: int
    normalized: bool
    batch_size: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    providers: list[EmbeddingProviderConfig]

    def resolve_active(self) -> tuple[EmbeddingProviderConfig, EmbeddingModelConfig]:
        for p in self.providers:
            if p.name == self.default_provider:
                for m in p.models:
                    if m.model_name == self.default_model:
                        if m.dimensions != self.dimensions:
                            raise ValueError(
                                f"Embedding dimensions mismatch: top-level={self.dimensions}, "
                                f"model {m.model_name}={m.dimensions}"
                            )
                        if self.chunk_max_tokens > m.max_context:
                            raise ValueError(
                                f"chunk_max_tokens ({self.chunk_max_tokens}) exceeds "
                                f"model {m.model_name} max_context ({m.max_context})"
                            )
                        return p, m
                raise ValueError(f"Model '{self.default_model}' not found in embedding provider '{self.default_provider}'")
        raise ValueError(f"Embedding provider '{self.default_provider}' not found")


@dataclass(frozen=True)
class RerankConfig:
    enabled: bool
    default_model: str
    default_provider: str
    top_n: int
    providers: list[RerankProviderConfig]

    def resolve_active(self) -> tuple[RerankProviderConfig, RerankModelConfig]:
        for p in self.providers:
            if p.name == self.default_provider:
                for m in p.models:
                    if m.model_name == self.default_model:
                        return p, m
                raise ValueError(f"Model '{self.default_model}' not found in rerank provider '{self.default_provider}'")
        raise ValueError(f"Rerank provider '{self.default_provider}' not found")
```

- [ ] **Step 5: Remove `is_support_embedding` / `is_support_rerank` from `ModelCapability`**

Revert `ModelCapability` to:

```python
@dataclass(frozen=True)
class ModelCapability:
    model_name: str
    is_stream: bool
    is_support_json_schema: bool
    is_support_json_object: bool
```

Remove the two fields and update `_parse_model_capabilities` to stop parsing them. Existing configs that still have these fields should be silently ignored (not rejected).

- [ ] **Step 6: Update `load_config` parsing**

Replace the old `embedding`/`rerank` parsing with:

```python
    embedding_data = data.get("embedding")
    embedding = None
    if embedding_data is not None:
        emb_providers = []
        for ep in embedding_data.get("providers", []):
            emb_models = [
                EmbeddingModelConfig(
                    model_name=str(m["model_name"]),
                    dimensions=int(m.get("dimensions", embedding_data.get("dimensions", 1024))),
                    max_context=int(m.get("max_context", 8192)),
                )
                for m in ep.get("models", [])
            ]
            emb_providers.append(
                EmbeddingProviderConfig(
                    name=str(ep["name"]),
                    type=str(ep.get("type", "openai_compatible")),
                    base_url=str(ep["base_url"]),
                    api_key=str(ep["api_key"]),
                    models=emb_models,
                )
            )
        embedding = EmbeddingConfig(
            default_model=str(embedding_data["default_model"]),
            default_provider=str(embedding_data["default_provider"]),
            dimensions=int(embedding_data.get("dimensions", 1024)),
            normalized=_as_bool(embedding_data.get("normalized"), True),
            batch_size=_as_int(embedding_data.get("batch_size"), 32),
            chunk_max_tokens=_as_int(embedding_data.get("chunk_max_tokens"), 512),
            chunk_overlap_tokens=_as_int(embedding_data.get("chunk_overlap_tokens"), 64),
            providers=emb_providers,
        )
```

Same pattern for `rerank` with `RerankProviderConfig` / `RerankModelConfig`.

- [ ] **Step 7: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding_config.py -v`

Expected: PASS

- [ ] **Step 8: Run existing config tests for regressions**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_config.py -v`

Expected: PASS — existing configs without embedding/rerank still work; `ModelCapability` without the removed fields still parses.

- [ ] **Step 9: Commit**

```bash
git add python/deepresearch_flow/paper/config.py python/deepresearch_flow/paper/tests/test_embedding_config.py
git commit -m "refactor: independent embedding/rerank provider config, remove ModelCapability embedding flags"
```

---

### Task 2: Paragraph-First Chunking

**Files:**
- Modify: `python/deepresearch_flow/paper/chunker.py`
- Modify: `python/deepresearch_flow/paper/tests/test_chunker.py`

- [ ] **Step 1: Add failing tests for paragraph-first behavior**

Add to `python/deepresearch_flow/paper/tests/test_chunker.py`:

```python
def test_paragraph_first_keeps_paragraphs_intact() -> None:
    text = "First paragraph about attention.\n\nSecond paragraph about transformers.\n\nThird paragraph about BERT."
    field = SearchableField(
        field_name="simple/summary",
        chunk_type="content",
        text=text,
        template_tag="simple",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=500, overlap_tokens=64)
    assert len(chunks) == 1
    assert "First paragraph" in chunks[0].text
    assert "Third paragraph" in chunks[0].text


def test_paragraph_first_splits_at_paragraph_boundary() -> None:
    para_a = "Word " * 80  # ~80 tokens
    para_b = "Term " * 80  # ~80 tokens
    para_c = "Text " * 80  # ~80 tokens
    text = f"{para_a.strip()}\n\n{para_b.strip()}\n\n{para_c.strip()}"
    field = SearchableField(
        field_name="deep_read/findings",
        chunk_type="content",
        text=text,
        template_tag="deep_read",
        lang="",
    )
    # max_tokens=120: each paragraph is ~80 tokens, so two paragraphs (~160) exceeds limit
    chunks = chunk_fields([field], max_tokens=120, overlap_tokens=10)
    assert len(chunks) >= 2
    # Each chunk should be a complete paragraph (not cut mid-word)
    for chunk in chunks:
        assert "\n\n" not in chunk.text.strip()


def test_paragraph_first_fallback_on_single_huge_paragraph() -> None:
    huge_para = "word " * 500  # one paragraph with ~500 tokens
    field = SearchableField(
        field_name="source_md",
        chunk_type="source_md",
        text=huge_para.strip(),
        template_tag="",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1  # forced to split within the paragraph


def test_paragraph_first_no_overlap_between_paragraph_chunks() -> None:
    para_a = "Alpha paragraph content here."
    para_b = "Beta paragraph content here."
    text = f"{para_a}\n\n{para_b}"
    field = SearchableField(
        field_name="content",
        chunk_type="content",
        text=text,
        template_tag="simple",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=20, overlap_tokens=5)
    # Paragraphs are small enough to be their own chunks — no overlap between them
    if len(chunks) == 2:
        assert "Alpha" in chunks[0].text
        assert "Alpha" not in chunks[1].text
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_chunker.py -v -k paragraph`

Expected: FAIL

- [ ] **Step 3: Rewrite `_sliding_window_split` into `_paragraph_first_split`**

In `python/deepresearch_flow/paper/chunker.py`, replace the splitting function:

```python
def _paragraph_first_split(
    text: str, *, max_tokens: int, overlap_tokens: int
) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    chunks: list[str] = []
    accumulator: list[str] = []
    acc_tokens = 0

    for para in paragraphs:
        para_tokens = _count_tokens(para)

        if para_tokens > max_tokens:
            # Flush accumulator first
            if accumulator:
                chunks.append("\n\n".join(accumulator))
                accumulator = []
                acc_tokens = 0
            # Fallback: sliding window for this one oversized paragraph
            chunks.extend(_sliding_window_split(para, max_tokens=max_tokens, overlap_tokens=overlap_tokens))
            continue

        if acc_tokens + para_tokens > max_tokens and accumulator:
            chunks.append("\n\n".join(accumulator))
            accumulator = []
            acc_tokens = 0

        accumulator.append(para)
        acc_tokens += para_tokens

    if accumulator:
        chunks.append("\n\n".join(accumulator))

    return chunks
```

Keep `_sliding_window_split` as a private fallback, only used for single oversized paragraphs.

- [ ] **Step 4: Update `chunk_fields` to call paragraph-first**

Change `chunk_fields` to use `_paragraph_first_split` instead of `_sliding_window_split` for all non-`_NO_SPLIT_TYPES`:

```python
        else:
            segments = _paragraph_first_split(
                field.text, max_tokens=max_tokens, overlap_tokens=overlap_tokens
            )
```

- [ ] **Step 5: Run all chunker tests**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_chunker.py -v`

Expected: PASS (both old and new tests)

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/chunker.py python/deepresearch_flow/paper/tests/test_chunker.py
git commit -m "refactor: paragraph-first chunking with sliding window fallback for oversized paragraphs"
```

---

### Task 3: Dynamic Vector Dimensions

**Files:**
- Modify: `python/deepresearch_flow/paper/vector_store.py`
- Modify: `python/deepresearch_flow/paper/tests/test_vector_store.py`

- [ ] **Step 1: Add failing test for dynamic dimensions**

Add to `python/deepresearch_flow/paper/tests/test_vector_store.py`:

```python
def test_create_table_with_custom_dimensions(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1__shared_title_0",
            doc_id="doc1",
            source_path="test.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Test",
            content_hash="abc",
            vector=[0.1] * 256,  # 256 dimensions, not 1024
            title="Test",
            year=2024,
            authors="A",
            venue="V",
            tags="t",
        ),
    ]
    write_chunks(db, rows, dimensions=256)
    results = query_vector(db, [0.1] * 256, top_k=5)
    assert len(results) >= 1


def test_query_vector_with_mismatched_dimensions_fails(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1__shared_title_0",
            doc_id="doc1",
            source_path="test.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Test",
            content_hash="abc",
            vector=[0.1] * 256,
            title="Test",
            year=2024,
            authors="A",
            venue="V",
            tags="t",
        ),
    ]
    write_chunks(db, rows, dimensions=256)
    with pytest.raises(Exception):
        query_vector(db, [0.1] * 1024, top_k=5)  # wrong dimension
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store.py -v -k custom_dimensions`

Expected: FAIL

- [ ] **Step 3: Make `_chunks_schema` accept dimensions parameter**

In `python/deepresearch_flow/paper/vector_store.py`:

```python
def _chunks_schema(dimensions: int) -> pa.Schema:
    return pa.schema(
        [
            # ... all other fields unchanged ...
            pa.field("vector", pa.list_(pa.float32(), dimensions)),
            # ...
        ]
    )
```

- [ ] **Step 4: Make `dimensions` a required parameter in `write_chunks`**

```python
def write_chunks(db: lancedb.DBConnection, rows: list[ChunkRow], *, dimensions: int) -> None:
    if not rows:
        return
    data = [...]  # unchanged
    table_names = _list_table_names(db)
    if _CHUNKS_TABLE in table_names:
        table = db.open_table(_CHUNKS_TABLE)
        table.add(data)
    else:
        db.create_table(_CHUNKS_TABLE, data, schema=_chunks_schema(dimensions))
```

`dimensions` is keyword-only with no default. This forces every caller to pass it explicitly. Callers to update:

1. `python/deepresearch_flow/paper/embed_pipeline.py` — `write_chunks(db, rows, dimensions=embedding_config.dimensions)`
2. `python/deepresearch_flow/paper/tests/test_vector_store.py` — all `write_chunks(db, rows, dimensions=...)` calls
3. `python/deepresearch_flow/paper/tests/test_semantic_api.py` — `_create_test_embed_db()` helper

Grep for `write_chunks(` to ensure no call site is missed.

- [ ] **Step 5: Run all vector store tests**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/vector_store.py python/deepresearch_flow/paper/tests/test_vector_store.py
git commit -m "refactor: dynamic vector dimensions from config, not hardcoded 1024"
```

---

### Task 4: Decouple Embedding/Rerank Call Sites from Main Providers

**Files:**
- Modify: `python/deepresearch_flow/paper/embed_pipeline.py`
- Modify: `python/deepresearch_flow/paper/web/handlers/api.py`
- Modify: `python/deepresearch_flow/paper/cli.py`
- Modify: `python/deepresearch_flow/paper/reranker.py`
- Modify: `python/deepresearch_flow/paper/tests/test_embed_pipeline.py`

- [ ] **Step 1: Update embed_pipeline to use `config.embedding.resolve_active()`**

In `python/deepresearch_flow/paper/embed_pipeline.py`, replace the `select_runtime_route` provider resolution block with:

```python
    provider, model = embedding_config.resolve_active()
    base_url = provider.base_url
    api_key = resolve_key_value(provider.api_key)
```

Remove imports of `ParsedModelSelector`, `resolve_model_capability`, `select_runtime_route`.

- [ ] **Step 2: Update `_embed_query` in API handler**

In `python/deepresearch_flow/paper/web/handlers/api.py`, replace the `_embed_query` function:

```python
async def _embed_query(
    text: str, config: PaperConfig, client: httpx.AsyncClient
) -> list[float]:
    from deepresearch_flow.paper.embedding import call_embedding
    from deepresearch_flow.paper.config import resolve_key_value

    provider, model = config.embedding.resolve_active()
    result = await call_embedding(
        base_url=provider.base_url,
        api_key=resolve_key_value(provider.api_key),
        model=model.model_name,
        texts=[text],
        dimensions=model.dimensions,
        client=client,
    )
    return result.vectors[0]
```

Remove imports of `ParsedModelSelector`, `select_runtime_route`.

- [ ] **Step 3: Update reranker to accept per-model params**

In `python/deepresearch_flow/paper/reranker.py`, update `OpenAICompatibleReranker.__init__`:

```python
class OpenAICompatibleReranker:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_context: int = 8192,
        max_chunks_per_doc: int | None = None,
        instruction: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._max_context = max_context
        self._max_chunks_per_doc = max_chunks_per_doc
        self._instruction = instruction
```

In `rerank()`, include optional fields in the request body:

```python
        if self._max_chunks_per_doc is not None:
            body["max_chunks_per_doc"] = self._max_chunks_per_doc
        if self._instruction is not None:
            body["instruction"] = self._instruction
```

Truncate each document to `self._max_context` tokens before sending.

- [ ] **Step 4: Update ALL reranker construction sites**

Three call sites construct a reranker — all must use `config.rerank.resolve_active()`:

1. `python/deepresearch_flow/paper/cli.py` — `paper search` command
2. `python/deepresearch_flow/paper/web/handlers/api.py` — `api_papers_semantic` handler
3. `python/deepresearch_flow/paper/search.py` — if it constructs internally

Each site:

```python
    if config.rerank and config.rerank.enabled:
        rr_provider, rr_model = config.rerank.resolve_active()
        reranker = OpenAICompatibleReranker(
            base_url=rr_provider.base_url,
            api_key=resolve_key_value(rr_provider.api_key),
            model=rr_model.model_name,
            max_context=rr_model.max_context,
            max_chunks_per_doc=rr_model.max_chunks_per_doc,
            instruction=rr_model.instruction,
        )
```

Verify no call site still references `config.providers` or `select_runtime_route` for rerank.

- [ ] **Step 5: Update ALL test fixtures that depend on embedding/rerank config**

These test files build config objects or fixtures with the old `EmbeddingConfig.provider: str` / `RerankConfig.provider: str` shape and must be updated to the new `providers: list[...]` + `resolve_active()` shape:

1. `python/deepresearch_flow/paper/tests/test_embed_pipeline.py` — `_test_config()` must use `EmbeddingProviderConfig` list
2. `python/deepresearch_flow/paper/tests/test_embed_cli.py` — `_write_embed_config()` TOML fixture must use `[[embedding.providers]]` syntax
3. `python/deepresearch_flow/paper/tests/test_semantic_api.py` — `_make_app()` must set `app.state.paper_config` with new shape; `_create_test_embed_db()` must pass `dimensions=` to `write_chunks`
4. `python/deepresearch_flow/paper/tests/test_reranker.py` — if it references `RerankConfig`, update to new shape

Remove references to `select_runtime_route` and `is_support_embedding`/`is_support_rerank` from all test files.

- [ ] **Step 6: Run all tests**

Run:

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_embedding_config.py \
  python/deepresearch_flow/paper/tests/test_chunker.py \
  python/deepresearch_flow/paper/tests/test_embedding.py \
  python/deepresearch_flow/paper/tests/test_reranker.py \
  python/deepresearch_flow/paper/tests/test_vector_store.py \
  python/deepresearch_flow/paper/tests/test_embed_source.py \
  python/deepresearch_flow/paper/tests/test_search.py \
  python/deepresearch_flow/paper/tests/test_embed_pipeline.py \
  python/deepresearch_flow/paper/tests/test_embed_cli.py \
  python/deepresearch_flow/paper/tests/test_semantic_api.py -v
```

Expected: PASS

- [ ] **Step 7: Run full regression**

Run:

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/ \
  python/deepresearch_flow/translator/tests/ -q
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add \
  python/deepresearch_flow/paper/embed_pipeline.py \
  python/deepresearch_flow/paper/web/handlers/api.py \
  python/deepresearch_flow/paper/cli.py \
  python/deepresearch_flow/paper/reranker.py \
  python/deepresearch_flow/paper/tests/test_embed_pipeline.py
git commit -m "refactor: decouple embedding/rerank from main providers, use independent provider config"
```

---

### Task 5: Update config.example.toml and README

**Files:**
- Modify: `config.example.toml`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Replace embedding/rerank example in config.example.toml**

Replace the old `[embedding]` / `[rerank]` sections with the new independent provider format from the spec.

- [ ] **Step 2: Update README examples**

Update both READMEs to show the new config shape with `[[embedding.providers]]` and `[[rerank.providers]]`.

- [ ] **Step 3: Commit**

```bash
git add config.example.toml README.md README_ZH.md
git commit -m "docs: update config examples for independent embedding/rerank providers"
```
