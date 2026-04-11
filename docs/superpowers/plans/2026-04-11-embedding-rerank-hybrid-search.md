# Embedding, Rerank & Hybrid Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LanceDB-backed vector indexing, hybrid (BM25 + vector + RRF) search, cloud reranking, and token-gated semantic search to the paper pipeline.

**Architecture:** Extend config with `[embedding]`, `[rerank]`, `[search]` sections. New modules: `embedding.py` (API call), `chunker.py` (template adapters + splitting), `vector_store.py` (LanceDB + index_meta), `reranker.py` (Protocol + OpenAI-compat impl), `embed_source.py` (JSON + snapshot data loading), `search.py` (hybrid pipeline + RRF + aggregation). New CLI: `paper embed`, `paper search`. New API: `/api/papers/semantic` with token gate. Frontend: DaisyUI token modal + IndexedDB persistence.

**Tech Stack:** Python 3.14, LanceDB, pyarrow, tiktoken, httpx, click, Starlette (existing), DaisyUI (existing frontend)

**Spec:** `docs/superpowers/specs/2026-04-11-embedding-rerank-hybrid-search-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/config.py` | Modify | Extend `ModelCapability` with `is_support_embedding`/`is_support_rerank`; add `EmbeddingConfig`, `RerankConfig`, `SearchConfig`; parse `[embedding]`, `[rerank]`, `[search]` from TOML |
| `python/deepresearch_flow/paper/embedding.py` | Create | `call_embedding()` — POST `/v1/embeddings`, batch support |
| `python/deepresearch_flow/paper/chunker.py` | Create | `SearchableField`, template adapters, `chunk_document()`, sliding window splitting |
| `python/deepresearch_flow/paper/embed_source.py` | Create | `load_from_json()`, `load_from_snapshot()` — unified data source abstraction producing `EmbedDocument` records |
| `python/deepresearch_flow/paper/vector_store.py` | Create | LanceDB table creation, `index_meta.json` validation, incremental group-level update, `_group_meta` tracking |
| `python/deepresearch_flow/paper/reranker.py` | Create | `RerankProvider` Protocol, `OpenAICompatibleReranker`, `RerankResult` |
| `python/deepresearch_flow/paper/search.py` | Create | `hybrid_search()` — embed query, vector retrieve, keyword retrieve, RRF merge, doc_id aggregation, rerank |
| `python/deepresearch_flow/paper/cli.py` | Modify | Add `paper embed` and `paper search` commands |
| `python/deepresearch_flow/paper/snapshot/builder.py` | Modify | Add `--output-embed-db` option to snapshot build |
| `python/deepresearch_flow/paper/embed_pipeline.py` | Create | Orchestration: load source → chunk → embed → incremental write to LanceDB |
| `python/deepresearch_flow/paper/db.py` | Modify | Add `--embed-db` to `paper db serve` command |
| `python/deepresearch_flow/paper/web/app.py` | Modify | Accept `embed_db` param, load LanceDB, wire semantic route |
| `python/deepresearch_flow/paper/web/handlers/api.py` | Modify | Add `api_papers_semantic` handler with token validation |
| `python/deepresearch_flow/paper/tests/test_chunker.py` | Create | Chunker unit tests |
| `python/deepresearch_flow/paper/tests/test_embedding.py` | Create | Embedding call tests |
| `python/deepresearch_flow/paper/tests/test_vector_store.py` | Create | LanceDB store tests |
| `python/deepresearch_flow/paper/tests/test_reranker.py` | Create | Reranker tests |
| `python/deepresearch_flow/paper/tests/test_search.py` | Create | Hybrid search pipeline tests |
| `python/deepresearch_flow/paper/tests/test_embed_source.py` | Create | Data source loading tests |
| `python/deepresearch_flow/paper/tests/test_embed_cli.py` | Create | `paper embed` CLI integration tests |
| `python/deepresearch_flow/paper/tests/test_embed_pipeline.py` | Create | End-to-end embed pipeline tests |
| `python/deepresearch_flow/paper/tests/test_semantic_api.py` | Create | `/api/papers/semantic` endpoint tests |
| `pyproject.toml` | Modify | Add `lancedb`, `pyarrow`, `tiktoken` dependencies |

---

### Task 1: Config Extension — `ModelCapability`, `EmbeddingConfig`, `RerankConfig`, `SearchConfig`

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py`
- Create: `python/deepresearch_flow/paper/tests/test_embedding_config.py`

- [ ] **Step 1: Write failing config tests**

Create `python/deepresearch_flow/paper/tests/test_embedding_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _full_config(tmp_path: Path, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [embedding]
        model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 32
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64
        provider = "ollama"

        [rerank]
        enabled = true
        model = "BAAI/bge-reranker-v2-m3"
        top_n = 10
        provider = "siliconflow"

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true, is_support_embedding = false, is_support_rerank = false }
        ]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [
          { model_name = "bge-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_embedding = true, is_support_rerank = false }
        ]

        [[providers]]
        name = "siliconflow"
        type = "openai_compatible"
        base = [{ url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "test-sf-key", weight = 1 }] }]
        models = [
          { model_name = "BAAI/bge-reranker-v2-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_embedding = false, is_support_rerank = true }
        ]
        """
        + extra,
        encoding="utf-8",
    )
    return config_path


def test_loads_embedding_config(tmp_path: Path) -> None:
    config = load_config(str(_full_config(tmp_path)))
    assert config.embedding is not None
    assert config.embedding.model == "bge-m3"
    assert config.embedding.dimensions == 1024
    assert config.embedding.normalized is True
    assert config.embedding.batch_size == 32
    assert config.embedding.chunk_max_tokens == 512
    assert config.embedding.chunk_overlap_tokens == 64
    assert config.embedding.provider == "ollama"


def test_loads_rerank_config(tmp_path: Path) -> None:
    config = load_config(str(_full_config(tmp_path)))
    assert config.rerank is not None
    assert config.rerank.enabled is True
    assert config.rerank.model == "BAAI/bge-reranker-v2-m3"
    assert config.rerank.top_n == 10
    assert config.rerank.provider == "siliconflow"


def test_loads_search_config(tmp_path: Path) -> None:
    config = load_config(str(_full_config(tmp_path)))
    assert config.search is not None
    assert config.search.vector_dir == "paper_vectors"
    assert config.search.vector_top_k == 50
    assert config.search.keyword_top_k == 30
    assert config.search.hybrid is True
    assert config.search.access_token is None


def test_search_config_with_access_token(tmp_path: Path) -> None:
    config = load_config(
        str(_full_config(tmp_path, '\naccess_token = "my-secret-token"'))
    )
    assert config.search.access_token == "my-secret-token"


def test_model_capability_embedding_flag(tmp_path: Path) -> None:
    config = load_config(str(_full_config(tmp_path)))
    ollama = next(p for p in config.providers if p.name == "ollama")
    bge = next(m for m in ollama.models if m.model_name == "bge-m3")
    assert bge.is_support_embedding is True
    assert bge.is_support_rerank is False


def test_model_capability_rerank_flag(tmp_path: Path) -> None:
    config = load_config(str(_full_config(tmp_path)))
    sf = next(p for p in config.providers if p.name == "siliconflow")
    reranker = next(m for m in sf.models if m.model_name == "BAAI/bge-reranker-v2-m3")
    assert reranker.is_support_rerank is True
    assert reranker.is_support_embedding is False


def test_embedding_rerank_search_sections_are_optional(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    assert config.embedding is None
    assert config.rerank is None
    assert config.search is None


def test_model_capability_defaults_embedding_rerank_false(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    model = config.providers[0].models[0]
    assert model.is_support_embedding is False
    assert model.is_support_rerank is False
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/tests/test_embedding_config.py -v`

Expected: FAIL — `EmbeddingConfig` etc. do not exist yet.

- [ ] **Step 3: Extend `ModelCapability` with two new fields**

In `python/deepresearch_flow/paper/config.py`, add to `ModelCapability`:

```python
@dataclass(frozen=True)
class ModelCapability:
    model_name: str
    is_stream: bool
    is_support_json_schema: bool
    is_support_json_object: bool
    is_support_embedding: bool = False
    is_support_rerank: bool = False
```

Update `_parse_model_capabilities` to parse the new fields with `_as_bool(..., False)` defaults.

- [ ] **Step 4: Add new config dataclasses**

In `python/deepresearch_flow/paper/config.py`:

```python
@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    dimensions: int
    normalized: bool
    batch_size: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    provider: str


@dataclass(frozen=True)
class RerankConfig:
    enabled: bool
    model: str
    top_n: int
    provider: str


@dataclass(frozen=True)
class SearchConfig:
    vector_dir: str
    vector_top_k: int
    keyword_top_k: int
    hybrid: bool
    access_token: str | None = None
```

Extend `PaperConfig`:

```python
@dataclass(frozen=True)
class PaperConfig:
    extract: ExtractConfig
    render: RenderConfig
    providers: list[ProviderConfig]
    main_model: list[MainModelConfig]
    embedding: EmbeddingConfig | None = None
    rerank: RerankConfig | None = None
    search: SearchConfig | None = None
```

- [ ] **Step 5: Add parsing logic in `load_config`**

In `load_config`, after parsing `main_model`, add:

```python
    embedding_data = data.get("embedding")
    embedding = None
    if embedding_data is not None:
        embedding = EmbeddingConfig(
            model=str(embedding_data["model"]),
            dimensions=int(embedding_data["dimensions"]),
            normalized=_as_bool(embedding_data.get("normalized"), True),
            batch_size=_as_int(embedding_data.get("batch_size"), 32),
            chunk_max_tokens=_as_int(embedding_data.get("chunk_max_tokens"), 512),
            chunk_overlap_tokens=_as_int(embedding_data.get("chunk_overlap_tokens"), 64),
            provider=str(embedding_data["provider"]),
        )

    rerank_data = data.get("rerank")
    rerank = None
    if rerank_data is not None:
        rerank = RerankConfig(
            enabled=_as_bool(rerank_data.get("enabled"), True),
            model=str(rerank_data["model"]),
            top_n=_as_int(rerank_data.get("top_n"), 10),
            provider=str(rerank_data["provider"]),
        )

    search_data = data.get("search")
    search = None
    if search_data is not None:
        search = SearchConfig(
            vector_dir=_as_str(search_data.get("vector_dir"), "paper_vectors") or "paper_vectors",
            vector_top_k=_as_int(search_data.get("vector_top_k"), 50),
            keyword_top_k=_as_int(search_data.get("keyword_top_k"), 30),
            hybrid=_as_bool(search_data.get("hybrid"), True),
            access_token=_as_str(search_data.get("access_token"), None),
        )
```

Pass all three to the `PaperConfig` constructor.

- [ ] **Step 6: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding_config.py -v`

Expected: PASS

- [ ] **Step 7: Run existing tests to verify no regressions**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_config.py python/deepresearch_flow/paper/tests/test_weighted_routing.py -v`

Expected: PASS — existing configs without `[embedding]`/`[rerank]`/`[search]` still load fine with None defaults.

- [ ] **Step 8: Commit**

```bash
git add python/deepresearch_flow/paper/config.py python/deepresearch_flow/paper/tests/test_embedding_config.py
git commit -m "feat: add embedding, rerank, search config sections and ModelCapability extension"
```

---

### Task 2: Chunker — Template Adapters and Splitting

**Files:**
- Create: `python/deepresearch_flow/paper/chunker.py`
- Create: `python/deepresearch_flow/paper/tests/test_chunker.py`

- [ ] **Step 1: Write failing chunker tests**

Create `python/deepresearch_flow/paper/tests/test_chunker.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.chunker import (
    SearchableField,
    chunk_fields,
    extract_searchable_fields,
)

_SIMPLE_RECORD = {
    "paper_title": "Attention Is All You Need",
    "summary": "This paper introduces the Transformer architecture.",
    "keywords": ["transformer", "attention"],
    "paper_authors": ["Vaswani", "Shazeer"],
}

_SIMPLE_RECORD_ALT_KEY = {
    "title": "Attention Is All You Need",
    "summary": "This paper introduces the Transformer architecture.",
}


def test_extract_simple_template_fields() -> None:
    fields = extract_searchable_fields(_SIMPLE_RECORD, "simple")
    titles = [f for f in fields if f.chunk_type == "title"]
    assert len(titles) == 1
    assert titles[0].text == "Attention Is All You Need"
    assert titles[0].template_tag == ""  # shared

    abstracts = [f for f in fields if f.chunk_type == "abstract"]
    assert len(abstracts) == 1
    assert abstracts[0].template_tag == "simple"


def test_extract_simple_template_accepts_title_key() -> None:
    fields = extract_searchable_fields(_SIMPLE_RECORD_ALT_KEY, "simple")
    titles = [f for f in fields if f.chunk_type == "title"]
    assert len(titles) == 1
    assert titles[0].text == "Attention Is All You Need"


def test_extract_fallback_adapter_scans_strings() -> None:
    record = {"title": "Test", "custom_field": "Some text content", "number_field": 42}
    fields = extract_searchable_fields(record, "unknown_template")
    types = {f.chunk_type for f in fields}
    assert "title" in types
    assert "content" in types


def test_extract_skips_missing_fields() -> None:
    record = {"title": "Test"}  # no summary, no qa
    fields = extract_searchable_fields(record, "simple")
    types = {f.chunk_type for f in fields}
    assert "title" in types
    assert "qa" not in types
    assert "abstract" not in types


def test_chunk_fields_no_split_short_text() -> None:
    field = SearchableField(
        field_name="title",
        chunk_type="title",
        text="Short title",
        template_tag="",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=512, overlap_tokens=64)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == "Short title"


def test_chunk_fields_splits_long_text() -> None:
    long_text = "word " * 1000  # ~1000 tokens, well above 100
    field = SearchableField(
        field_name="deep_read/findings",
        chunk_type="content",
        text=long_text.strip(),
        template_tag="deep_read",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert chunk.field_name == "deep_read/findings"
        assert chunk.template_tag == "deep_read"


def test_chunk_fields_qa_not_split() -> None:
    long_qa = "Q: " + "question " * 200 + "\nA: " + "answer " * 200
    field = SearchableField(
        field_name="simple/qa[0]",
        chunk_type="qa",
        text=long_qa,
        template_tag="simple",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1  # qa chunks are never split


def test_source_md_field_tagged_shared() -> None:
    field = SearchableField(
        field_name="source_md",
        chunk_type="source_md",
        text="# Introduction\nThis paper...",
        template_tag="",
        lang="",
    )
    chunks = chunk_fields([field], max_tokens=512, overlap_tokens=64)
    assert all(c.template_tag == "" for c in chunks)


def test_translated_md_carries_lang() -> None:
    field = SearchableField(
        field_name="translated_md",
        chunk_type="translated_md",
        text="# 介绍\n本文...",
        template_tag="",
        lang="zh",
    )
    chunks = chunk_fields([field], max_tokens=512, overlap_tokens=64)
    assert all(c.lang == "zh" for c in chunks)
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_chunker.py -v`

Expected: FAIL — `chunker` module does not exist.

- [ ] **Step 3: Implement chunker**

Create `python/deepresearch_flow/paper/chunker.py`:

```python
"""Document chunking with template adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tiktoken


@dataclass(frozen=True)
class SearchableField:
    field_name: str
    chunk_type: str  # title / abstract / content / qa / source_md / translated_md
    text: str
    template_tag: str  # "" for shared (title, source_md, translated_md)
    lang: str  # language code for translated_md, "" otherwise


@dataclass(frozen=True)
class Chunk:
    field_name: str
    chunk_type: str
    chunk_index: int
    text: str
    template_tag: str
    lang: str


_SHARED = ""
_NO_SPLIT_TYPES = {"title", "qa"}
_ENC = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_ENC.encode(text, disallowed_special=()))


def _sliding_window_split(
    text: str, *, max_tokens: int, overlap_tokens: int
) -> list[str]:
    tokens = _ENC.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return [text]
    step = max(max_tokens - overlap_tokens, 1)
    segments: list[str] = []
    for start in range(0, len(tokens), step):
        segment_tokens = tokens[start : start + max_tokens]
        segments.append(_ENC.decode(segment_tokens))
        if start + max_tokens >= len(tokens):
            break
    return segments


def chunk_fields(
    fields: list[SearchableField],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for field in fields:
        if not field.text or not field.text.strip():
            continue
        if field.chunk_type in _NO_SPLIT_TYPES:
            chunks.append(
                Chunk(
                    field_name=field.field_name,
                    chunk_type=field.chunk_type,
                    chunk_index=0,
                    text=field.text,
                    template_tag=field.template_tag,
                    lang=field.lang,
                )
            )
        else:
            segments = _sliding_window_split(
                field.text, max_tokens=max_tokens, overlap_tokens=overlap_tokens
            )
            for idx, segment in enumerate(segments):
                chunks.append(
                    Chunk(
                        field_name=field.field_name,
                        chunk_type=field.chunk_type,
                        chunk_index=idx,
                        text=segment,
                        template_tag=field.template_tag,
                        lang=field.lang,
                    )
                )
    return chunks


def _resolve_title(record: dict[str, Any]) -> str | None:
    for key in ("paper_title", "title"):
        value = record.get(key)
        if value and isinstance(value, str):
            return value
    return None


def _extract_simple(record: dict[str, Any], tag: str) -> list[SearchableField]:
    fields: list[SearchableField] = []
    title = _resolve_title(record)
    if title:
        fields.append(SearchableField("title", "title", title, _SHARED, ""))
    for key in ("summary", "abstract"):
        value = record.get(key)
        if value and isinstance(value, str):
            fields.append(SearchableField(f"{tag}/{key}", "abstract", value, tag, ""))
            break
    text_keys = [
        k
        for k in record
        if k not in {"paper_title", "title", "summary", "abstract", "keywords", "paper_authors",
                      "source_path", "source_hash", "template_tag", "prompt_template",
                      "extracted_at", "provider", "model", "output_language",
                      "paper_institutions", "ai_generated_tags", "_tags", "_authors",
                      "_keywords", "_venue", "doi", "bibtex"}
        and isinstance(record[k], str)
        and len(record[k]) > 20
    ]
    for k in text_keys:
        fields.append(SearchableField(f"{tag}/{k}", "content", record[k], tag, ""))
    qa_items = record.get("qa") or record.get("qa_pairs") or []
    if isinstance(qa_items, list):
        for i, item in enumerate(qa_items):
            if isinstance(item, dict):
                q = item.get("question", item.get("q", ""))
                a = item.get("answer", item.get("a", ""))
                if q or a:
                    fields.append(
                        SearchableField(f"{tag}/qa[{i}]", "qa", f"Q: {q}\nA: {a}", tag, "")
                    )
    return fields


def _extract_fallback(record: dict[str, Any], tag: str) -> list[SearchableField]:
    fields: list[SearchableField] = []
    title = _resolve_title(record)
    if title:
        fields.append(SearchableField("title", "title", title, _SHARED, ""))
    for k, v in record.items():
        if k in ("paper_title", "title"):
            continue
        if isinstance(v, str) and len(v) > 20:
            fields.append(SearchableField(f"{tag}/{k}", "content", v, tag, ""))
    return fields


_ADAPTERS: dict[str, Any] = {
    "simple": _extract_simple,
    "simple_phi": _extract_simple,
}


def extract_searchable_fields(
    record: dict[str, Any], template_tag: str
) -> list[SearchableField]:
    adapter = _ADAPTERS.get(template_tag, _extract_fallback)
    return adapter(record, template_tag)
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_chunker.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/chunker.py python/deepresearch_flow/paper/tests/test_chunker.py
git commit -m "feat: add document chunker with template adapters and sliding window splitting"
```

---

### Task 3: Embedding API Client

**Files:**
- Create: `python/deepresearch_flow/paper/embedding.py`
- Create: `python/deepresearch_flow/paper/tests/test_embedding.py`

- [ ] **Step 1: Write failing embedding tests**

Create `python/deepresearch_flow/paper/tests/test_embedding.py`:

```python
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from deepresearch_flow.paper.embedding import EmbeddingResult, call_embedding


def _mock_transport(response_data: dict) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "bge-m3"
        assert isinstance(body["input"], list)
        n = len(body["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1] * 1024, "index": i} for i in range(n)
                ],
                "usage": {"prompt_tokens": n * 10},
            },
        )

    return httpx.MockTransport(handler)


def test_call_embedding_returns_vectors() -> None:
    async def _run() -> EmbeddingResult:
        transport = _mock_transport({})
        async with httpx.AsyncClient(transport=transport) as client:
            return await call_embedding(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                model="bge-m3",
                texts=["hello", "world"],
                dimensions=1024,
                client=client,
            )

    result = asyncio.run(_run())
    assert len(result.vectors) == 2
    assert len(result.vectors[0]) == 1024
    assert result.model == "bge-m3"
    assert result.usage_tokens == 20


def test_call_embedding_empty_input_raises() -> None:
    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            await call_embedding(
                base_url="http://localhost/v1",
                api_key="k",
                model="m",
                texts=[],
                client=client,
            )

    with pytest.raises(ValueError, match="empty"):
        asyncio.run(_run())
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding.py -v`

Expected: FAIL

- [ ] **Step 3: Implement embedding client**

Create `python/deepresearch_flow/paper/embedding.py`:

```python
"""OpenAI-compatible embedding API client."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    usage_tokens: int


async def call_embedding(
    base_url: str,
    api_key: str,
    model: str,
    texts: list[str],
    *,
    dimensions: int | None = None,
    client: httpx.AsyncClient,
) -> EmbeddingResult:
    if not texts:
        raise ValueError("Embedding input must not be empty")
    url = base_url.rstrip("/") + "/embeddings"
    body: dict = {"model": model, "input": texts}
    if dimensions is not None:
        body["dimensions"] = dimensions
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = await client.post(url, json=body, headers=headers, timeout=120.0)
    response.raise_for_status()
    data = response.json()
    sorted_data = sorted(data["data"], key=lambda x: x["index"])
    vectors = [item["embedding"] for item in sorted_data]
    usage = data.get("usage", {})
    return EmbeddingResult(
        vectors=vectors,
        model=model,
        usage_tokens=usage.get("prompt_tokens", 0),
    )
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/embedding.py python/deepresearch_flow/paper/tests/test_embedding.py
git commit -m "feat: add OpenAI-compatible embedding API client"
```

---

### Task 4: Reranker — Protocol and OpenAI-Compatible Implementation

**Files:**
- Create: `python/deepresearch_flow/paper/reranker.py`
- Create: `python/deepresearch_flow/paper/tests/test_reranker.py`

- [ ] **Step 1: Write failing reranker tests**

Create `python/deepresearch_flow/paper/tests/test_reranker.py`:

```python
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from deepresearch_flow.paper.reranker import (
    OpenAICompatibleReranker,
    RerankResult,
)


def _mock_rerank_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "model" in body
        assert "query" in body
        assert "documents" in body
        results = [
            {"index": i, "relevance_score": 1.0 - i * 0.1}
            for i in range(min(body.get("top_n", len(body["documents"])), len(body["documents"])))
        ]
        return httpx.Response(200, json={"id": "test", "results": results})

    return httpx.MockTransport(handler)


def test_rerank_returns_sorted_indices() -> None:
    async def _run() -> RerankResult:
        reranker = OpenAICompatibleReranker(
            base_url="http://localhost/v1",
            api_key="key",
            model="test-reranker",
        )
        transport = _mock_rerank_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            return await reranker.rerank(
                query="test query",
                documents=["doc a", "doc b", "doc c"],
                top_n=2,
                client=client,
            )

    result = asyncio.run(_run())
    assert len(result.indices) == 2
    assert len(result.scores) == 2
    assert result.scores[0] >= result.scores[1]


def test_rerank_empty_documents_raises() -> None:
    async def _run() -> None:
        reranker = OpenAICompatibleReranker(
            base_url="http://localhost/v1",
            api_key="key",
            model="test-reranker",
        )
        async with httpx.AsyncClient() as client:
            await reranker.rerank(query="q", documents=[], top_n=5, client=client)

    with pytest.raises(ValueError, match="empty"):
        asyncio.run(_run())
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_reranker.py -v`

Expected: FAIL

- [ ] **Step 3: Implement reranker**

Create `python/deepresearch_flow/paper/reranker.py`:

```python
"""Rerank provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class RerankResult:
    indices: list[int]
    scores: list[float]


class RerankProvider(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        client: httpx.AsyncClient,
    ) -> RerankResult: ...


class OpenAICompatibleReranker:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
        client: httpx.AsyncClient,
    ) -> RerankResult:
        if not documents:
            raise ValueError("Rerank documents must not be empty")
        url = self._base_url.rstrip("/") + "/rerank"
        body = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = await client.post(url, json=body, headers=headers, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        results = sorted(data["results"], key=lambda r: r["relevance_score"], reverse=True)
        return RerankResult(
            indices=[r["index"] for r in results],
            scores=[r["relevance_score"] for r in results],
        )
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_reranker.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/reranker.py python/deepresearch_flow/paper/tests/test_reranker.py
git commit -m "feat: add rerank provider protocol and OpenAI-compatible implementation"
```

---

### Task 5: Vector Store — LanceDB, Index Meta, Incremental Update

**Files:**
- Create: `python/deepresearch_flow/paper/vector_store.py`
- Create: `python/deepresearch_flow/paper/tests/test_vector_store.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Add to `[project]` dependencies:

```toml
    "lancedb>=0.20.0",
    "pyarrow>=18.0.0",
    "tiktoken>=0.9.0",
```

Run: `uv sync`

- [ ] **Step 2: Write failing vector store tests**

Create `python/deepresearch_flow/paper/tests/test_vector_store.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepresearch_flow.paper.chunker import Chunk
from deepresearch_flow.paper.vector_store import (
    INDEX_VERSION,
    ChunkRow,
    build_chunk_id,
    load_index_meta,
    open_store,
    save_index_meta,
    validate_index_meta,
    write_chunks,
    read_group_hashes,
    delete_groups,
    query_vector,
)


def _make_chunk(
    doc_id: str = "doc1",
    template_tag: str = "simple",
    chunk_type: str = "abstract",
    chunk_index: int = 0,
    text: str = "test text",
    lang: str = "",
) -> Chunk:
    return Chunk(
        field_name=f"{template_tag}/summary" if template_tag else "title",
        chunk_type=chunk_type,
        chunk_index=chunk_index,
        text=text,
        template_tag=template_tag,
        lang=lang,
    )


def test_build_chunk_id_template_scoped() -> None:
    cid = build_chunk_id("abc123", "simple", "abstract", 0)
    assert cid == "abc123_simple_abstract_0"


def test_build_chunk_id_shared() -> None:
    cid = build_chunk_id("abc123", "", "title", 0)
    assert cid == "abc123__shared_title_0"


def test_build_chunk_id_translated_md() -> None:
    cid = build_chunk_id("abc123", "", "translated_md_zh", 2)
    assert cid == "abc123__shared_translated_md_zh_2"


def test_index_meta_roundtrip(tmp_path: Path) -> None:
    meta = {
        "model": "bge-m3",
        "dimensions": 1024,
        "normalized": True,
        "provider": "ollama",
        "index_version": INDEX_VERSION,
    }
    save_index_meta(tmp_path, meta)
    loaded = load_index_meta(tmp_path)
    assert loaded == meta


def test_validate_index_meta_mismatch_fails(tmp_path: Path) -> None:
    save_index_meta(tmp_path, {
        "model": "bge-m3",
        "dimensions": 1024,
        "normalized": True,
        "provider": "ollama",
        "index_version": INDEX_VERSION,
    })
    with pytest.raises(ValueError, match="model"):
        validate_index_meta(tmp_path, model="different-model", dimensions=1024, normalized=True)


def test_validate_index_meta_missing_creates(tmp_path: Path) -> None:
    validate_index_meta(tmp_path, model="bge-m3", dimensions=1024, normalized=True, provider="ollama")
    meta = load_index_meta(tmp_path)
    assert meta["model"] == "bge-m3"


def test_write_and_query_chunks(tmp_path: Path) -> None:
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
            text="Attention Is All You Need",
            content_hash="abc",
            vector=[0.1] * 1024,
            title="Attention Is All You Need",
            year=2017,
            authors="Vaswani",
            venue="NeurIPS",
            tags="transformer",
        ),
    ]
    write_chunks(db, rows)
    results = query_vector(db, [0.1] * 1024, top_k=5)
    assert len(results) >= 1
    assert results[0]["doc_id"] == "doc1"
```

- [ ] **Step 3: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store.py -v`

Expected: FAIL

- [ ] **Step 4: Implement vector store**

Create `python/deepresearch_flow/paper/vector_store.py`:

```python
"""LanceDB vector store with index metadata validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

INDEX_VERSION = 1
_SHARED_KEY = "_shared"
_META_FILE = "index_meta.json"
_GROUP_META_TABLE = "_group_meta"
_CHUNKS_TABLE = "paper_chunks"


@dataclass
class ChunkRow:
    id: str
    doc_id: str
    source_path: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    field_name: str
    lang: str
    text: str
    content_hash: str
    vector: list[float]
    title: str
    year: int
    authors: str
    venue: str
    tags: str


def build_chunk_id(doc_id: str, template_tag: str, chunk_type: str, chunk_index: int) -> str:
    template_key = template_tag if template_tag else _SHARED_KEY
    return f"{doc_id}_{template_key}_{chunk_type}_{chunk_index}"


def compute_group_hash(content_hashes: list[str]) -> str:
    combined = "\n".join(sorted(content_hashes))
    return hashlib.sha256(combined.encode()).hexdigest()


def save_index_meta(vector_dir: Path, meta: dict[str, Any]) -> None:
    (vector_dir / _META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_index_meta(vector_dir: Path) -> dict[str, Any]:
    return json.loads((vector_dir / _META_FILE).read_text(encoding="utf-8"))


def validate_index_meta(
    vector_dir: Path,
    *,
    model: str,
    dimensions: int,
    normalized: bool,
    provider: str = "",
) -> None:
    meta_path = vector_dir / _META_FILE
    if not meta_path.exists():
        meta = {
            "model": model,
            "dimensions": dimensions,
            "normalized": normalized,
            "provider": provider,
            "index_version": INDEX_VERSION,
        }
        vector_dir.mkdir(parents=True, exist_ok=True)
        save_index_meta(vector_dir, meta)
        return
    meta = load_index_meta(vector_dir)
    if meta.get("model") != model:
        raise ValueError(
            f"Index model mismatch: index has '{meta.get('model')}', config has '{model}'. "
            "Use --force to rebuild."
        )
    if meta.get("dimensions") != dimensions:
        raise ValueError(
            f"Index dimensions mismatch: index has {meta.get('dimensions')}, config has {dimensions}. "
            "Use --force to rebuild."
        )
    if meta.get("normalized") != normalized:
        raise ValueError(
            f"Index normalized mismatch: index has {meta.get('normalized')}, config has {normalized}. "
            "Use --force to rebuild."
        )
    if meta.get("index_version", 0) != INDEX_VERSION:
        raise ValueError(
            f"Index version mismatch: index has {meta.get('index_version')}, current is {INDEX_VERSION}. "
            "Use --force to rebuild."
        )


def open_store(vector_dir: Path) -> lancedb.DBConnection:
    vector_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(vector_dir))


def _chunks_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("doc_id", pa.string()),
            pa.field("source_path", pa.string()),
            pa.field("template_tag", pa.string()),
            pa.field("chunk_type", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("field_name", pa.string()),
            pa.field("lang", pa.string()),
            pa.field("text", pa.string()),
            pa.field("content_hash", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 1024)),
            pa.field("title", pa.string()),
            pa.field("year", pa.int32()),
            pa.field("authors", pa.string()),
            pa.field("venue", pa.string()),
            pa.field("tags", pa.string()),
        ]
    )


def write_chunks(db: lancedb.DBConnection, rows: list[ChunkRow]) -> None:
    if not rows:
        return
    data = [
        {
            "id": r.id,
            "doc_id": r.doc_id,
            "source_path": r.source_path,
            "template_tag": r.template_tag,
            "chunk_type": r.chunk_type,
            "chunk_index": r.chunk_index,
            "field_name": r.field_name,
            "lang": r.lang,
            "text": r.text,
            "content_hash": r.content_hash,
            "vector": r.vector,
            "title": r.title,
            "year": r.year,
            "authors": r.authors,
            "venue": r.venue,
            "tags": r.tags,
        }
        for r in rows
    ]
    table_names = db.table_names()
    if _CHUNKS_TABLE in table_names:
        table = db.open_table(_CHUNKS_TABLE)
        table.add(data)
    else:
        db.create_table(_CHUNKS_TABLE, data, schema=_chunks_schema())


def delete_groups(db: lancedb.DBConnection, groups: list[tuple[str, str]]) -> None:
    if not groups or _CHUNKS_TABLE not in db.table_names():
        return
    table = db.open_table(_CHUNKS_TABLE)
    for doc_id, template_key in groups:
        tag = "" if template_key == _SHARED_KEY else template_key
        table.delete(f'doc_id = "{doc_id}" AND template_tag = "{tag}"')


def read_group_hashes(db: lancedb.DBConnection) -> dict[tuple[str, str], str]:
    if _CHUNKS_TABLE not in db.table_names():
        return {}
    table = db.open_table(_CHUNKS_TABLE)
    df = table.to_pandas()[["doc_id", "template_tag", "content_hash"]]
    groups: dict[tuple[str, str], list[str]] = {}
    for _, row in df.iterrows():
        key = (row["doc_id"], row["template_tag"] or _SHARED_KEY)
        groups.setdefault(key, []).append(row["content_hash"])
    return {k: compute_group_hash(v) for k, v in groups.items()}


def query_vector(
    db: lancedb.DBConnection,
    query_vector: list[float],
    top_k: int = 50,
    where: str | None = None,
) -> list[dict[str, Any]]:
    if _CHUNKS_TABLE not in db.table_names():
        return []
    table = db.open_table(_CHUNKS_TABLE)
    q = table.search(query_vector).metric("cosine").limit(top_k)
    if where:
        q = q.where(where)
    return q.to_list()
```

- [ ] **Step 5: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/vector_store.py python/deepresearch_flow/paper/tests/test_vector_store.py pyproject.toml
git commit -m "feat: add LanceDB vector store with index metadata validation and incremental update"
```

---

### Task 6: Data Source Abstraction — JSON and Snapshot Loading

**Files:**
- Create: `python/deepresearch_flow/paper/embed_source.py`
- Create: `python/deepresearch_flow/paper/tests/test_embed_source.py`

- [ ] **Step 1: Write failing data source tests**

Create `python/deepresearch_flow/paper/tests/test_embed_source.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepresearch_flow.paper.embed_source import (
    EmbedDocument,
    load_from_json,
    resolve_template_tag,
)


def test_load_single_json_with_explicit_template(tmp_path: Path) -> None:
    data = [
        {
            "title": "Test Paper",
            "summary": "A summary.",
            "source_path": "papers/test.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        }
    ]
    json_path = tmp_path / "paper_infos.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")

    docs = load_from_json([json_path])
    assert len(docs) == 1
    assert docs[0].doc_id  # non-empty
    assert docs[0].template_records["simple"][0]["title"] == "Test Paper"
    assert docs[0].metadata.title == "Test Paper"


def test_load_multiple_json_merges_by_doc_id(tmp_path: Path) -> None:
    paper = {
        "title": "Same Paper",
        "summary": "Summary A.",
        "source_path": "papers/same.md",
        "paper_authors": ["Author"],
    }
    json_a = tmp_path / "simple.json"
    json_a.write_text(json.dumps([{**paper, "prompt_template": "simple"}]), encoding="utf-8")
    json_b = tmp_path / "deep.json"
    json_b.write_text(json.dumps([{**paper, "prompt_template": "deep_read", "findings": "Found X."}]), encoding="utf-8")

    docs = load_from_json([json_a, json_b])
    assert len(docs) == 1  # same paper, merged
    assert "simple" in docs[0].template_records
    assert "deep_read" in docs[0].template_records


def test_resolve_template_tag_from_record() -> None:
    assert resolve_template_tag({"prompt_template": "simple"}, None) == "simple"
    assert resolve_template_tag({"template_tag": "deep_read"}, None) == "deep_read"


def test_resolve_template_tag_cli_override() -> None:
    assert resolve_template_tag({}, "custom") == "custom"


def test_resolve_template_tag_missing_raises() -> None:
    with pytest.raises(ValueError, match="template"):
        resolve_template_tag({}, None)
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_source.py -v`

Expected: FAIL

- [ ] **Step 3: Implement data source abstraction**

Create `python/deepresearch_flow/paper/embed_source.py`:

```python
"""Unified data source loading for paper embed."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepresearch_flow.paper.snapshot.identity import (
    build_paper_key_candidates,
    choose_preferred_key,
    paper_id_for_key,
)


@dataclass(frozen=True)
class DocumentMetadata:
    title: str
    year: int
    authors: str
    venue: str
    tags: str
    source_path: str


@dataclass
class EmbedDocument:
    doc_id: str
    metadata: DocumentMetadata
    template_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    source_md: str | None = None
    translations: dict[str, str] = field(default_factory=dict)  # lang -> text


def resolve_template_tag(record: dict[str, Any], cli_override: str | None) -> str:
    if cli_override:
        return cli_override
    for key in ("template_tag", "prompt_template"):
        value = record.get(key)
        if value and isinstance(value, str):
            return value
    raise ValueError(
        "Cannot determine template tag for record. "
        "Provide --template-tag or include 'template_tag'/'prompt_template' in JSON."
    )


def _resolve_doc_id(record: dict[str, Any]) -> str:
    candidates = build_paper_key_candidates(record)
    if candidates:
        preferred = choose_preferred_key(candidates)
        return paper_id_for_key(preferred.paper_key)
    source_path = record.get("source_path", "")
    if source_path:
        import hashlib
        return hashlib.sha256(source_path.encode()).hexdigest()[:32]
    raise ValueError("Cannot resolve document identity: no DOI, BibTeX key, metadata, or source_path")


def _extract_metadata(record: dict[str, Any]) -> DocumentMetadata:
    authors_raw = record.get("paper_authors") or record.get("_authors") or []
    if isinstance(authors_raw, list):
        authors = ", ".join(str(a) for a in authors_raw)
    else:
        authors = str(authors_raw)
    tags_raw = record.get("ai_generated_tags") or record.get("_tags") or []
    if isinstance(tags_raw, list):
        tags = ", ".join(str(t) for t in tags_raw)
    else:
        tags = str(tags_raw)
    year_raw = record.get("year") or record.get("publication_date", "")
    try:
        year = int(str(year_raw)[:4])
    except (ValueError, TypeError):
        year = 0
    title = record.get("paper_title") or record.get("title") or ""
    return DocumentMetadata(
        title=str(title),
        year=year,
        authors=authors,
        venue=str(record.get("_venue") or record.get("publication_venue") or ""),
        tags=tags,
        source_path=str(record.get("source_path", "")),
    )


def _match_source_md(
    record: dict[str, Any], md_roots: list[Path]
) -> str | None:
    source_path = record.get("source_path", "")
    source_hash = record.get("source_hash", "")
    for root in md_roots:
        if source_path:
            candidate = root / Path(source_path).name
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        if source_hash:
            for md_file in root.glob("*.md"):
                if source_hash in md_file.stem:
                    return md_file.read_text(encoding="utf-8")
    return None


def _match_translations(
    record: dict[str, Any], md_translated_roots: list[Path]
) -> dict[str, str]:
    source_hash = record.get("source_hash", "")
    translations: dict[str, str] = {}
    if not source_hash:
        return translations
    for root in md_translated_roots:
        if not root.is_dir():
            continue
        for lang_dir in root.iterdir():
            if not lang_dir.is_dir():
                continue
            lang = lang_dir.name
            for md_file in lang_dir.glob("*.md"):
                if source_hash in md_file.stem:
                    translations[lang] = md_file.read_text(encoding="utf-8")
                    break
    return translations


def load_from_json(
    paths: list[Path],
    *,
    template_tag_override: str | None = None,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
) -> list[EmbedDocument]:
    docs_by_id: dict[str, EmbedDocument] = {}
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = raw if isinstance(raw, list) else raw.get("papers", [raw])
        for record in records:
            if not isinstance(record, dict):
                continue
            tag = resolve_template_tag(record, template_tag_override)
            doc_id = _resolve_doc_id(record)
            if doc_id not in docs_by_id:
                doc = EmbedDocument(
                    doc_id=doc_id,
                    metadata=_extract_metadata(record),
                )
                if md_roots:
                    doc.source_md = _match_source_md(record, md_roots)
                if md_translated_roots:
                    doc.translations = _match_translations(record, md_translated_roots)
                docs_by_id[doc_id] = doc
            docs_by_id[doc_id].template_records.setdefault(tag, []).append(record)
    return list(docs_by_id.values())


def load_from_snapshot(
    snapshot_db: Path,
    static_export_dir: Path,
) -> list[EmbedDocument]:
    import sqlite3

    conn = sqlite3.connect(str(snapshot_db))
    conn.row_factory = sqlite3.Row
    papers = conn.execute(
        "SELECT paper_id, title, year, venue, source_md_content_hash FROM paper ORDER BY paper_index"
    ).fetchall()
    docs: list[EmbedDocument] = []
    for row in papers:
        paper_id = row["paper_id"]
        authors_rows = conn.execute(
            "SELECT a.value FROM author a JOIN paper_author pa ON a.author_id = pa.author_id WHERE pa.paper_id = ?",
            (paper_id,),
        ).fetchall()
        tags_rows = conn.execute(
            "SELECT t.value FROM tag t JOIN paper_tag pt ON t.tag_id = pt.tag_id WHERE pt.paper_id = ?",
            (paper_id,),
        ).fetchall()
        templates = conn.execute(
            "SELECT template_tag FROM paper_summary WHERE paper_id = ?", (paper_id,)
        ).fetchall()
        translations = conn.execute(
            "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?", (paper_id,)
        ).fetchall()
        meta = DocumentMetadata(
            title=row["title"],
            year=int(str(row["year"])[:4]) if row["year"] else 0,
            authors=", ".join(r["value"] for r in authors_rows),
            venue=row["venue"] or "",
            tags=", ".join(r["value"] for r in tags_rows),
            source_path="",
        )
        doc = EmbedDocument(doc_id=paper_id, metadata=meta)
        for tmpl_row in templates:
            tag = tmpl_row["template_tag"]
            summary_path = static_export_dir / "summary" / paper_id / f"{tag}.json"
            if summary_path.exists():
                summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(summary_data, dict):
                    summary_data["title"] = meta.title
                    doc.template_records.setdefault(tag, []).append(summary_data)
        source_hash = row["source_md_content_hash"]
        if source_hash:
            md_path = static_export_dir / "md" / f"{source_hash}.md"
            if md_path.exists():
                doc.source_md = md_path.read_text(encoding="utf-8")
        for trans_row in translations:
            lang = trans_row["lang"]
            md_hash = trans_row["md_content_hash"]
            trans_path = static_export_dir / "md_translate" / lang / f"{md_hash}.md"
            if trans_path.exists():
                doc.translations[lang] = trans_path.read_text(encoding="utf-8")
        docs.append(doc)
    conn.close()
    return docs
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_source.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/embed_source.py python/deepresearch_flow/paper/tests/test_embed_source.py
git commit -m "feat: add unified data source loading for paper embed (JSON + snapshot)"
```

---

### Task 7: Search Pipeline — Hybrid Search, RRF, Aggregation

**Files:**
- Create: `python/deepresearch_flow/paper/search.py`
- Create: `python/deepresearch_flow/paper/tests/test_search.py`

- [ ] **Step 1: Write failing search tests**

Create `python/deepresearch_flow/paper/tests/test_search.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.search import (
    SearchHit,
    SearchResult,
    aggregate_by_doc_id,
    reciprocal_rank_fusion,
)


def test_rrf_single_list() -> None:
    ranked = ["doc_a", "doc_b", "doc_c"]
    scores = reciprocal_rank_fusion([ranked], k=60)
    assert scores["doc_a"] > scores["doc_b"] > scores["doc_c"]


def test_rrf_two_lists_overlap() -> None:
    vector_ranked = ["doc_a", "doc_b", "doc_c"]
    keyword_ranked = ["doc_b", "doc_d", "doc_a"]
    scores = reciprocal_rank_fusion([vector_ranked, keyword_ranked], k=60)
    # doc_a and doc_b appear in both lists, should score higher
    assert scores["doc_a"] > scores["doc_c"]
    assert scores["doc_b"] > scores["doc_d"]


def test_rrf_empty_lists() -> None:
    scores = reciprocal_rank_fusion([], k=60)
    assert scores == {}


def test_aggregate_by_doc_id_picks_best_chunk() -> None:
    hits = [
        SearchHit(doc_id="doc1", chunk_text="chunk A", score=0.9, field_name="simple/summary", template_tag="simple", chunk_type="abstract", lang=""),
        SearchHit(doc_id="doc1", chunk_text="chunk B", score=0.7, field_name="deep_read/findings", template_tag="deep_read", chunk_type="content", lang=""),
        SearchHit(doc_id="doc2", chunk_text="chunk C", score=0.8, field_name="title", template_tag="", chunk_type="title", lang=""),
    ]
    aggregated = aggregate_by_doc_id(hits)
    assert len(aggregated) == 2
    doc1 = next(h for h in aggregated if h.doc_id == "doc1")
    assert doc1.chunk_text == "chunk A"  # highest score
    assert doc1.score == 0.9


def test_aggregate_translated_md_preserves_lang() -> None:
    hits = [
        SearchHit(doc_id="doc1", chunk_text="中文摘要", score=0.95, field_name="translated_md", template_tag="", chunk_type="translated_md", lang="zh"),
    ]
    aggregated = aggregate_by_doc_id(hits)
    assert aggregated[0].lang == "zh"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_search.py -v`

Expected: FAIL

- [ ] **Step 3: Implement search pipeline**

Create `python/deepresearch_flow/paper/search.py`:

```python
"""Hybrid search pipeline: vector + keyword + RRF + rerank."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from deepresearch_flow.paper.reranker import RerankProvider, RerankResult


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    chunk_text: str
    score: float
    field_name: str
    template_tag: str
    chunk_type: str
    lang: str


@dataclass(frozen=True)
class SearchResult:
    doc_id: str
    score: float
    score_type: str  # "rerank", "rrf", or "cosine"
    matched_chunk: str
    matched_field: str
    matched_template: str
    matched_chunk_type: str
    matched_lang: str


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def aggregate_by_doc_id(hits: list[SearchHit]) -> list[SearchHit]:
    best: dict[str, SearchHit] = {}
    for hit in hits:
        existing = best.get(hit.doc_id)
        if existing is None or hit.score > existing.score:
            best[hit.doc_id] = hit
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


def vector_hits_to_search_hits(results: list[dict[str, Any]]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for r in results:
        # LanceDB cosine metric returns _distance in [0, 2] where 0 = identical.
        # Convert to similarity: 1 - (distance / 2) gives [0, 1] range.
        distance = r.get("_distance", 0.0)
        cosine_similarity = 1.0 - distance / 2.0
        hits.append(
            SearchHit(
                doc_id=r["doc_id"],
                chunk_text=r["text"],
                score=cosine_similarity,
                field_name=r.get("field_name", ""),
                template_tag=r.get("template_tag", ""),
                chunk_type=r.get("chunk_type", ""),
                lang=r.get("lang", ""),
            )
        )
    return hits


async def hybrid_search(
    *,
    query_vector: list[float],
    query_text: str,
    vector_store_db: Any,
    keyword_search_fn: Any | None,
    reranker: RerankProvider | None,
    vector_top_k: int = 50,
    keyword_top_k: int = 30,
    rerank_top_n: int = 10,
    hybrid: bool = True,
    where: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[SearchResult]:
    from deepresearch_flow.paper.vector_store import query_vector as vs_query

    raw_vector = vs_query(vector_store_db, query_vector, top_k=vector_top_k, where=where)
    vector_hits = vector_hits_to_search_hits(raw_vector)
    aggregated_vector = aggregate_by_doc_id(vector_hits)

    if hybrid and keyword_search_fn is not None:
        keyword_doc_ids = keyword_search_fn(query_text, limit=keyword_top_k)
        vector_ranked = [h.doc_id for h in aggregated_vector]
        rrf_scores = reciprocal_rank_fusion([vector_ranked, keyword_doc_ids], k=60)
        hit_map = {h.doc_id: h for h in aggregated_vector}
        all_doc_ids = list(rrf_scores.keys())
        candidates: list[tuple[str, float, SearchHit | None]] = []
        for doc_id in all_doc_ids:
            candidates.append((doc_id, rrf_scores[doc_id], hit_map.get(doc_id)))
        candidates.sort(key=lambda c: c[1], reverse=True)
        score_type = "rrf"
    else:
        candidates = [(h.doc_id, h.score, h) for h in aggregated_vector]
        score_type = "cosine"

    if reranker is not None and client is not None:
        docs_for_rerank = []
        doc_id_order = []
        for doc_id, _, hit in candidates:
            text = hit.chunk_text if hit else doc_id
            docs_for_rerank.append(text)
            doc_id_order.append(doc_id)
        try:
            rerank_result = await reranker.rerank(
                query=query_text,
                documents=docs_for_rerank,
                top_n=rerank_top_n,
                client=client,
            )
            results: list[SearchResult] = []
            for idx, relevance in zip(rerank_result.indices, rerank_result.scores):
                doc_id = doc_id_order[idx]
                hit = next((h for _, _, h in candidates if h and h.doc_id == doc_id), None)
                results.append(
                    SearchResult(
                        doc_id=doc_id,
                        score=relevance,
                        score_type="rerank",
                        matched_chunk=hit.chunk_text if hit else "",
                        matched_field=hit.field_name if hit else "",
                        matched_template=hit.template_tag if hit else "",
                        matched_chunk_type=hit.chunk_type if hit else "",
                        matched_lang=hit.lang if hit else "",
                    )
                )
            return results
        except Exception:
            pass  # fall through to non-reranked results

    results = []
    for doc_id, score, hit in candidates[:rerank_top_n]:
        results.append(
            SearchResult(
                doc_id=doc_id,
                score=score,
                score_type=score_type,
                matched_chunk=hit.chunk_text if hit else "",
                matched_field=hit.field_name if hit else "",
                matched_template=hit.template_tag if hit else "",
                matched_chunk_type=hit.chunk_type if hit else "",
                matched_lang=hit.lang if hit else "",
            )
        )
    return results
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_search.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/search.py python/deepresearch_flow/paper/tests/test_search.py
git commit -m "feat: add hybrid search pipeline with RRF fusion and doc_id aggregation"
```

---

### Task 8: CLI — `paper embed` and `paper search` Commands

**Files:**
- Modify: `python/deepresearch_flow/paper/cli.py`
- Create: `python/deepresearch_flow/paper/tests/test_embed_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `python/deepresearch_flow/paper/tests/test_embed_cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli


def _write_embed_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [embedding]
        model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 2
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64
        provider = "ollama"

        [search]
        vector_dir = "paper_vectors"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [{ url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }]
        models = [{ model_name = "bge-m3", is_stream = false, is_support_json_schema = false, is_support_json_object = false, is_support_embedding = true, is_support_rerank = false }]
        """,
        encoding="utf-8",
    )
    return config_path


def test_paper_embed_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["paper", "embed", "--help"])
    assert result.exit_code == 0
    assert "--output-embed-db" in result.output
    assert "--snapshot-db" in result.output
    assert "--force" in result.output
    assert "--template-tag" in result.output


def test_paper_search_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["paper", "search", "--help"])
    assert result.exit_code == 0
    assert "--embed-db" in result.output
    assert "--no-rerank" in result.output
    assert "--no-hybrid" in result.output


def test_paper_embed_rejects_no_input(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    result = runner.invoke(cli, ["paper", "embed", "-c", str(config_path)])
    assert result.exit_code != 0
    assert "input" in result.output.lower() or "snapshot" in result.output.lower()


def test_paper_embed_rejects_mixed_sources(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_embed_config(tmp_path)
    json_path = tmp_path / "papers.json"
    json_path.write_text("[]", encoding="utf-8")
    result = runner.invoke(
        cli,
        [
            "paper", "embed",
            "-c", str(config_path),
            "-i", str(json_path),
            "--snapshot-db", "fake.db",
            "--static-export-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_cli.py -v`

Expected: FAIL

- [ ] **Step 3: Add `paper embed` command to `cli.py`**

In `python/deepresearch_flow/paper/cli.py`, add after the `extract` command:

```python
@paper.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option("-i", "--input", "input_paths", multiple=True, help="Input paper_infos JSON (repeatable)")
@click.option("--snapshot-db", "snapshot_db", default=None, help="Snapshot SQLite database path")
@click.option("--static-export-dir", "static_export_dir", default=None, help="Snapshot static export directory")
@click.option("--md-root", "md_roots", multiple=True, help="Source markdown root directory")
@click.option("--md-translated-root", "md_translated_roots", multiple=True, help="Translated markdown root directory")
@click.option("--output-embed-db", "output_embed_db", default=None, help="LanceDB output directory")
@click.option("--template-tag", "template_tag", default=None, help="Override template tag for all JSON inputs")
@click.option("--force", is_flag=True, help="Delete existing index and rebuild from scratch")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def embed(
    config_path: str,
    input_paths: tuple[str, ...],
    snapshot_db: str | None,
    static_export_dir: str | None,
    md_roots: tuple[str, ...],
    md_translated_roots: tuple[str, ...],
    output_embed_db: str | None,
    template_tag: str | None,
    force: bool,
    verbose: bool,
) -> None:
    """Build vector embeddings for paper search."""
    from deepresearch_flow.paper.config import load_config

    config = load_config(config_path)
    if not config.embedding:
        raise click.ClickException("Config missing [embedding] section")

    has_json = len(input_paths) > 0
    has_snapshot = snapshot_db is not None
    if not has_json and not has_snapshot:
        raise click.ClickException("Provide -i <json> or --snapshot-db <db>")
    if has_json and has_snapshot:
        raise click.ClickException("-i and --snapshot-db are mutually exclusive")
    if has_snapshot and not static_export_dir:
        raise click.ClickException("--snapshot-db requires --static-export-dir")

    vector_dir = Path(output_embed_db or config.search.vector_dir if config.search else "paper_vectors")

    if force and vector_dir.exists():
        import shutil
        click.echo(f"Removing existing vector index at {vector_dir} (--force)")
        shutil.rmtree(vector_dir)

    # Import and run the embedding pipeline
    import asyncio
    from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

    from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

    asyncio.run(
        run_embed_pipeline(
            config=config,
            input_paths=[Path(p) for p in input_paths] if has_json else None,
            snapshot_db=Path(snapshot_db) if snapshot_db else None,
            static_export_dir=Path(static_export_dir) if static_export_dir else None,
            md_roots=[Path(p) for p in md_roots],
            md_translated_roots=[Path(p) for p in md_translated_roots],
            vector_dir=vector_dir,
            template_tag_override=template_tag,
            verbose=verbose,
        )
    )
    click.echo("Embedding complete.")
```

- [ ] **Step 4: Add `paper search` command to `cli.py`**

```python
@paper.command()
@click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
@click.option("--embed-db", "embed_db", default=None, help="LanceDB directory to query")
@click.option("-q", "--query", "query_text", required=True, help="Search query")
@click.option("--top-n", "top_n", type=int, default=10, help="Number of results")
@click.option("--year", "year", type=int, default=None, help="Filter by year")
@click.option("--venue", "venue", default=None, help="Filter by venue")
@click.option("--no-rerank", "no_rerank", is_flag=True, help="Disable reranking")
@click.option("--no-hybrid", "no_hybrid", is_flag=True, help="Vector-only, no keyword recall")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def search(
    config_path: str,
    embed_db: str | None,
    query_text: str,
    top_n: int,
    year: int | None,
    venue: str | None,
    no_rerank: bool,
    no_hybrid: bool,
    verbose: bool,
) -> None:
    """Search papers using hybrid semantic + keyword search."""
    import asyncio
    from deepresearch_flow.paper.config import load_config

    config = load_config(config_path)
    if not config.embedding:
        raise click.ClickException("Config missing [embedding] section")

    vector_dir = Path(embed_db or (config.search.vector_dir if config.search else "paper_vectors"))
    if not vector_dir.exists():
        raise click.ClickException(f"Vector index not found at {vector_dir}. Run 'paper embed' first.")

    asyncio.run(
        _run_search(
            config=config,
            vector_dir=vector_dir,
            query_text=query_text,
            top_n=top_n,
            year=year,
            venue=venue,
            no_rerank=no_rerank,
            no_hybrid=no_hybrid,
        )
    )
```

Note: `run_embed_pipeline` is implemented in Task 8.5 below. `_run_search` is a thin wrapper calling `hybrid_search` from Task 7.

- [ ] **Step 5: Run CLI tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_cli.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/cli.py python/deepresearch_flow/paper/tests/test_embed_cli.py
git commit -m "feat: add paper embed and paper search CLI commands"
```

---

### Task 8.5: Embed Pipeline — Orchestration Layer

**Files:**
- Create: `python/deepresearch_flow/paper/embed_pipeline.py`
- Create: `python/deepresearch_flow/paper/tests/test_embed_pipeline.py`

This is the "load source → chunk → embed → incremental write" orchestrator that `paper embed` CLI and `snapshot build --output-embed-db` both call.

- [ ] **Step 1: Write failing pipeline tests**

Create `python/deepresearch_flow/paper/tests/test_embed_pipeline.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline
from deepresearch_flow.paper.config import EmbeddingConfig


def _write_json(tmp_path: Path) -> Path:
    data = [
        {
            "paper_title": "Test Paper",
            "summary": "A summary of the test paper.",
            "source_path": "papers/test.md",
            "prompt_template": "simple",
            "paper_authors": ["Author A"],
        }
    ]
    path = tmp_path / "papers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _test_config() -> "PaperConfig":
    from deepresearch_flow.paper.config import (
        EmbeddingConfig, ExtractConfig, RenderConfig, PaperConfig,
        ProviderConfig, BaseConfig, KeyConfig, ModelCapability, MainModelConfig,
        DEFAULT_EXTRACT, DEFAULT_RENDER,
    )
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[
            ProviderConfig(
                name="ollama", type="openai_compatible",
                base=[BaseConfig(url="http://localhost/v1", weight=1, key=[KeyConfig(value="ollama", weight=1)])],
                models=[ModelCapability(model_name="bge-m3", is_stream=False, is_support_json_schema=False, is_support_json_object=False, is_support_embedding=True, is_support_rerank=False)],
                api_version=None, deployment=None, project_id=None, location=None,
                credentials_path=None, anthropic_version=None, max_tokens=None,
                extra_headers={}, system_prompt=None, user_prompt=None,
            ),
        ],
        main_model=[MainModelConfig(model="ollama/bge-m3", weight=1)],
        embedding=EmbeddingConfig(
            model="bge-m3", dimensions=4, normalized=True,
            batch_size=2, chunk_max_tokens=512, chunk_overlap_tokens=64,
            provider="ollama",
        ),
    )


def test_pipeline_creates_index_meta(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"

    async def fake_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):
        from deepresearch_flow.paper.embedding import EmbeddingResult
        return EmbeddingResult(vectors=[[0.1] * 4 for _ in texts], model=model, usage_tokens=len(texts))

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", fake_embed)

    asyncio.run(
        run_embed_pipeline(
            config=_test_config(),
            input_paths=[json_path],
            vector_dir=vector_dir,
        )
    )

    assert (vector_dir / "index_meta.json").exists()


def test_pipeline_incremental_skips_unchanged(tmp_path: Path, monkeypatch) -> None:
    json_path = _write_json(tmp_path)
    vector_dir = tmp_path / "vectors"
    call_count = 0

    async def counting_embed(base_url, api_key, model, texts, *, dimensions=None, client=None):
        nonlocal call_count
        call_count += len(texts)
        from deepresearch_flow.paper.embedding import EmbeddingResult
        return EmbeddingResult(vectors=[[0.1] * 4 for _ in texts], model=model, usage_tokens=len(texts))

    monkeypatch.setattr("deepresearch_flow.paper.embed_pipeline.call_embedding", counting_embed)

    asyncio.run(run_embed_pipeline(config=_test_config(), input_paths=[json_path], vector_dir=vector_dir))
    first_count = call_count

    asyncio.run(run_embed_pipeline(config=_test_config(), input_paths=[json_path], vector_dir=vector_dir))
    assert call_count == first_count  # no new embeddings on second run
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_pipeline.py -v`

Expected: FAIL

- [ ] **Step 3: Implement embed_pipeline.py**

Create `python/deepresearch_flow/paper/embed_pipeline.py`:

```python
"""Orchestration: load source -> chunk -> embed -> incremental write."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from deepresearch_flow.paper.chunker import (
    SearchableField,
    chunk_fields,
    extract_searchable_fields,
)
from deepresearch_flow.paper.config import EmbeddingConfig, PaperConfig, ProviderConfig
from deepresearch_flow.paper.embed_source import EmbedDocument, load_from_json, load_from_snapshot
from deepresearch_flow.paper.embedding import EmbeddingResult, call_embedding
from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    build_chunk_id,
    compute_group_hash,
    delete_groups,
    open_store,
    read_group_hashes,
    validate_index_meta,
    write_chunks,
)

logger = logging.getLogger(__name__)
_SHARED_KEY = "_shared"


def _build_searchable_fields(doc: EmbedDocument) -> list[SearchableField]:
    fields: list[SearchableField] = []
    # Shared: title (from first available template record)
    title = doc.metadata.title
    if title:
        fields.append(SearchableField("title", "title", title, "", ""))
    # Shared: source_md
    if doc.source_md:
        fields.append(SearchableField("source_md", "source_md", doc.source_md, "", ""))
    # Shared: translated_md per language
    for lang, text in doc.translations.items():
        fields.append(SearchableField("translated_md", "translated_md", text, "", lang))
    # Per-template structured fields
    for tag, records in doc.template_records.items():
        for record in records:
            fields.extend(extract_searchable_fields(record, tag))
    # Deduplicate title (template adapters may also emit it)
    seen_title = False
    deduped: list[SearchableField] = []
    for f in fields:
        if f.chunk_type == "title":
            if seen_title:
                continue
            seen_title = True
        deduped.append(f)
    return deduped


def _group_chunks_by_template_key(
    chunks: list[Any],
) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for chunk in chunks:
        key = chunk.template_tag if chunk.template_tag else _SHARED_KEY
        groups.setdefault(key, []).append(chunk)
    return groups


async def run_embed_pipeline(
    *,
    config: PaperConfig,
    input_paths: list[Path] | None = None,
    snapshot_db: Path | None = None,
    static_export_dir: Path | None = None,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
    vector_dir: Path,
    template_tag_override: str | None = None,
    verbose: bool = False,
) -> None:
    embedding_config = config.embedding
    if not embedding_config:
        raise ValueError("Config missing [embedding] section")

    # Load documents
    if input_paths:
        docs = load_from_json(
            input_paths,
            template_tag_override=template_tag_override,
            md_roots=md_roots,
            md_translated_roots=md_translated_roots,
        )
    elif snapshot_db and static_export_dir:
        docs = load_from_snapshot(snapshot_db, static_export_dir)
    else:
        raise ValueError("No input source provided")

    # Validate index metadata
    validate_index_meta(
        vector_dir,
        model=embedding_config.model,
        dimensions=embedding_config.dimensions,
        normalized=embedding_config.normalized,
        provider=embedding_config.provider,
    )

    db = open_store(vector_dir)
    existing_hashes = read_group_hashes(db)

    # Resolve embedding provider via existing routing (one-shot, not RoutePool)
    from deepresearch_flow.paper.routing import (
        ParsedModelSelector,
        resolve_model_capability,
        select_runtime_route,
    )
    from deepresearch_flow.paper.config import resolve_key_value

    provider_name = embedding_config.provider
    resolve_model_capability(provider_name, embedding_config.model, config.providers)
    selector = ParsedModelSelector(
        kind="single",
        fixed_model=f"{provider_name}/{embedding_config.model}",
        pool=[],
    )
    route = select_runtime_route(config, selector)
    base_url = route.base.url
    api_key = resolve_key_value(route.key.value)

    all_new_rows: list[ChunkRow] = []
    groups_to_delete: list[tuple[str, str]] = []
    source_group_keys: set[tuple[str, str]] = set()

    for doc in docs:
        fields = _build_searchable_fields(doc)
        chunks = chunk_fields(
            fields,
            max_tokens=embedding_config.chunk_max_tokens,
            overlap_tokens=embedding_config.chunk_overlap_tokens,
        )
        grouped = _group_chunks_by_template_key(chunks)

        for template_key, group_chunks in grouped.items():
            source_group_keys.add((doc.doc_id, template_key))
            hashes = [hashlib.sha256(c.text.encode()).hexdigest() for c in group_chunks]
            new_hash = compute_group_hash(hashes)
            existing_hash = existing_hashes.get((doc.doc_id, template_key))
            if existing_hash == new_hash:
                continue  # skip unchanged group
            if existing_hash is not None:
                groups_to_delete.append((doc.doc_id, template_key))

            for i, chunk in enumerate(group_chunks):
                chunk_type_label = (
                    f"{chunk.chunk_type}_{chunk.lang}" if chunk.chunk_type == "translated_md" and chunk.lang
                    else chunk.chunk_type
                )
                all_new_rows.append(
                    ChunkRow(
                        id=build_chunk_id(doc.doc_id, chunk.template_tag, chunk_type_label, chunk.chunk_index),
                        doc_id=doc.doc_id,
                        source_path=doc.metadata.source_path,
                        template_tag=chunk.template_tag,
                        chunk_type=chunk.chunk_type,
                        chunk_index=chunk.chunk_index,
                        field_name=chunk.field_name,
                        lang=chunk.lang,
                        text=chunk.text,
                        content_hash=hashes[i],
                        vector=[],  # placeholder, filled after embedding
                        title=doc.metadata.title,
                        year=doc.metadata.year,
                        authors=doc.metadata.authors,
                        venue=doc.metadata.venue,
                        tags=doc.metadata.tags,
                    )
                )

    # Delete changed/orphan groups
    orphan_keys = set(existing_hashes.keys()) - source_group_keys
    delete_groups(db, groups_to_delete + list(orphan_keys))

    if not all_new_rows:
        logger.info("No new chunks to embed.")
        return

    # Batch embed
    texts = [row.text for row in all_new_rows]
    batch_size = embedding_config.batch_size
    all_vectors: list[list[float]] = []
    async with httpx.AsyncClient() as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            result = await call_embedding(
                base_url=base_url,
                api_key=api_key,
                model=embedding_config.model,
                texts=batch,
                dimensions=embedding_config.dimensions,
                client=client,
            )
            all_vectors.extend(result.vectors)

    for row, vector in zip(all_new_rows, all_vectors):
        row_dict = row.__dict__.copy()
        row_dict["vector"] = vector
        all_new_rows[all_new_rows.index(row)] = ChunkRow(**row_dict)

    write_chunks(db, all_new_rows)
    logger.info("Embedded %d chunks across %d documents.", len(all_new_rows), len(docs))
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embed_pipeline.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/embed_pipeline.py python/deepresearch_flow/paper/tests/test_embed_pipeline.py
git commit -m "feat: add embed pipeline orchestration (load -> chunk -> embed -> write)"
```

---

### Task 9: DB Integration — `--output-embed-db` on snapshot build, `--embed-db` on serve

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py`

- [ ] **Step 1: Add `--output-embed-db` option to snapshot build command**

In `python/deepresearch_flow/paper/db.py`, find the `snapshot build` command and add:

```python
@click.option("--output-embed-db", "output_embed_db", default=None, help="Build LanceDB vector index alongside snapshot")
```

At the end of the build function, after snapshot construction completes, add:

```python
    if output_embed_db:
        from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline
        click.echo(f"Building vector index at {output_embed_db}...")
        asyncio.run(
            run_embed_pipeline(
                config=config,
                snapshot_db=Path(output_db),
                static_export_dir=Path(static_export_dir),
                vector_dir=Path(output_embed_db),
                verbose=verbose,
            )
        )
```

- [ ] **Step 2: Add `--embed-db` to `paper db serve` command**

In the `serve` command (the one at line ~1580 that uses `paper/web/app.py`), add:

```python
@click.option("--embed-db", "embed_db", default=None, help="LanceDB directory for semantic search")
@click.option("--search-access-token", "search_access_token", default=None, envvar="SEARCH_ACCESS_TOKEN", help="Token to gate semantic search")
```

Pass to `create_app`:

```python
    app = create_app(
        db_paths=[Path(p) for p in input_paths],
        # ... existing params ...
        embed_db=Path(embed_db) if embed_db else None,
        search_access_token=search_access_token,
    )
```

- [ ] **Step 3: (Secondary parity) Optionally add `--embed-db` to `api serve` command**

The primary semantic search integration is on `paper db serve` (Task 10). The snapshot-backed `api serve` command (`snapshot/api.py`) can optionally receive the same `--embed-db` for parity, but this is lower priority and can be deferred. If implemented, follow the same pattern as Step 2 but pass to `snapshot/api.py`'s `create_app`. Mark as done or skipped.

- [ ] **Step 4: Verify CLI help**

Run:

```bash
uv run python -m deepresearch_flow paper db serve --help
uv run python -m deepresearch_flow paper db snapshot build --help
```

Expected: new options appear.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/db.py
git commit -m "feat: add --output-embed-db to snapshot build, --embed-db to serve commands"
```

---

### Task 10: Semantic API Endpoint with Token Gate

**Files:**
- Modify: `python/deepresearch_flow/paper/web/app.py`
- Modify: `python/deepresearch_flow/paper/web/handlers/api.py`
- Create: `python/deepresearch_flow/paper/tests/test_semantic_api.py`

This task adds `/api/papers/semantic` to the **Web UI app** (`paper/web/`), not the snapshot API. The Web UI is what the frontend calls.

- [ ] **Step 1: Write failing API tests**

Create `python/deepresearch_flow/paper/tests/test_semantic_api.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from deepresearch_flow.paper.vector_store import ChunkRow, open_store, write_chunks


def _create_test_embed_db(tmp_path: Path) -> Path:
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    db = open_store(embed_dir)
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
            text="Attention Is All You Need",
            content_hash="abc",
            vector=[0.1] * 1024,
            title="Attention Is All You Need",
            year=2017,
            authors="Vaswani",
            venue="NeurIPS",
            tags="transformer",
        ),
    ]
    write_chunks(db, rows)
    # Write index_meta.json
    from deepresearch_flow.paper.vector_store import save_index_meta, INDEX_VERSION
    save_index_meta(embed_dir, {
        "model": "bge-m3", "dimensions": 1024, "normalized": True,
        "provider": "test", "index_version": INDEX_VERSION,
    })
    return embed_dir


def _make_app(tmp_path: Path, *, access_token: str | None = None) -> TestClient:
    embed_dir = _create_test_embed_db(tmp_path)
    # Minimal Starlette app with just the semantic endpoint
    from starlette.applications import Starlette
    from starlette.routing import Route
    from deepresearch_flow.paper.web.handlers.api import api_papers_semantic

    app = Starlette(routes=[Route("/api/papers/semantic", api_papers_semantic)])
    app.state.embed_db = open_store(embed_dir)
    app.state.search_access_token = access_token
    app.state.embedding_config = None  # will be monkeypatched
    return TestClient(app)


def test_semantic_returns_403_without_token(tmp_path: Path) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    resp = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert resp.status_code == 403


def test_semantic_returns_403_wrong_token(tmp_path: Path) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    resp = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_semantic_returns_200_correct_token(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token="secret-token")
    # Mock the embedding call so we don't need a real model
    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query
    )
    resp = client.get(
        "/api/papers/semantic?q=attention&top_n=5",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


def test_semantic_open_when_no_token_configured(tmp_path: Path, monkeypatch) -> None:
    client = _make_app(tmp_path, access_token=None)  # no token gate
    async def fake_embed_query(text, config, client_obj):
        return [0.1] * 1024
    monkeypatch.setattr(
        "deepresearch_flow.paper.web.handlers.api._embed_query", fake_embed_query
    )
    resp = client.get("/api/papers/semantic?q=attention&top_n=5")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_semantic_api.py -v`

Expected: FAIL — `api_papers_semantic` does not exist yet.

- [ ] **Step 3: Extend `create_app` in `paper/web/app.py`**

Add `embed_db` and `search_access_token` parameters:

```python
def create_app(
    *,
    db_paths: list[Path],
    # ... existing params ...
    embed_db: Path | None = None,
    search_access_token: str | None = None,
) -> Starlette:
```

In the function body, after existing setup:

```python
    if embed_db and embed_db.exists():
        from deepresearch_flow.paper.vector_store import open_store
        app.state.embed_db = open_store(embed_db)
    else:
        app.state.embed_db = None
    app.state.search_access_token = search_access_token
    app.state.paper_config = None  # set by caller if embedding config is available
```

Add route:

```python
    Route("/api/papers/semantic", api_papers_semantic),
```

- [ ] **Step 4: Implement the semantic handler in `web/handlers/api.py`**

```python
async def _embed_query(
    text: str, config: "PaperConfig", client: httpx.AsyncClient
) -> list[float]:
    from deepresearch_flow.paper.embedding import call_embedding
    from deepresearch_flow.paper.config import resolve_key_value
    from deepresearch_flow.paper.routing import ParsedModelSelector, select_runtime_route

    embedding_config = config.embedding
    selector = ParsedModelSelector(
        kind="single",
        fixed_model=f"{embedding_config.provider}/{embedding_config.model}",
        pool=[],
    )
    route = select_runtime_route(config, selector)
    result = await call_embedding(
        base_url=route.base.url,
        api_key=resolve_key_value(route.key.value),
        model=embedding_config.model,
        texts=[text],
        dimensions=embedding_config.dimensions,
        client=client,
    )
    return result.vectors[0]


async def api_papers_semantic(request: Request) -> JSONResponse:
    access_token = getattr(request.app.state, "search_access_token", None)
    if access_token:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != access_token:
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    embed_db = getattr(request.app.state, "embed_db", None)
    if embed_db is None:
        return JSONResponse({"error": "Semantic search not available"}, status_code=503)

    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "Query parameter q is required"}, status_code=400)

    top_n = min(int(request.query_params.get("top_n", "10")), 100)

    from deepresearch_flow.paper.vector_store import query_vector
    from deepresearch_flow.paper.search import (
        vector_hits_to_search_hits,
        aggregate_by_doc_id,
    )

    # Build where clause from filters
    where_parts: list[str] = []
    year = request.query_params.get("year")
    if year:
        where_parts.append(f"year = {int(year)}")
    venue = request.query_params.get("venue")
    if venue:
        where_parts.append(f'venue = "{venue}"')
    where = " AND ".join(where_parts) if where_parts else None

    # Embed query
    paper_config = getattr(request.app.state, "paper_config", None)
    async with httpx.AsyncClient() as client:
        query_vector_val = await _embed_query(q, paper_config, client)

    # Vector search
    raw = query_vector(embed_db, query_vector_val, top_k=top_n * 5, where=where)
    hits = vector_hits_to_search_hits(raw)
    aggregated = aggregate_by_doc_id(hits)[:top_n]

    items = [
        {
            "doc_id": h.doc_id,
            "score": h.score,
            "score_type": "cosine",
            "matched_chunk": h.chunk_text,
            "matched_field": h.field_name,
            "matched_template": h.template_tag or "_shared",
            "matched_chunk_type": h.chunk_type,
            "matched_lang": h.lang,
        }
        for h in aggregated
    ]
    return JSONResponse({"items": items, "total": len(items)})
```

- [ ] **Step 5: Run API tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_semantic_api.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/web/app.py python/deepresearch_flow/paper/web/handlers/api.py python/deepresearch_flow/paper/tests/test_semantic_api.py
git commit -m "feat: add /api/papers/semantic endpoint with token gate to web UI"
```

---

### Task 11: Frontend — Token Modal and Search Mode Switch

**Files:**
- Modify: Frontend search component (exact path depends on current frontend structure in `python/deepresearch_flow/paper/web/`)

- [ ] **Step 1: Add IndexedDB helper for token storage**

Add a small JavaScript module (inline in template or separate file):

```javascript
const DB_NAME = 'deepresearch_flow';
const STORE_NAME = 'settings';
const TOKEN_KEY = 'search_access_token';

async function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => req.result.createObjectStore(STORE_NAME);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

async function getToken() {
    const db = await openDB();
    return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const req = tx.objectStore(STORE_NAME).get(TOKEN_KEY);
        req.onsuccess = () => resolve(req.result?.token || null);
        req.onerror = () => resolve(null);
    });
}

async function saveToken(token) {
    const db = await openDB();
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).put({ token, saved_at: new Date().toISOString() }, TOKEN_KEY);
}

async function clearToken() {
    const db = await openDB();
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).delete(TOKEN_KEY);
}
```

- [ ] **Step 2: Add lock/unlock icon and DaisyUI modal**

Add to the search bar area:

```html
<!-- Lock button -->
<button id="semantic-toggle" class="btn btn-ghost btn-sm" title="Unlock semantic search">
    <svg id="lock-icon" ...><!-- lock icon --></svg>
</button>

<!-- DaisyUI modal -->
<dialog id="token-modal" class="modal">
    <div class="modal-box">
        <h3 class="text-lg font-bold">Unlock Semantic Search</h3>
        <input type="password" id="token-input" class="input input-bordered w-full mt-4"
               placeholder="Enter access token" />
        <p id="token-error" class="text-error text-sm mt-1 hidden">Invalid token</p>
        <div class="modal-action">
            <button id="token-submit" class="btn btn-primary">Unlock</button>
            <form method="dialog"><button class="btn">Cancel</button></form>
        </div>
    </div>
</dialog>
```

- [ ] **Step 3: Wire up token validation and search mode switching**

```javascript
document.addEventListener('DOMContentLoaded', async () => {
    const token = await getToken();
    if (token) {
        activateSemanticMode(token);
    }

    document.getElementById('semantic-toggle').addEventListener('click', () => {
        document.getElementById('token-modal').showModal();
    });

    document.getElementById('token-submit').addEventListener('click', async () => {
        const input = document.getElementById('token-input');
        const token = input.value.trim();
        try {
            const resp = await fetch('/api/papers/semantic?q=test&top_n=1', {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (resp.ok) {
                await saveToken(token);
                activateSemanticMode(token);
                document.getElementById('token-modal').close();
                document.getElementById('token-error').classList.add('hidden');
            } else {
                document.getElementById('token-error').classList.remove('hidden');
            }
        } catch {
            document.getElementById('token-error').classList.remove('hidden');
        }
    });
});

function activateSemanticMode(token) {
    // Change lock icon to unlocked
    // Add "Semantic" badge to search bar
    // Override search function to use /api/papers/semantic with Authorization header
    // On 403 response: clearToken(), revert to keyword mode
}
```

- [ ] **Step 4: Commit**

```bash
git add <frontend files>
git commit -m "feat: add token-gated semantic search UI with IndexedDB persistence"
```

---

### Task 12: Update Documentation and Example Config

**Files:**
- Modify: `config.example.toml`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Add embedding/rerank/search sections to `config.example.toml`**

Add after the existing provider examples:

```toml
# --- Embedding & Search (optional) ---

[embedding]
model = "bge-m3"
dimensions = 1024
normalized = true
batch_size = 32
chunk_max_tokens = 512
chunk_overlap_tokens = 64
provider = "ollama"           # references a [[providers]] name with is_support_embedding = true

[rerank]
enabled = true
model = "BAAI/bge-reranker-v2-m3"
top_n = 10
provider = "siliconflow"     # references a [[providers]] name with is_support_rerank = true

[search]
vector_dir = "paper_vectors"
vector_top_k = 50
keyword_top_k = 30
hybrid = true
# access_token = "env:SEARCH_ACCESS_TOKEN"   # optional: gate semantic search behind a token
```

- [ ] **Step 2: Update README with embedding commands**

Add a "Semantic Search" section documenting `paper embed`, `paper search`, and the `/api/papers/semantic` endpoint.

- [ ] **Step 3: Verify no stale references**

Run:

```bash
rg -n "api_keys|model_list|structured_mode" README.md README_ZH.md config.example.toml | grep -v migration
```

Expected: no stale references outside migration notes.

- [ ] **Step 4: Commit**

```bash
git add config.example.toml README.md README_ZH.md
git commit -m "docs: document embedding, rerank, and hybrid search configuration"
```

---

### Task 13: Full Verification

**Files:** No code changes.

- [ ] **Step 1: Run all new tests**

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

Expected: all PASS.

- [ ] **Step 2: Run existing tests for regressions**

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_weighted_config.py \
  python/deepresearch_flow/paper/tests/test_weighted_routing.py \
  python/deepresearch_flow/paper/tests/test_extract_errors.py \
  python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py -v
```

Expected: all PASS — existing functionality unbroken.

- [ ] **Step 3: CLI help smoke tests**

```bash
uv run python -m deepresearch_flow --help
uv run python -m deepresearch_flow paper --help
uv run python -m deepresearch_flow paper embed --help
uv run python -m deepresearch_flow paper search --help
uv run python -m deepresearch_flow paper db snapshot build --help
```

Expected: all help outputs show new options.

- [ ] **Step 4: Commit verification fixes if needed**

```bash
git add <files>
git commit -m "test: verification fixes for embedding and hybrid search"
```
