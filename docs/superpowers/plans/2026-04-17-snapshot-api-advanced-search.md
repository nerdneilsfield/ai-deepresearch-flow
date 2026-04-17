# Advanced Search on `paper db api serve` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a token-gated `GET /api/v1/search/advanced` endpoint (plus `/verify-token`) on the snapshot JSON API, with a Vue frontend panel, reusing the deployed `snapshot.db` + `LanceDB` read-only.

**Architecture:** New Python package `python/deepresearch_flow/paper/snapshot/advanced/` containing pure functional pipeline stages (normalize → filter → dense + sparse → RRF fuse → chunk select → dedup → rerank → MMR → response assembly) plus Starlette handlers. Wired into existing `snapshot/api.py::create_app` through a new `AdvancedSearchContext`. New Vue components and composable under `frontend/src/` reuse the existing Vitest convention.

**Tech Stack:** Python ≥ 3.12 (see `pyproject.toml:8`, `.python-version`), Starlette, LanceDB, SQLite FTS5, `httpx`, `click`, `dataclasses`; Vue 3, TypeScript, Vitest, `@vue/test-utils`, `jsdom`.

**Spec:** `docs/superpowers/specs/2026-04-17-snapshot-api-advanced-search-design.md` rev 5.

---

## Phase 1 — Backend foundation

### Task 1: Extend `SearchConfig` with `advanced_*` fields

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py` (`SearchConfig` dataclass; `_parse_search_config` helper at line 686)
- Test: `python/deepresearch_flow/paper/tests/test_search_config_advanced.py`

- [ ] **Step 1: Write failing tests**

Create `python/deepresearch_flow/paper/tests/test_search_config_advanced.py`:

```python
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def _base_config() -> str:
    # Shape matches the current _parse_base_configs, _parse_model_capabilities,
    # and _parse_main_model in paper/config.py. See config.example.toml for the
    # authoritative layout.
    return """
        main_model = [
          { model = "ollama/m", weight = 1 },
        ]

        [extract]
        output = "out.json"
        errors = "err.json"

        [render]

        [[providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [
          { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "x", weight = 1 }] },
        ]
        models = [
          { model_name = "m" },
        ]

        [embedding]
        default_provider = "ollama"
        default_model = "bge-m3"
        dimensions = 1024
        normalized = true
        batch_size = 16
        chunk_max_tokens = 512
        chunk_overlap_tokens = 64

        [[embedding.providers]]
        name = "ollama"
        type = "openai_compatible"
        base = [
          { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] },
        ]
        models = [
          { model_name = "bge-m3", dimensions = 1024, max_context = 8192 },
        ]
    """


def test_advanced_defaults_present_when_search_section_exists(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_dir = "./embed_db"
        vector_top_k = 50
        keyword_top_k = 30
        hybrid = true
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search is not None
    assert cfg.search.advanced_enabled is False
    assert cfg.search.advanced_rrf_k == 60
    assert cfg.search.advanced_dense_top_k == 50
    assert cfg.search.advanced_sparse_top_k == 30
    assert cfg.search.advanced_post_fusion_top_k == 50
    assert cfg.search.advanced_dedup_cosine_threshold == pytest.approx(0.95)
    assert cfg.search.advanced_rerank_top_n == 20
    assert cfg.search.advanced_mmr_lambda_default == pytest.approx(0.6)
    assert cfg.search.advanced_rerank_timeout_ms == 1500
    assert cfg.search.advanced_top_n_max == 50
    assert cfg.search.advanced_max_query_length == 500


def test_advanced_fields_overridable(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_dir = "./embed_db"
        vector_top_k = 40
        keyword_top_k = 20
        hybrid = true
        advanced_enabled = true
        advanced_rrf_k = 30
        advanced_rerank_timeout_ms = 2500
        advanced_top_n_max = 25
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search.advanced_enabled is True
    assert cfg.search.advanced_rrf_k == 30
    assert cfg.search.advanced_rerank_timeout_ms == 2500
    assert cfg.search.advanced_top_n_max == 25


def test_existing_search_fields_still_parse(tmp_path: Path) -> None:
    body = _base_config() + """
        [search]
        vector_dir = "./v"
        vector_top_k = 10
        keyword_top_k = 5
        hybrid = false
        access_token = "env:SEARCH_ACCESS_TOKEN"
    """
    cfg = load_config(str(_write_toml(tmp_path, body)))
    assert cfg.search.vector_dir == "./v"
    assert cfg.search.hybrid is False
    assert cfg.search.access_token is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_search_config_advanced.py -v`
Expected: `test_advanced_defaults_present_when_search_section_exists` and `test_advanced_fields_overridable` FAIL with `AttributeError: 'SearchConfig' object has no attribute 'advanced_enabled'`.

- [ ] **Step 3: Extend `SearchConfig` dataclass**

Open `python/deepresearch_flow/paper/config.py`. Find the existing `SearchConfig` dataclass (around line 276). Append the new fields at the end (after `access_token`):

```python
@dataclass(frozen=True)
class SearchConfig:
    vector_dir: str
    vector_top_k: int
    keyword_top_k: int
    hybrid: bool
    access_token: str | None = None

    advanced_enabled: bool = False
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

- [ ] **Step 4: Extend `_parse_search_config` to read new fields**

Find `_parse_search_config` at `python/deepresearch_flow/paper/config.py:686`. The current body returns a `SearchConfig(...)` with the five legacy fields (`vector_dir`, `vector_top_k`, `keyword_top_k`, `hybrid`, `access_token`). Extend the returned constructor call to include the eleven new `advanced_*` fields, keeping the existing access_token branch (using `resolve_key_value` + `_as_str`) untouched:

```python
def _parse_search_config(value: Any) -> SearchConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config [search] must be an object")

    vector_dir = _as_str(value.get("vector_dir"))
    if not vector_dir:
        raise ValueError("Config [search] must include vector_dir")

    return SearchConfig(
        vector_dir=vector_dir,
        vector_top_k=_as_int(value.get("vector_top_k"), 0),
        keyword_top_k=_as_int(value.get("keyword_top_k"), 0),
        hybrid=_as_bool(value.get("hybrid"), False),
        access_token=(
            resolve_key_value(raw_token)
            if (raw_token := _as_str(value.get("access_token"), None))
            else None
        ),
        advanced_enabled=_as_bool(value.get("advanced_enabled"), False),
        advanced_rrf_k=_as_int(value.get("advanced_rrf_k"), 60),
        advanced_dense_top_k=_as_int(value.get("advanced_dense_top_k"), 50),
        advanced_sparse_top_k=_as_int(value.get("advanced_sparse_top_k"), 30),
        advanced_post_fusion_top_k=_as_int(
            value.get("advanced_post_fusion_top_k"), 50
        ),
        advanced_dedup_cosine_threshold=float(
            value.get("advanced_dedup_cosine_threshold", 0.95)
        ),
        advanced_rerank_top_n=_as_int(value.get("advanced_rerank_top_n"), 20),
        advanced_mmr_lambda_default=float(
            value.get("advanced_mmr_lambda_default", 0.6)
        ),
        advanced_rerank_timeout_ms=_as_int(
            value.get("advanced_rerank_timeout_ms"), 1500
        ),
        advanced_top_n_max=_as_int(value.get("advanced_top_n_max"), 50),
        advanced_max_query_length=_as_int(
            value.get("advanced_max_query_length"), 500
        ),
    )
```

All helpers (`_as_int`, `_as_bool`, `_as_str`, `resolve_key_value`) already exist in the module; no new imports needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_search_config_advanced.py -v`
Expected: all three tests PASS.

- [ ] **Step 6: Run the broader config test suite to confirm no regressions**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_embedding_config.py python/deepresearch_flow/paper/tests/test_weighted_config.py -v`
Expected: PASS (all existing).

- [ ] **Step 7: Commit**

```bash
git add python/deepresearch_flow/paper/config.py \
  python/deepresearch_flow/paper/tests/test_search_config_advanced.py
git commit -m "feat(config): add advanced_* fields to SearchConfig"
```

---

### Task 2: Create `AdvancedSearchContext` + `advanced` package skeleton

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/__init__.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/config.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/__init__.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_context.py`

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/__init__.py` (empty file).

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_context.py`:

```python
from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext


def test_context_is_frozen_dataclass(tmp_path: Path) -> None:
    ctx = AdvancedSearchContext(
        embed_db_path=tmp_path,
        lance_db=object(),
        paper_config=object(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token="abc",
        search_config=object(),
    )
    try:
        ctx.search_access_token = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("AdvancedSearchContext should be frozen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deepresearch_flow.paper.snapshot.advanced'`.

- [ ] **Step 3: Create the package + `AdvancedSearchContext`**

Create `python/deepresearch_flow/paper/snapshot/advanced/__init__.py`:

```python
"""Advanced search endpoint on snapshot API (token-gated hybrid retrieval)."""

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext

__all__ = ["AdvancedSearchContext"]
```

Create `python/deepresearch_flow/paper/snapshot/advanced/config.py`:

```python
"""Advanced search runtime context bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdvancedSearchContext:
    """Immutable bundle of runtime handles the advanced endpoint needs.

    Assembled once at server startup; mutated never.
    """

    embed_db_path: Path
    lance_db: Any
    paper_config: Any
    embedding_route_pool: Any
    rerank_route_pool: Any | None
    search_access_token: str
    search_config: Any
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/
git commit -m "feat(advanced): scaffold advanced search package with context dataclass"
```

---

### Task 3: Typed errors module

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/errors.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_errors.py`

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_errors.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.errors import (
    AdvancedSearchError,
    InvalidFilterError,
    InvalidQueryError,
    TotalFailureError,
    UnauthorizedError,
    VectorStoreUnavailableError,
)


def test_invalid_query_has_correct_status_and_code() -> None:
    exc = InvalidQueryError("query too long")
    assert exc.code == "INVALID_QUERY"
    assert exc.http_status == 400
    assert isinstance(exc, AdvancedSearchError)


def test_invalid_filter_has_correct_status_and_code() -> None:
    exc = InvalidFilterError("bad venue")
    assert exc.code == "INVALID_FILTER"
    assert exc.http_status == 400


def test_unauthorized_carries_reason() -> None:
    exc = UnauthorizedError("missing")
    assert exc.code == "UNAUTHORIZED"
    assert exc.http_status == 401
    assert exc.reason == "missing"


def test_unauthorized_reason_invalid() -> None:
    exc = UnauthorizedError("invalid")
    assert exc.reason == "invalid"


def test_vector_store_unavailable() -> None:
    exc = VectorStoreUnavailableError("lancedb open failed")
    assert exc.code == "VECTOR_STORE_UNAVAILABLE"
    assert exc.http_status == 503


def test_total_failure() -> None:
    exc = TotalFailureError("both channels dead")
    assert exc.code == "TOTAL_FAILURE"
    assert exc.http_status == 503


def test_base_error_defaults() -> None:
    with pytest.raises(AdvancedSearchError):
        raise AdvancedSearchError("x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deepresearch_flow.paper.snapshot.advanced.errors'`.

- [ ] **Step 3: Implement errors module**

Create `python/deepresearch_flow/paper/snapshot/advanced/errors.py`:

```python
"""Typed exceptions for the advanced search endpoint.

Each exception carries an HTTP status and a machine-readable code for the
error envelope in responses.
"""

from __future__ import annotations


class AdvancedSearchError(Exception):
    """Base class for advanced search errors."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500


class InvalidQueryError(AdvancedSearchError):
    code = "INVALID_QUERY"
    http_status = 400


class InvalidFilterError(AdvancedSearchError):
    code = "INVALID_FILTER"
    http_status = 400


class UnauthorizedError(AdvancedSearchError):
    code = "UNAUTHORIZED"
    http_status = 401

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VectorStoreUnavailableError(AdvancedSearchError):
    code = "VECTOR_STORE_UNAVAILABLE"
    http_status = 503


class TotalFailureError(AdvancedSearchError):
    code = "TOTAL_FAILURE"
    http_status = 503
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_errors.py -v`
Expected: all seven tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/errors.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_errors.py
git commit -m "feat(advanced): typed exceptions with error codes and http status"
```

---

## Phase 2 — Backend retrieval primitives

### Task 4: Query normalization

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/normalize.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_normalize.py`

Interface contract: `normalize(raw: str) -> NormalizedQuery` returns a frozen dataclass `{raw, normalized, fts_expr, lang}`. NFC-normalize, collapse whitespace, detect language via CJK ratio (`zh` if > 0.5, `mixed` if > 0.1, else `en`), produce an FTS-safe MATCH expression using the existing `snapshot/text.py::rewrite_search_query`.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_normalize.py`:

```python
from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.normalize import NormalizedQuery, normalize


def test_nfc_and_whitespace() -> None:
    q = normalize("  Vision   Transformer\n\t Pre-training  ")
    assert q.normalized == "Vision Transformer Pre-training"


def test_empty_query_returns_empty_fields() -> None:
    q = normalize("   ")
    assert q.normalized == ""
    assert q.fts_expr == ""
    assert q.lang == "en"


def test_language_detect_zh() -> None:
    q = normalize("视觉 transformer 预训练")
    assert q.lang in {"zh", "mixed"}


def test_language_detect_en() -> None:
    q = normalize("vision transformer pretraining")
    assert q.lang == "en"


def test_language_detect_mixed() -> None:
    q = normalize("transformer 预训练 model")
    assert q.lang == "mixed"


def test_fts_expr_nonempty_for_non_empty_query() -> None:
    q = normalize("vision transformer")
    assert q.fts_expr != ""


def test_returns_frozen_dataclass() -> None:
    q = normalize("hello")
    assert isinstance(q, NormalizedQuery)
    try:
        q.raw = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("NormalizedQuery should be frozen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement normalize module**

Create `python/deepresearch_flow/paper/snapshot/advanced/normalize.py`:

```python
"""Query normalization: NFC, whitespace, language detection, FTS rewrite."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.text import rewrite_search_query

_WS_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


@dataclass(frozen=True)
class NormalizedQuery:
    raw: str
    normalized: str
    fts_expr: str
    lang: str  # "zh" | "en" | "mixed"


def normalize(raw: str) -> NormalizedQuery:
    normalized = unicodedata.normalize("NFC", raw or "")
    normalized = _WS_RE.sub(" ", normalized).strip()
    if not normalized:
        return NormalizedQuery(raw=raw, normalized="", fts_expr="", lang="en")
    lang = _detect_lang(normalized)
    fts_expr = rewrite_search_query(normalized) or ""
    return NormalizedQuery(
        raw=raw, normalized=normalized, fts_expr=fts_expr, lang=lang
    )


def _detect_lang(text: str) -> str:
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return "en"
    cjk = len(_CJK_RE.findall(text))
    ratio = cjk / max(1, len(non_ws))
    if ratio > 0.5:
        return "zh"
    if ratio > 0.1:
        return "mixed"
    return "en"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_normalize.py -v`
Expected: all seven tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/normalize.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_normalize.py
git commit -m "feat(advanced): query normalize + language detection + FTS rewrite"
```

---

### Task 5: Filter parsing

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/filters.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_filters.py`

Interface contract: `parse_filters(params: Mapping[str, list[str]]) -> ParsedFilters` returning a frozen dataclass with `year: YearRange | None`, `venues: tuple[str, ...]`, `authors: tuple[str, ...]`, `keywords: tuple[str, ...]`, `tags: tuple[str, ...]`, `lang: str | None`, `sql_where: str`, `lance_where: str`, `applied: dict`. Validates venue via `search.validate_venue_filter`; parses `2020..2023` range syntax; raises `InvalidFilterError` on bad input.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_filters.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.errors import InvalidFilterError
from deepresearch_flow.paper.snapshot.advanced.filters import ParsedFilters, parse_filters


def test_empty_filters() -> None:
    out = parse_filters({})
    assert isinstance(out, ParsedFilters)
    assert out.year is None
    assert out.venues == ()
    assert out.authors == ()
    assert out.sql_where == ""
    assert out.lance_where == ""
    assert out.applied == {}


def test_year_single() -> None:
    out = parse_filters({"filters.year": ["2023"]})
    assert out.year is not None
    assert out.year.min == 2023 and out.year.max == 2023
    assert out.applied["year"] == {"min": 2023, "max": 2023}


def test_year_range() -> None:
    out = parse_filters({"filters.year": ["2020..2023"]})
    assert out.year.min == 2020 and out.year.max == 2023
    assert "year" in out.sql_where.lower() or "year" in out.lance_where.lower()


def test_year_invalid_raises() -> None:
    with pytest.raises(InvalidFilterError):
        parse_filters({"filters.year": ["abc"]})


def test_venue_validated() -> None:
    out = parse_filters({"filters.venue": ["NeurIPS"]})
    assert out.venues == ("NeurIPS",)


def test_venue_invalid_raises() -> None:
    with pytest.raises(InvalidFilterError):
        parse_filters({"filters.venue": ["drop; table"]})


def test_authors_normalized_and_tuple() -> None:
    out = parse_filters(
        {"filters.authors": ["Hinton G.", "LeCun Y."]}
    )
    assert out.authors == ("hinton g.", "lecun y.")


def test_applied_echoes_only_present_filters() -> None:
    out = parse_filters({"filters.tags": ["nlp"], "filters.lang": ["en"]})
    assert "tags" in out.applied
    assert "lang" in out.applied
    assert "authors" not in out.applied


def test_is_frozen() -> None:
    out = parse_filters({})
    try:
        out.venues = ("x",)  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ParsedFilters should be frozen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_filters.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement filters module**

Create `python/deepresearch_flow/paper/snapshot/advanced/filters.py`:

```python
"""Filter parsing for the advanced search endpoint.

Produces SQL WHERE clauses for the sparse branch and LanceDB where-strings
for the dense branch. Raises InvalidFilterError on malformed input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from deepresearch_flow.paper.search import validate_venue_filter
from deepresearch_flow.paper.snapshot.advanced.errors import InvalidFilterError

_YEAR_RANGE_RE = re.compile(r"^(\d{4})\.\.(\d{4})$")
_YEAR_SINGLE_RE = re.compile(r"^\d{4}$")
_IDENT_RE = re.compile(r"^[\w\s\-\.\+&/(),:]+$", re.UNICODE)


@dataclass(frozen=True)
class YearRange:
    min: int
    max: int


@dataclass(frozen=True)
class ParsedFilters:
    year: YearRange | None
    venues: tuple[str, ...]
    authors: tuple[str, ...]
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    lang: str | None
    sql_where: str
    lance_where: str
    applied: dict[str, object] = field(default_factory=dict)


def _multi(params: Mapping[str, list[str]], key: str) -> list[str]:
    values = params.get(key) or []
    return [v.strip() for v in values if v and v.strip()]


def _parse_year(raw: str) -> YearRange:
    s = raw.strip()
    m = _YEAR_RANGE_RE.match(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise InvalidFilterError(f"year range reversed: {raw}")
        return YearRange(min=lo, max=hi)
    if _YEAR_SINGLE_RE.match(s):
        y = int(s)
        return YearRange(min=y, max=y)
    raise InvalidFilterError(f"unparseable year filter: {raw}")


def _validate_ident(value: str, kind: str) -> str:
    if not _IDENT_RE.match(value):
        raise InvalidFilterError(f"invalid {kind} filter value: {value}")
    return value


def parse_filters(params: Mapping[str, list[str]]) -> ParsedFilters:
    year: YearRange | None = None
    year_raw = _multi(params, "filters.year")
    if year_raw:
        year = _parse_year(year_raw[0])

    venues_in = _multi(params, "filters.venue")
    venues: list[str] = []
    for v in venues_in:
        try:
            venues.append(validate_venue_filter(v))
        except ValueError as e:
            raise InvalidFilterError(f"venue filter rejected: {e}") from e

    authors = tuple(a.lower() for a in _multi(params, "filters.authors"))
    keywords = tuple(k.lower() for k in _multi(params, "filters.keywords"))
    tags = tuple(t.lower() for t in _multi(params, "filters.tags"))
    for a in authors:
        _validate_ident(a, "authors")
    for k in keywords:
        _validate_ident(k, "keywords")
    for t in tags:
        _validate_ident(t, "tags")

    lang_vals = _multi(params, "filters.lang")
    lang = lang_vals[0] if lang_vals else None
    if lang is not None:
        _validate_ident(lang, "lang")

    sql_parts: list[str] = []
    lance_parts: list[str] = []
    applied: dict[str, object] = {}

    if year is not None:
        sql_parts.append(
            f"CAST(p.year AS INTEGER) BETWEEN {year.min} AND {year.max}"
        )
        lance_parts.append(f"year >= {year.min} AND year <= {year.max}")
        applied["year"] = {"min": year.min, "max": year.max}

    if venues:
        quoted = ", ".join(f"'{v.replace(chr(39), chr(39)*2)}'" for v in venues)
        sql_parts.append(f"p.venue IN ({quoted})")
        lance_parts.append(
            " OR ".join(f"venue = '{v.replace(chr(39), chr(39)*2)}'" for v in venues)
        )
        applied["venues"] = list(venues)

    if lang is not None:
        sql_parts.append(f"p.output_language = '{lang}'")
        lance_parts.append(f"lang = '{lang}'")
        applied["lang"] = lang

    if authors:
        applied["authors"] = list(authors)
    if keywords:
        applied["keywords"] = list(keywords)
    if tags:
        applied["tags"] = list(tags)

    return ParsedFilters(
        year=year,
        venues=tuple(venues),
        authors=authors,
        keywords=keywords,
        tags=tags,
        lang=lang,
        sql_where=" AND ".join(sql_parts),
        lance_where=" AND ".join(lance_parts),
        applied=applied,
    )
```

Note: author/keyword/tag filters are recorded in `applied` and pushed down later by joining with their dimension tables in `retrieve_sparse.py` (Task 8); the lance_where does not include them because LanceDB chunks are denormalized with a single `authors` string, not structured.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_filters.py -v`
Expected: all nine tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/filters.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_filters.py
git commit -m "feat(advanced): parse filters.* into SQL/Lance where clauses"
```

---

### Task 6: Auth (bearer token verification)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/auth.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`

Interface contract: `verify_bearer(header_value: str | None, expected: str) -> None` raises `UnauthorizedError(reason="missing"|"invalid")` on failure, returns `None` on success. Uses `hmac.compare_digest` for constant-time compare.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.auth import verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError


def test_missing_header_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as e:
        verify_bearer(None, "secret")
    assert e.value.reason == "missing"


def test_empty_header_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as e:
        verify_bearer("", "secret")
    assert e.value.reason == "missing"


def test_malformed_prefix_raises_missing() -> None:
    with pytest.raises(UnauthorizedError) as e:
        verify_bearer("Basic xyz", "secret")
    assert e.value.reason == "missing"


def test_wrong_token_raises_invalid() -> None:
    with pytest.raises(UnauthorizedError) as e:
        verify_bearer("Bearer wrong", "secret")
    assert e.value.reason == "invalid"


def test_correct_token_returns_none() -> None:
    assert verify_bearer("Bearer secret", "secret") is None


def test_constant_time_compare_not_substring() -> None:
    # "sec" is a prefix; must still fail invalid
    with pytest.raises(UnauthorizedError) as e:
        verify_bearer("Bearer sec", "secret")
    assert e.value.reason == "invalid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement auth module**

Create `python/deepresearch_flow/paper/snapshot/advanced/auth.py`:

```python
"""Bearer token verification for advanced search endpoints."""

from __future__ import annotations

import hmac

from deepresearch_flow.paper.snapshot.advanced.errors import UnauthorizedError

_BEARER_PREFIX = "Bearer "


def verify_bearer(header_value: str | None, expected: str) -> None:
    """Raise UnauthorizedError on missing/malformed/invalid token."""
    if not header_value or not header_value.startswith(_BEARER_PREFIX):
        raise UnauthorizedError("missing")
    candidate = header_value[len(_BEARER_PREFIX):]
    if not hmac.compare_digest(candidate, expected):
        raise UnauthorizedError("invalid")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py -v`
Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/auth.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py
git commit -m "feat(advanced): bearer token verification with constant-time compare"
```

---

### Task 7: Dense retrieval (embed + LanceDB query)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/retrieve_dense.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_dense.py`

Interface contract: `async dense_retrieve(*, query_text: str, lance_db, embedding_route_pool, client, dimensions: int, top_k: int, lance_where: str) -> list[ChunkHit]` returns chunk-level hits sorted by cosine similarity descending. `ChunkHit` is a frozen dataclass `{chunk_id, paper_id, dense_score, chunk_text, field_name, template_tag, chunk_type, chunk_index, lang, vector}`.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_dense.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import (
    ChunkHit,
    dense_retrieve,
)


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows
        self.received_where = None
        self.received_vector = None
        self.received_top_k = None


def _fake_query_vector(db, vector, *, top_k, where=None):
    db.received_vector = list(vector)
    db.received_top_k = top_k
    db.received_where = where
    return list(db.rows)


def test_returns_chunk_hits(monkeypatch) -> None:
    async def fake_embed(**kwargs):
        class R:
            vectors = [[0.1, 0.2, 0.3]]
            model = "m"
            usage_tokens = 0
        return R()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense as mod
    monkeypatch.setattr(mod, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(mod, "query_vector", _fake_query_vector)

    db = _FakeLance(rows=[
        {
            "id": "p1_simple_content_0",
            "doc_id": "p1",
            "text": "...",
            "field_name": "simple/content",
            "template_tag": "simple",
            "chunk_type": "content",
            "chunk_index": 0,
            "lang": "en",
            "_distance": 0.2,
            "vector": [0.1, 0.2, 0.3],
        }
    ])

    hits = asyncio.run(
        dense_retrieve(
            query_text="q",
            lance_db=db,
            embedding_route_pool=object(),
            client=object(),
            dimensions=3,
            top_k=10,
            lance_where="year = 2023",
        )
    )
    assert db.received_top_k == 10
    assert db.received_where == "year = 2023"
    assert len(hits) == 1
    h = hits[0]
    assert isinstance(h, ChunkHit)
    assert h.paper_id == "p1"
    assert h.chunk_id == "p1_simple_content_0"
    assert h.dense_score == pytest.approx(0.8)  # 1.0 - 0.2
    assert h.field_name == "simple/content"
    assert h.chunk_type == "content"


def test_empty_lance_where_is_not_sent(monkeypatch) -> None:
    async def fake_embed(**kwargs):
        class R:
            vectors = [[0.0]]
            model = "m"
            usage_tokens = 0
        return R()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense as mod
    monkeypatch.setattr(mod, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(mod, "query_vector", _fake_query_vector)
    db = _FakeLance(rows=[])
    asyncio.run(
        dense_retrieve(
            query_text="q",
            lance_db=db,
            embedding_route_pool=object(),
            client=object(),
            dimensions=1,
            top_k=5,
            lance_where="",
        )
    )
    assert db.received_where is None or db.received_where == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_dense.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement dense retrieval**

Create `python/deepresearch_flow/paper/snapshot/advanced/retrieve_dense.py`:

```python
"""Dense retrieval: embed the query, query LanceDB, return chunk hits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.embedding import call_embedding_with_route_pool
from deepresearch_flow.paper.vector_store import query_vector


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    paper_id: str
    dense_score: float
    chunk_text: str
    field_name: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    lang: str
    vector: tuple[float, ...]


async def dense_retrieve(
    *,
    query_text: str,
    lance_db: Any,
    embedding_route_pool: Any,
    client: Any,
    dimensions: int,
    top_k: int,
    lance_where: str,
) -> list[ChunkHit]:
    embed_result = await call_embedding_with_route_pool(
        route_pool=embedding_route_pool,
        texts=[query_text],
        dimensions=dimensions,
        client=client,
    )
    query_vec = embed_result.vectors[0]
    where = lance_where or None
    rows = query_vector(lance_db, query_vec, top_k=top_k, where=where)
    hits: list[ChunkHit] = []
    for row in rows:
        distance = float(row.get("_distance", 0.0))
        hits.append(
            ChunkHit(
                chunk_id=str(row.get("id", "")),
                paper_id=str(row.get("doc_id", "")),
                dense_score=1.0 - distance,
                chunk_text=str(row.get("text", "")),
                field_name=str(row.get("field_name", "")),
                template_tag=str(row.get("template_tag", "")),
                chunk_type=str(row.get("chunk_type", "")),
                chunk_index=int(row.get("chunk_index", 0) or 0),
                lang=str(row.get("lang", "")),
                vector=tuple(float(v) for v in (row.get("vector") or ())),
            )
        )
    return hits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_dense.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/retrieve_dense.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_dense.py
git commit -m "feat(advanced): dense retrieval via embed + LanceDB with filter pushdown"
```

---

### Task 8: Sparse retrieval (paper_fts MATCH + BM25)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/retrieve_sparse.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_sparse.py`

Interface contract: `sparse_retrieve(*, conn, fts_expr: str, filters: ParsedFilters, top_k: int, lang: str) -> list[PaperHit]` returns paper-level hits sorted by BM25 ascending rank. `PaperHit` is a frozen dataclass `{paper_id, sparse_score}`. Applies filter pushdown via JOINs on `paper_author` / `paper_keyword` / `paper_tag` when corresponding filters are set. For `lang == "zh"` and nonempty sparse results, merges trigram-fallback paper_ids from `paper_fts_trigram`.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_sparse.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from deepresearch_flow.paper.snapshot.advanced.filters import parse_filters
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import (
    PaperHit,
    sparse_retrieve,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY,
          title TEXT, year TEXT, venue TEXT, output_language TEXT
        );
        CREATE VIRTUAL TABLE paper_fts USING fts5(
          paper_id UNINDEXED, title, summary, source, translated, metadata,
          tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE paper_fts_trigram USING fts5(
          paper_id UNINDEXED, title, venue, tokenize='trigram'
        );
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER,
          PRIMARY KEY(paper_id, author_id));

        INSERT INTO paper VALUES
          ('p1','Vision Transformer','2021','ICLR','en'),
          ('p2','ResNet Deep Residual','2016','CVPR','en'),
          ('p3','视觉模型综述','2024','journal','zh');

        INSERT INTO paper_fts (paper_id, title, summary, source, translated, metadata)
          VALUES
          ('p1','Vision Transformer','patch transformer','','','arxiv'),
          ('p2','ResNet Deep Residual','residual learning','','','cvpr'),
          ('p3','视觉模型综述','综述 视觉 transformer','','','review');

        INSERT INTO paper_fts_trigram (paper_id, title, venue) VALUES
          ('p3','视觉模型综述','journal');

        INSERT INTO author VALUES (1,'alice'),(2,'bob');
        INSERT INTO paper_author VALUES ('p1',1),('p2',2);
        """
    )
    return c


def test_returns_paper_hits_sorted(conn) -> None:
    f = parse_filters({})
    hits = sparse_retrieve(
        conn=conn,
        fts_expr="transformer",
        filters=f,
        top_k=10,
        lang="en",
    )
    assert all(isinstance(h, PaperHit) for h in hits)
    ids = [h.paper_id for h in hits]
    assert "p1" in ids


def test_applies_year_filter(conn) -> None:
    f = parse_filters({"filters.year": ["2020..2022"]})
    hits = sparse_retrieve(
        conn=conn, fts_expr="transformer", filters=f, top_k=10, lang="en"
    )
    ids = [h.paper_id for h in hits]
    assert "p1" in ids
    assert "p2" not in ids


def test_applies_author_filter(conn) -> None:
    f = parse_filters({"filters.authors": ["bob"]})
    hits = sparse_retrieve(
        conn=conn, fts_expr="residual", filters=f, top_k=10, lang="en"
    )
    ids = [h.paper_id for h in hits]
    assert ids == ["p2"]


def test_empty_fts_expr_returns_empty(conn) -> None:
    f = parse_filters({})
    hits = sparse_retrieve(
        conn=conn, fts_expr="", filters=f, top_k=10, lang="en"
    )
    assert hits == []


def test_zh_lang_merges_trigram_hits(conn) -> None:
    f = parse_filters({})
    hits = sparse_retrieve(
        conn=conn, fts_expr='"视觉"', filters=f, top_k=10, lang="zh"
    )
    ids = [h.paper_id for h in hits]
    assert "p3" in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_sparse.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement sparse retrieval**

Create `python/deepresearch_flow/paper/snapshot/advanced/retrieve_sparse.py`:

```python
"""Sparse retrieval: paper_fts MATCH + BM25 ranking + filter pushdown."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.advanced.filters import ParsedFilters


@dataclass(frozen=True)
class PaperHit:
    paper_id: str
    sparse_score: float


_BM25 = "bm25(paper_fts, 5.0, 3.0, 1.0, 1.0, 2.0)"


def sparse_retrieve(
    *,
    conn: sqlite3.Connection,
    fts_expr: str,
    filters: ParsedFilters,
    top_k: int,
    lang: str,
) -> list[PaperHit]:
    if not fts_expr:
        return []
    joins: list[str] = ["JOIN paper p ON p.paper_id = paper_fts.paper_id"]
    where_parts: list[str] = ["paper_fts MATCH ?"]
    params: list[object] = [fts_expr]

    if filters.sql_where:
        where_parts.append(filters.sql_where)

    if filters.authors:
        joins.append(
            "JOIN paper_author pa ON pa.paper_id = p.paper_id "
            "JOIN author a ON a.author_id = pa.author_id"
        )
        placeholders = ",".join("?" * len(filters.authors))
        where_parts.append(f"LOWER(a.value) IN ({placeholders})")
        params.extend(filters.authors)

    if filters.keywords:
        joins.append(
            "JOIN paper_keyword pk ON pk.paper_id = p.paper_id "
            "JOIN keyword kw ON kw.keyword_id = pk.keyword_id"
        )
        placeholders = ",".join("?" * len(filters.keywords))
        where_parts.append(f"LOWER(kw.value) IN ({placeholders})")
        params.extend(filters.keywords)

    if filters.tags:
        joins.append(
            "JOIN paper_tag pt ON pt.paper_id = p.paper_id "
            "JOIN tag t ON t.tag_id = pt.tag_id"
        )
        placeholders = ",".join("?" * len(filters.tags))
        where_parts.append(f"LOWER(t.value) IN ({placeholders})")
        params.extend(filters.tags)

    sql = (
        f"SELECT paper_fts.paper_id AS paper_id, {_BM25} AS rank "
        f"FROM paper_fts "
        + " ".join(joins)
        + " WHERE "
        + " AND ".join(where_parts)
        + " GROUP BY paper_fts.paper_id "
        + " ORDER BY rank ASC LIMIT ?"
    )
    params.append(top_k)

    hits: dict[str, float] = {}
    for row in conn.execute(sql, params):
        hits[str(row["paper_id"])] = float(row["rank"])

    # zh fallback via trigram — only for zh queries; merge paper_ids not already hit
    if lang == "zh":
        try:
            tri_sql = (
                "SELECT paper_id, bm25(paper_fts_trigram) AS rank "
                "FROM paper_fts_trigram WHERE paper_fts_trigram MATCH ? "
                "ORDER BY rank ASC LIMIT ?"
            )
            for row in conn.execute(tri_sql, [fts_expr, top_k]):
                pid = str(row["paper_id"])
                if pid not in hits:
                    hits[pid] = float(row["rank"])
        except sqlite3.Error:
            pass  # trigram table absent or malformed — skip silently

    # BM25 returns negative or small values; ascending = most relevant.
    # Invert to positive score so higher = more relevant.
    max_rank = max(hits.values()) if hits else 0.0
    normalized = [
        PaperHit(paper_id=pid, sparse_score=(max_rank - rank))
        for pid, rank in hits.items()
    ]
    normalized.sort(key=lambda h: h.sparse_score, reverse=True)
    return normalized[:top_k]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_sparse.py -v`
Expected: five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/retrieve_sparse.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_retrieve_sparse.py
git commit -m "feat(advanced): sparse retrieval via paper_fts BM25 with filter pushdown"
```

---

### Task 9: RRF fusion (paper level)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/fusion.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_fusion.py`

Interface contract: `fuse_paper_level(*, dense_chunks: list[ChunkHit], sparse_papers: list[PaperHit], k: int, w_dense: float, w_sparse: float) -> list[FusedPaper]`. Aggregates dense chunks to paper-level via `max(dense_score)`, then applies weighted RRF. `FusedPaper` is `{paper_id, fused_score, paper_dense_score: float | None, paper_sparse_score: float | None}`. Stable for tied ranks (sorted by paper_id).

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_fusion.py`:

```python
from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.fusion import (
    FusedPaper,
    fuse_paper_level,
)
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import PaperHit


def _chunk(pid: str, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=f"{pid}_c0", paper_id=pid, dense_score=score,
        chunk_text="", field_name="", template_tag="", chunk_type="",
        chunk_index=0, lang="", vector=(),
    )


def test_deterministic_on_fixed_input() -> None:
    dense = [_chunk("p1", 0.9), _chunk("p2", 0.8), _chunk("p3", 0.7)]
    sparse = [
        PaperHit("p2", 10.0),
        PaperHit("p3", 5.0),
        PaperHit("p4", 1.0),
    ]
    out = fuse_paper_level(
        dense_chunks=dense, sparse_papers=sparse, k=60, w_dense=1.0, w_sparse=1.0
    )
    assert all(isinstance(f, FusedPaper) for f in out)
    ids = [f.paper_id for f in out]
    # p2 is top of sparse AND #2 dense → should rank high
    assert ids[0] == "p2"


def test_dense_only_channel() -> None:
    dense = [_chunk("p1", 0.5)]
    out = fuse_paper_level(
        dense_chunks=dense, sparse_papers=[], k=60, w_dense=1.0, w_sparse=1.0
    )
    assert len(out) == 1
    assert out[0].paper_id == "p1"
    assert out[0].paper_dense_score == 0.5
    assert out[0].paper_sparse_score is None


def test_sparse_only_channel() -> None:
    out = fuse_paper_level(
        dense_chunks=[],
        sparse_papers=[PaperHit("p9", 3.0)],
        k=60, w_dense=1.0, w_sparse=1.0,
    )
    assert len(out) == 1 and out[0].paper_id == "p9"
    assert out[0].paper_dense_score is None
    assert out[0].paper_sparse_score == 3.0


def test_aggregates_multiple_chunks_per_paper() -> None:
    dense = [_chunk("p1", 0.3), _chunk("p1", 0.8), _chunk("p1", 0.5)]
    out = fuse_paper_level(
        dense_chunks=dense, sparse_papers=[], k=60, w_dense=1.0, w_sparse=1.0
    )
    assert out[0].paper_dense_score == 0.8


def test_tied_ranks_stable_order() -> None:
    # Two papers with identical fused scores → sort by paper_id
    dense = [_chunk("pB", 0.5), _chunk("pA", 0.5)]
    out = fuse_paper_level(
        dense_chunks=dense, sparse_papers=[], k=60, w_dense=1.0, w_sparse=1.0
    )
    assert [f.paper_id for f in out] == ["pA", "pB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_fusion.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement fusion module**

Create `python/deepresearch_flow/paper/snapshot/advanced/fusion.py`:

```python
"""Paper-level RRF fusion over dense chunk hits and sparse paper hits."""

from __future__ import annotations

from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit
from deepresearch_flow.paper.snapshot.advanced.retrieve_sparse import PaperHit


@dataclass(frozen=True)
class FusedPaper:
    paper_id: str
    fused_score: float
    paper_dense_score: float | None
    paper_sparse_score: float | None


def fuse_paper_level(
    *,
    dense_chunks: list[ChunkHit],
    sparse_papers: list[PaperHit],
    k: int,
    w_dense: float,
    w_sparse: float,
) -> list[FusedPaper]:
    # Aggregate dense to paper-level via max(dense_score)
    paper_dense: dict[str, float] = {}
    for hit in dense_chunks:
        cur = paper_dense.get(hit.paper_id)
        if cur is None or hit.dense_score > cur:
            paper_dense[hit.paper_id] = hit.dense_score

    paper_sparse: dict[str, float] = {h.paper_id: h.sparse_score for h in sparse_papers}

    # Build ranked lists (descending by score, stable by paper_id)
    dense_ranked = sorted(
        paper_dense.items(), key=lambda x: (-x[1], x[0])
    )
    sparse_ranked = sorted(
        paper_sparse.items(), key=lambda x: (-x[1], x[0])
    )

    scores: dict[str, float] = {}
    for rank, (pid, _s) in enumerate(dense_ranked, start=1):
        scores[pid] = scores.get(pid, 0.0) + w_dense / (k + rank)
    for rank, (pid, _s) in enumerate(sparse_ranked, start=1):
        scores[pid] = scores.get(pid, 0.0) + w_sparse / (k + rank)

    out = [
        FusedPaper(
            paper_id=pid,
            fused_score=score,
            paper_dense_score=paper_dense.get(pid),
            paper_sparse_score=paper_sparse.get(pid),
        )
        for pid, score in scores.items()
    ]
    out.sort(key=lambda f: (-f.fused_score, f.paper_id))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_fusion.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/fusion.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_fusion.py
git commit -m "feat(advanced): paper-level RRF fusion with weighted ranks"
```

---

### Task 10: Chunk selection (representative chunk per paper)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/chunk_select.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_chunk_select.py`

Interface contract: `select_chunks(*, fused_papers: list[FusedPaper], dense_chunks: list[ChunkHit], lance_db, max_papers: int) -> list[SelectedChunk]`. For each paper in fused order (up to `max_papers`): if it has any `ChunkHit`, pick the one with highest `dense_score`; otherwise query LanceDB for chunks matching `paper_id`, prefer `chunk_type in {"abstract","title"}`, else `chunk_index==0`, else any. `SelectedChunk` carries fused-paper info + chunk fields. Missing LanceDB → `VectorStoreUnavailableError`.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_chunk_select.py`:

```python
from __future__ import annotations

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import (
    SelectedChunk,
    select_chunks,
)
from deepresearch_flow.paper.snapshot.advanced.errors import (
    VectorStoreUnavailableError,
)
from deepresearch_flow.paper.snapshot.advanced.fusion import FusedPaper
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit


def _chunk(pid: str, cid: str, ctype: str, idx: int, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=cid, paper_id=pid, dense_score=score, chunk_text=f"t-{cid}",
        field_name="", template_tag="simple", chunk_type=ctype,
        chunk_index=idx, lang="en", vector=(0.0,),
    )


class _FakeLance:
    """Fake LanceDB: open_table().search().where()"""

    def __init__(self, rows_by_paper: dict[str, list[dict]]):
        self.rows_by_paper = rows_by_paper
        self.calls: list[str] = []

    def open_table(self, name):
        return self

    def to_list(self):
        return list(self._current)

    def search(self, *args, **kwargs):
        return self

    def where(self, clause: str):
        self.calls.append(clause)
        for pid, rows in self.rows_by_paper.items():
            if f"doc_id = '{pid}'" in clause:
                self._current = rows
                return self
        self._current = []
        return self

    def limit(self, n):
        self._current = self._current[:n]
        return self


def test_dense_chunk_picked_when_available() -> None:
    fused = [FusedPaper("p1", 0.02, paper_dense_score=0.9, paper_sparse_score=None)]
    dense = [_chunk("p1", "p1_a", "content", 2, 0.5), _chunk("p1", "p1_b", "content", 5, 0.9)]
    out = select_chunks(
        fused_papers=fused, dense_chunks=dense,
        lance_db=_FakeLance({}), max_papers=10,
    )
    assert len(out) == 1
    assert isinstance(out[0], SelectedChunk)
    assert out[0].chunk_id == "p1_b"


def test_sparse_only_fetches_abstract_from_lance() -> None:
    fused = [FusedPaper("p1", 0.01, paper_dense_score=None, paper_sparse_score=4.0)]
    lance = _FakeLance(rows_by_paper={
        "p1": [
            {"id": "p1_simple_content_3", "doc_id": "p1", "text": "body",
             "field_name": "simple/content", "template_tag": "simple",
             "chunk_type": "content", "chunk_index": 3, "lang": "en",
             "vector": [0.0]},
            {"id": "p1_simple_abstract_0", "doc_id": "p1", "text": "abs",
             "field_name": "simple/abstract", "template_tag": "simple",
             "chunk_type": "abstract", "chunk_index": 0, "lang": "en",
             "vector": [0.0]},
        ]
    })
    out = select_chunks(
        fused_papers=fused, dense_chunks=[], lance_db=lance, max_papers=10,
    )
    assert out[0].chunk_type == "abstract"


def test_falls_back_to_index_zero() -> None:
    fused = [FusedPaper("p2", 0.01, None, 1.0)]
    lance = _FakeLance(rows_by_paper={
        "p2": [
            {"id": "p2_simple_content_5", "doc_id": "p2", "text": "x",
             "field_name": "simple/content", "template_tag": "simple",
             "chunk_type": "content", "chunk_index": 5, "lang": "en",
             "vector": [0.0]},
            {"id": "p2_simple_content_0", "doc_id": "p2", "text": "zero",
             "field_name": "simple/content", "template_tag": "simple",
             "chunk_type": "content", "chunk_index": 0, "lang": "en",
             "vector": [0.0]},
        ]
    })
    out = select_chunks(
        fused_papers=fused, dense_chunks=[], lance_db=lance, max_papers=10,
    )
    assert out[0].chunk_index == 0


def test_lance_failure_raises() -> None:
    class _Bad:
        def open_table(self, name):
            raise RuntimeError("cannot open")

    fused = [FusedPaper("p2", 0.01, None, 1.0)]
    with pytest.raises(VectorStoreUnavailableError):
        select_chunks(
            fused_papers=fused, dense_chunks=[], lance_db=_Bad(), max_papers=10,
        )


def test_max_papers_truncates() -> None:
    fused = [
        FusedPaper(f"p{i}", 1.0 / (i + 1), paper_dense_score=0.5, paper_sparse_score=None)
        for i in range(10)
    ]
    dense = [_chunk(f"p{i}", f"p{i}_c0", "content", 0, 0.5) for i in range(10)]
    out = select_chunks(
        fused_papers=fused, dense_chunks=dense, lance_db=_FakeLance({}), max_papers=3,
    )
    assert len(out) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_chunk_select.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement chunk selection**

Create `python/deepresearch_flow/paper/snapshot/advanced/chunk_select.py`:

```python
"""Stage 4.5: pick one representative chunk per fused paper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.errors import VectorStoreUnavailableError
from deepresearch_flow.paper.snapshot.advanced.fusion import FusedPaper
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit

_PREFERRED = ("abstract", "title")
_TABLE = "paper_chunks"


@dataclass(frozen=True)
class SelectedChunk:
    paper_id: str
    chunk_id: str
    chunk_text: str
    field_name: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    lang: str
    vector: tuple[float, ...]
    fused_score: float
    paper_dense_score: float | None
    paper_sparse_score: float | None
    dense_score: float | None  # per-chunk dense score if from dense hit; else None


def _score_for_preference(row: dict[str, Any]) -> tuple[int, int]:
    ctype = str(row.get("chunk_type", ""))
    idx = int(row.get("chunk_index", 0) or 0)
    pref = _PREFERRED.index(ctype) if ctype in _PREFERRED else len(_PREFERRED)
    return (pref, idx)


def _lookup_from_lance(lance_db: Any, paper_id: str) -> dict[str, Any] | None:
    try:
        tbl = lance_db.open_table(_TABLE)
        rows = list(
            tbl.search()
            .where(f"doc_id = '{paper_id.replace(chr(39), chr(39) * 2)}'")
            .limit(32)
            .to_list()
        )
    except Exception as exc:
        raise VectorStoreUnavailableError(str(exc)) from exc
    if not rows:
        return None
    rows.sort(key=_score_for_preference)
    return rows[0]


def select_chunks(
    *,
    fused_papers: list[FusedPaper],
    dense_chunks: list[ChunkHit],
    lance_db: Any,
    max_papers: int,
) -> list[SelectedChunk]:
    # Group dense hits by paper; pick best-score chunk per paper
    dense_by_paper: dict[str, ChunkHit] = {}
    for hit in dense_chunks:
        cur = dense_by_paper.get(hit.paper_id)
        if cur is None or hit.dense_score > cur.dense_score:
            dense_by_paper[hit.paper_id] = hit

    out: list[SelectedChunk] = []
    for fp in fused_papers[:max_papers]:
        dense_hit = dense_by_paper.get(fp.paper_id)
        if dense_hit is not None:
            out.append(
                SelectedChunk(
                    paper_id=fp.paper_id,
                    chunk_id=dense_hit.chunk_id,
                    chunk_text=dense_hit.chunk_text,
                    field_name=dense_hit.field_name,
                    template_tag=dense_hit.template_tag,
                    chunk_type=dense_hit.chunk_type,
                    chunk_index=dense_hit.chunk_index,
                    lang=dense_hit.lang,
                    vector=dense_hit.vector,
                    fused_score=fp.fused_score,
                    paper_dense_score=fp.paper_dense_score,
                    paper_sparse_score=fp.paper_sparse_score,
                    dense_score=dense_hit.dense_score,
                )
            )
            continue
        row = _lookup_from_lance(lance_db, fp.paper_id)
        if row is None:
            continue
        out.append(
            SelectedChunk(
                paper_id=fp.paper_id,
                chunk_id=str(row.get("id", "")),
                chunk_text=str(row.get("text", "")),
                field_name=str(row.get("field_name", "")),
                template_tag=str(row.get("template_tag", "")),
                chunk_type=str(row.get("chunk_type", "")),
                chunk_index=int(row.get("chunk_index", 0) or 0),
                lang=str(row.get("lang", "")),
                vector=tuple(float(v) for v in (row.get("vector") or ())),
                fused_score=fp.fused_score,
                paper_dense_score=fp.paper_dense_score,
                paper_sparse_score=fp.paper_sparse_score,
                dense_score=None,
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_chunk_select.py -v`
Expected: five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/chunk_select.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_chunk_select.py
git commit -m "feat(advanced): chunk selection (best-dense or abstract-preferring lance lookup)"
```

---

### Task 11: Dedup (content hash + cosine)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/dedup.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_dedup.py`

Interface contract: `dedup(selected: list[SelectedChunk], *, cosine_threshold: float) -> list[SelectedChunk]`. (1) Collapse by identical chunk `chunk_id` (safety) or same `chunk_text` content hash (SHA-256), keeping highest `fused_score`. (2) For remaining, if any two chunks have cosine similarity ≥ threshold (using their `vector` tuple), collapse to the higher-fused-score one.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_dedup.py`:

```python
from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.dedup import dedup


def _sc(cid: str, pid: str, text: str, vec: tuple[float, ...], fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=pid, chunk_id=cid, chunk_text=text, field_name="",
        template_tag="simple", chunk_type="content", chunk_index=0, lang="en",
        vector=vec, fused_score=fused, paper_dense_score=None,
        paper_sparse_score=None, dense_score=None,
    )


def test_content_hash_collapses_keeps_higher_fused() -> None:
    a = _sc("a", "p1", "same text", (1.0, 0.0), 0.1)
    b = _sc("b", "p2", "same text", (0.0, 1.0), 0.5)
    out = dedup([a, b], cosine_threshold=0.95)
    assert len(out) == 1
    assert out[0].chunk_id == "b"


def test_cosine_collapses_near_duplicates() -> None:
    a = _sc("a", "p1", "text1", (1.0, 0.0, 0.0), 0.2)
    b = _sc("b", "p2", "text2", (0.99, 0.1, 0.0), 0.6)
    out = dedup([a, b], cosine_threshold=0.95)
    assert len(out) == 1
    assert out[0].chunk_id == "b"


def test_unrelated_chunks_preserved() -> None:
    a = _sc("a", "p1", "t1", (1.0, 0.0, 0.0), 0.3)
    b = _sc("b", "p2", "t2", (0.0, 1.0, 0.0), 0.4)
    out = dedup([a, b], cosine_threshold=0.95)
    assert len(out) == 2


def test_empty_input_returns_empty() -> None:
    assert dedup([], cosine_threshold=0.95) == []


def test_zero_vectors_not_treated_as_similar() -> None:
    a = _sc("a", "p1", "t1", (0.0, 0.0), 0.3)
    b = _sc("b", "p2", "t2", (0.0, 0.0), 0.4)
    out = dedup([a, b], cosine_threshold=0.95)
    # with zero vectors cosine is undefined; should NOT collapse
    assert len(out) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_dedup.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement dedup**

Create `python/deepresearch_flow/paper/snapshot/advanced/dedup.py`:

```python
"""Stage 5: content_hash dedup + cosine near-duplicate collapse."""

from __future__ import annotations

import hashlib
import math

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def dedup(
    selected: list[SelectedChunk],
    *,
    cosine_threshold: float,
) -> list[SelectedChunk]:
    # Phase 1: hash dedup (keep highest fused per hash)
    by_hash: dict[str, SelectedChunk] = {}
    for s in selected:
        h = _hash(s.chunk_text)
        cur = by_hash.get(h)
        if cur is None or s.fused_score > cur.fused_score:
            by_hash[h] = s

    phase1 = sorted(by_hash.values(), key=lambda x: -x.fused_score)

    # Phase 2: cosine collapse on top-50
    kept: list[SelectedChunk] = []
    for cand in phase1:
        collapse = False
        for existing in kept:
            sim = _cosine(cand.vector, existing.vector)
            if sim is not None and sim >= cosine_threshold:
                # keep whichever has higher fused_score
                if cand.fused_score > existing.fused_score:
                    # replace existing with cand
                    kept.remove(existing)
                    kept.append(cand)
                collapse = True
                break
        if not collapse:
            kept.append(cand)
    kept.sort(key=lambda x: -x.fused_score)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_dedup.py -v`
Expected: five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/dedup.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_dedup.py
git commit -m "feat(advanced): dedup by content_hash then cosine collapse"
```

---

### Task 12: Rerank adapter (with timeout)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/rerank_adapter.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_rerank_adapter.py`

Interface contract: `async rerank_with_timeout(*, reranker, query: str, chunks: list[SelectedChunk], top_n: int, timeout_ms: int, client) -> RerankOutcome`. `RerankOutcome` is `{success: bool, reason: str | None, chunks: list[SelectedChunk with reranker score set]}`. On timeout or any exception → `success=False, reason="reranker_failed"`, returns input unchanged up to `top_n`.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_rerank_adapter.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.rerank_adapter import (
    RerankOutcome,
    rerank_with_timeout,
)


def _sc(cid: str, fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=cid, chunk_id=f"{cid}_c", chunk_text=f"t-{cid}",
        field_name="", template_tag="", chunk_type="", chunk_index=0, lang="en",
        vector=(0.0,), fused_score=fused,
        paper_dense_score=None, paper_sparse_score=None, dense_score=None,
    )


class _FakeReranker:
    def __init__(self, indices, scores, *, sleep=0.0, raises=None):
        self.indices = indices
        self.scores = scores
        self.sleep = sleep
        self.raises = raises

    async def rerank(self, query, documents, *, top_n, client):
        if self.raises:
            raise self.raises
        if self.sleep:
            await asyncio.sleep(self.sleep)
        class R:
            pass
        r = R()
        r.indices = list(self.indices)
        r.scores = list(self.scores)
        return r


def test_happy_path_attaches_reranker_scores() -> None:
    chunks = [_sc("p1", 0.1), _sc("p2", 0.3), _sc("p3", 0.2)]
    reranker = _FakeReranker(indices=[1, 2, 0], scores=[0.9, 0.5, 0.1])
    out = asyncio.run(
        rerank_with_timeout(
            reranker=reranker, query="q", chunks=chunks, top_n=2,
            timeout_ms=5000, client=object(),
        )
    )
    assert isinstance(out, RerankOutcome)
    assert out.success is True
    assert out.reason is None
    assert len(out.chunks) == 2
    assert out.chunks[0].chunk_id == "p2_c"
    assert out.chunks[0].dense_score is None  # not touched
    # reranker score propagation is not on SelectedChunk; out payload includes it
    assert out.scores[0] == pytest.approx(0.9)


def test_timeout_returns_degraded() -> None:
    chunks = [_sc("p1", 0.1)]
    reranker = _FakeReranker(indices=[0], scores=[0.5], sleep=0.2)
    out = asyncio.run(
        rerank_with_timeout(
            reranker=reranker, query="q", chunks=chunks, top_n=1,
            timeout_ms=50, client=object(),
        )
    )
    assert out.success is False
    assert out.reason == "reranker_failed"
    assert out.chunks == chunks
    assert out.scores == []


def test_exception_returns_degraded() -> None:
    chunks = [_sc("p1", 0.1)]
    reranker = _FakeReranker(indices=[], scores=[], raises=RuntimeError("boom"))
    out = asyncio.run(
        rerank_with_timeout(
            reranker=reranker, query="q", chunks=chunks, top_n=1,
            timeout_ms=5000, client=object(),
        )
    )
    assert out.success is False
    assert out.reason == "reranker_failed"


def test_none_reranker_returns_success_without_changes() -> None:
    chunks = [_sc("p1", 0.3), _sc("p2", 0.1)]
    out = asyncio.run(
        rerank_with_timeout(
            reranker=None, query="q", chunks=chunks, top_n=10,
            timeout_ms=5000, client=object(),
        )
    )
    assert out.success is True
    assert out.reason is None
    assert out.chunks == chunks
    assert out.scores == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_rerank_adapter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement rerank adapter**

Create `python/deepresearch_flow/paper/snapshot/advanced/rerank_adapter.py`:

```python
"""Rerank adapter: wraps RoutedReranker with timeout + fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


@dataclass(frozen=True)
class RerankOutcome:
    success: bool
    reason: str | None
    chunks: list[SelectedChunk]
    scores: list[float]


async def rerank_with_timeout(
    *,
    reranker: Any | None,
    query: str,
    chunks: list[SelectedChunk],
    top_n: int,
    timeout_ms: int,
    client: Any,
) -> RerankOutcome:
    if reranker is None or not chunks:
        return RerankOutcome(success=True, reason=None, chunks=chunks, scores=[])

    documents = [c.chunk_text for c in chunks]
    try:
        result = await asyncio.wait_for(
            reranker.rerank(query, documents, top_n=top_n, client=client),
            timeout=timeout_ms / 1000.0,
        )
    except (asyncio.TimeoutError, Exception):
        return RerankOutcome(
            success=False, reason="reranker_failed",
            chunks=chunks, scores=[],
        )

    ranked: list[SelectedChunk] = []
    scores: list[float] = []
    for idx, score in zip(result.indices, result.scores):
        if 0 <= idx < len(chunks):
            ranked.append(chunks[idx])
            scores.append(float(score))
        if len(ranked) >= top_n:
            break
    return RerankOutcome(success=True, reason=None, chunks=ranked, scores=scores)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_rerank_adapter.py -v`
Expected: four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/rerank_adapter.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_rerank_adapter.py
git commit -m "feat(advanced): rerank adapter with timeout and degradation fallback"
```

---

### Task 13: MMR selection

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/mmr.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_mmr.py`

Interface contract: `mmr_select(chunks: list[SelectedChunk], *, relevance_scores: list[float] | None, lambda_: float, top_n: int) -> list[SelectedChunk]`. `relevance_scores[i]` is used when provided (from reranker); otherwise falls back to each chunk's `fused_score`. Similarity = cosine over `chunk.vector`. λ=1.0 → pure relevance (fast path). λ=0.0 → pure diversity. Ties broken by insertion order (stable).

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_mmr.py`:

```python
from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.mmr import mmr_select


def _sc(cid: str, vec: tuple[float, ...], fused: float) -> SelectedChunk:
    return SelectedChunk(
        paper_id=cid, chunk_id=f"{cid}_c", chunk_text="", field_name="",
        template_tag="", chunk_type="", chunk_index=0, lang="en",
        vector=vec, fused_score=fused,
        paper_dense_score=None, paper_sparse_score=None, dense_score=None,
    )


def test_lambda_one_is_pure_relevance() -> None:
    a = _sc("a", (1.0, 0.0), 0.3)
    b = _sc("b", (0.9, 0.1), 0.5)
    c = _sc("c", (0.0, 1.0), 0.4)
    out = mmr_select([a, b, c], relevance_scores=None, lambda_=1.0, top_n=3)
    assert [x.paper_id for x in out] == ["b", "c", "a"]


def test_lambda_zero_prefers_diversity() -> None:
    a = _sc("a", (1.0, 0.0), 0.9)
    b = _sc("b", (0.99, 0.01), 0.8)
    c = _sc("c", (0.0, 1.0), 0.1)
    # λ=0: first pick is arbitrary (highest relevance still breaks tie at step 1);
    # second pick must be the most dissimilar from first
    out = mmr_select([a, b, c], relevance_scores=None, lambda_=0.0, top_n=2)
    assert out[0].paper_id == "a"
    assert out[1].paper_id == "c"


def test_uses_reranker_scores_when_provided() -> None:
    a = _sc("a", (1.0, 0.0), 0.9)
    b = _sc("b", (0.0, 1.0), 0.1)
    # Reranker reverses: b is more relevant
    out = mmr_select(
        [a, b], relevance_scores=[0.1, 0.9], lambda_=1.0, top_n=2,
    )
    assert [x.paper_id for x in out] == ["b", "a"]


def test_stable_tie_break() -> None:
    a = _sc("a", (0.1, 0.0), 0.5)
    b = _sc("b", (0.2, 0.0), 0.5)
    out = mmr_select([a, b], relevance_scores=None, lambda_=1.0, top_n=2)
    assert [x.paper_id for x in out] == ["a", "b"]


def test_top_n_truncates() -> None:
    chunks = [_sc(f"p{i}", (float(i),), float(i)) for i in range(5)]
    out = mmr_select(chunks, relevance_scores=None, lambda_=0.5, top_n=2)
    assert len(out) == 2


def test_empty_input_returns_empty() -> None:
    assert mmr_select([], relevance_scores=None, lambda_=0.5, top_n=10) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_mmr.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement MMR**

Create `python/deepresearch_flow/paper/snapshot/advanced/mmr.py`:

```python
"""Stage 7: Maximal Marginal Relevance selection."""

from __future__ import annotations

import math

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def mmr_select(
    chunks: list[SelectedChunk],
    *,
    relevance_scores: list[float] | None,
    lambda_: float,
    top_n: int,
) -> list[SelectedChunk]:
    if not chunks or top_n <= 0:
        return []
    rel = relevance_scores if relevance_scores is not None else [c.fused_score for c in chunks]
    if len(rel) != len(chunks):
        rel = [c.fused_score for c in chunks]

    if lambda_ >= 1.0:
        indexed = sorted(
            range(len(chunks)), key=lambda i: (-rel[i], i)
        )
        return [chunks[i] for i in indexed[:top_n]]

    remaining = list(range(len(chunks)))
    selected: list[int] = []
    while remaining and len(selected) < top_n:
        best_i = None
        best_score = None
        for i in remaining:
            if not selected:
                score = lambda_ * rel[i]
            else:
                max_sim = max(
                    _cosine(chunks[i].vector, chunks[j].vector) for j in selected
                )
                score = lambda_ * rel[i] - (1.0 - lambda_) * max_sim
            if best_score is None or score > best_score:
                best_score = score
                best_i = i
        assert best_i is not None
        selected.append(best_i)
        remaining.remove(best_i)
    return [chunks[i] for i in selected]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_mmr.py -v`
Expected: six tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/mmr.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_mmr.py
git commit -m "feat(advanced): MMR selection with tunable lambda"
```

---

### Task 14: Response assembly (paper metadata hydration)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/response.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_response.py`

Interface contract: `assemble_response(*, chunks: list[SelectedChunk], rerank_scores: list[float], conn, rerank_applied: bool, mmr_applied: bool, mmr_lambda: float, fusion_label: str, embedding_model: str, embedding_dimensions: int, reranker_model: str | None, query_raw: str, query_normalized: str, applied_filters: dict, counts: dict, latency_ms: dict, trace_id: str, degraded: bool, degradation_reason: str | None) -> dict` builds the full success JSON shape documented in spec §3.2.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_response.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk
from deepresearch_flow.paper.snapshot.advanced.response import assemble_response


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE paper (
          paper_id TEXT PRIMARY KEY, title TEXT, year TEXT, venue TEXT,
          source_hash TEXT, doi TEXT
        );
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER,
          PRIMARY KEY(paper_id, author_id));
        INSERT INTO paper VALUES ('p1','Vision Transformer','2021','ICLR','abc123','10.x');
        INSERT INTO author VALUES (1,'Dosovitskiy A.'),(2,'Kolesnikov A.');
        INSERT INTO paper_author VALUES ('p1',1),('p1',2);
        """
    )
    return c


def _sc(pid: str, cid: str, fused: float, *, dense: float | None = None,
        sparse: float | None = None, vec: tuple[float, ...] = (0.0,)) -> SelectedChunk:
    return SelectedChunk(
        paper_id=pid, chunk_id=cid, chunk_text="body",
        field_name="simple/content", template_tag="simple",
        chunk_type="content", chunk_index=0, lang="en",
        vector=vec, fused_score=fused,
        paper_dense_score=dense, paper_sparse_score=sparse,
        dense_score=dense,
    )


def test_success_payload_shape(conn) -> None:
    out = assemble_response(
        chunks=[_sc("p1", "p1_c0", 0.016, dense=0.84, sparse=12.37)],
        rerank_scores=[0.912],
        conn=conn,
        rerank_applied=True,
        mmr_applied=True,
        mmr_lambda=0.6,
        fusion_label="rrf",
        embedding_model="bge-m3",
        embedding_dimensions=1024,
        reranker_model="bge-reranker-v2-m3",
        query_raw="vision transformer",
        query_normalized="vision transformer",
        applied_filters={"year": {"min": 2020, "max": 2022}},
        counts={"dense_papers": 5, "sparse_papers": 3, "fused_papers": 6,
                "selected_chunks": 6, "deduped": 5, "reranked": 3, "returned": 1},
        latency_ms={"total": 100, "embed": 10},
        trace_id="tid-1",
        degraded=False,
        degradation_reason=None,
    )
    assert out["success"] is True
    assert out["trace_id"] == "tid-1"
    assert out["degraded"] is False
    assert out["degradation"] is None
    assert out["query"]["raw"] == "vision transformer"
    assert out["query"]["applied_filters"]["year"]["min"] == 2020
    results = out["results"]
    assert len(results) == 1
    r = results[0]
    assert r["paper_id"] == "p1"
    assert r["chunk_id"] == "p1_c0"
    assert r["paper"]["title"] == "Vision Transformer"
    assert r["paper"]["authors"] == ["Dosovitskiy A.", "Kolesnikov A."]
    assert r["paper"]["year"] == "2021"
    assert r["paper"]["source_hash"] == "abc123"
    assert r["scores"]["fused"] == pytest.approx(0.016)
    assert r["scores"]["reranker"] == pytest.approx(0.912)
    assert r["scores"]["final"] == pytest.approx(0.912)
    assert r["chunk"]["field_name"] == "simple/content"
    md = out["metadata"]
    assert md["fusion"] == "rrf"
    assert md["reranker"]["applied"] is True
    assert md["reranker"]["model"] == "bge-reranker-v2-m3"
    assert md["mmr"]["applied"] is True
    assert md["embedding"]["dimensions"] == 1024


def test_degraded_fields_set_when_degraded(conn) -> None:
    out = assemble_response(
        chunks=[_sc("p1", "p1_c0", 0.01)],
        rerank_scores=[],
        conn=conn,
        rerank_applied=False,
        mmr_applied=True,
        mmr_lambda=0.6,
        fusion_label="rrf",
        embedding_model="bge-m3",
        embedding_dimensions=1024,
        reranker_model=None,
        query_raw="q",
        query_normalized="q",
        applied_filters={},
        counts={},
        latency_ms={},
        trace_id="t",
        degraded=True,
        degradation_reason="reranker_failed",
    )
    assert out["degraded"] is True
    assert out["degradation"] == {"reason": "reranker_failed"}
    r = out["results"][0]
    assert "reranker" not in r["scores"]
    assert r["scores"]["final"] == pytest.approx(0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_response.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement response assembly**

Create `python/deepresearch_flow/paper/snapshot/advanced/response.py`:

```python
"""Stage 8: response payload assembly."""

from __future__ import annotations

import sqlite3
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _hydrate_papers(
    conn: sqlite3.Connection, paper_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not paper_ids:
        return {}
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT paper_id, title, year, venue, source_hash, doi "
        f"FROM paper WHERE paper_id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    papers: dict[str, dict[str, Any]] = {}
    for row in rows:
        papers[str(row["paper_id"])] = {
            "title": row["title"] or "",
            "year": str(row["year"] or ""),
            "venue": row["venue"] or "",
            "source_hash": row["source_hash"] or "",
            "doi": row["doi"] if "doi" in row.keys() else "",
            "authors": [],
        }
    author_rows = conn.execute(
        f"SELECT pa.paper_id, a.value "
        f"FROM paper_author pa JOIN author a ON a.author_id = pa.author_id "
        f"WHERE pa.paper_id IN ({placeholders}) "
        f"ORDER BY pa.paper_id, a.author_id",
        paper_ids,
    ).fetchall()
    for row in author_rows:
        pid = str(row["paper_id"])
        if pid in papers:
            papers[pid]["authors"].append(row["value"])
    return papers


def assemble_response(
    *,
    chunks: list[SelectedChunk],
    rerank_scores: list[float],
    conn: sqlite3.Connection,
    rerank_applied: bool,
    mmr_applied: bool,
    mmr_lambda: float,
    fusion_label: str,
    embedding_model: str,
    embedding_dimensions: int,
    reranker_model: str | None,
    query_raw: str,
    query_normalized: str,
    applied_filters: dict[str, Any],
    counts: dict[str, int],
    latency_ms: dict[str, int],
    trace_id: str,
    degraded: bool,
    degradation_reason: str | None,
) -> dict[str, Any]:
    paper_ids = [c.paper_id for c in chunks]
    papers = _hydrate_papers(conn, paper_ids)

    results: list[dict[str, Any]] = []
    for idx, c in enumerate(chunks):
        scores: dict[str, Any] = {"fused": c.fused_score}
        if c.dense_score is not None:
            scores["dense"] = c.dense_score
        if c.paper_sparse_score is not None:
            scores["sparse"] = c.paper_sparse_score
        if rerank_applied and idx < len(rerank_scores):
            scores["reranker"] = rerank_scores[idx]
            scores["final"] = rerank_scores[idx]
        else:
            scores["final"] = c.fused_score

        paper_meta = papers.get(c.paper_id, {
            "title": "", "year": "", "venue": "",
            "source_hash": "", "doi": "", "authors": [],
        })
        paper_meta = {**paper_meta, "paper_id": c.paper_id}  # no-op; paper_id lives on result

        results.append({
            "chunk_id": c.chunk_id,
            "paper_id": c.paper_id,
            "paper": {
                "title": papers.get(c.paper_id, {}).get("title", ""),
                "authors": papers.get(c.paper_id, {}).get("authors", []),
                "year": papers.get(c.paper_id, {}).get("year", ""),
                "venue": papers.get(c.paper_id, {}).get("venue", ""),
                "doi": papers.get(c.paper_id, {}).get("doi", ""),
                "source_hash": papers.get(c.paper_id, {}).get("source_hash", ""),
            },
            "chunk": {
                "text": c.chunk_text,
                "field_name": c.field_name,
                "template_tag": c.template_tag,
                "chunk_type": c.chunk_type,
                "chunk_index": c.chunk_index,
                "lang": c.lang,
            },
            "scores": scores,
        })

    return {
        "success": True,
        "trace_id": trace_id,
        "query": {
            "raw": query_raw,
            "normalized": query_normalized,
            "applied_filters": applied_filters,
        },
        "results": results,
        "metadata": {
            "counts": counts,
            "fusion": fusion_label,
            "reranker": {"applied": rerank_applied, "model": reranker_model}
            if rerank_applied else {"applied": False, "model": reranker_model},
            "mmr": {"applied": mmr_applied, "lambda": mmr_lambda},
            "embedding": {
                "model": embedding_model,
                "dimensions": embedding_dimensions,
            },
            "latency_ms": latency_ms,
        },
        "degraded": degraded,
        "degradation": {"reason": degradation_reason} if degraded else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_response.py -v`
Expected: two tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/response.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_response.py
git commit -m "feat(advanced): response payload assembly with paper metadata hydration"
```

---

## Phase 3 — Backend orchestration

### Task 15: Pipeline orchestrator

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/pipeline.py`
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_pipeline.py`

Interface contract: `async run_advanced_search(*, request_spec, ctx, conn, client) -> dict` executes Stages 1–8 with proper degradation. `RequestSpec` is a frozen dataclass `{query_raw, top_n, mmr_lambda, rerank_mode, filter_params, trace_id}`. Returns the full response dict (success or degraded 200). Raises `TotalFailureError` / `VectorStoreUnavailableError` / `InvalidQueryError` / `InvalidFilterError` when no degradation is possible.

- [ ] **Step 1: Write failing test**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_pipeline.py`:

```python
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from deepresearch_flow.paper.snapshot.advanced.errors import (
    TotalFailureError,
    VectorStoreUnavailableError,
)
from deepresearch_flow.paper.snapshot.advanced.pipeline import (
    RequestSpec,
    run_advanced_search,
)


class _Ctx:
    def __init__(self, *, dense_rows, paper_rows, lance_ok=True,
                 reranker_response=None, reranker_raises=False,
                 embed_raises=False):
        self.embedding_route_pool = object()
        self.rerank_route_pool = None
        self.search_config = _SearchCfg()
        self.paper_config = _PaperCfg()
        self.lance_db = _FakeLance(paper_rows) if lance_ok else _BadLance()
        self._dense_rows = dense_rows
        self._reranker_response = reranker_response
        self._reranker_raises = reranker_raises
        self._embed_raises = embed_raises
        self.embed_db_path = "/tmp/embed_db"


class _SearchCfg:
    advanced_rrf_k = 60
    advanced_dense_top_k = 50
    advanced_sparse_top_k = 30
    advanced_post_fusion_top_k = 50
    advanced_dedup_cosine_threshold = 0.95
    advanced_rerank_top_n = 20
    advanced_mmr_lambda_default = 0.6
    advanced_rerank_timeout_ms = 1500
    advanced_top_n_max = 50
    advanced_max_query_length = 500


class _EmbedCfg:
    default_model = "bge-m3"
    dimensions = 2
    def resolve_active(self):
        class P: default_model = "bge-m3"; default_provider = "ollama"
        class M: model_name = "bge-m3"; canonical_name = "bge-m3"; dimensions = 2
        return P(), M()


class _RerankCfg:
    enabled = True


class _PaperCfg:
    embedding = _EmbedCfg()
    rerank = _RerankCfg()


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows
    def open_table(self, name):
        return self
    def search(self, *a, **kw):
        return self
    def where(self, clause):
        return self
    def limit(self, n):
        return self
    def to_list(self):
        return list(self.rows)


class _BadLance:
    def open_table(self, name):
        raise RuntimeError("nope")


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE paper (paper_id TEXT PRIMARY KEY, title TEXT, year TEXT,
          venue TEXT, source_hash TEXT, output_language TEXT);
        CREATE VIRTUAL TABLE paper_fts USING fts5(
          paper_id UNINDEXED, title, summary, source, translated, metadata,
          tokenize='unicode61');
        CREATE VIRTUAL TABLE paper_fts_trigram USING fts5(
          paper_id UNINDEXED, title, venue, tokenize='trigram');
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER);
        INSERT INTO paper VALUES ('p1','Vision','2023','ICLR','h','en');
        INSERT INTO paper_fts(paper_id,title,summary,source,translated,metadata)
          VALUES ('p1','Vision','vision transformer','','','meta');
    """)
    return c


def _dense_row(pid: str, score_distance: float = 0.1):
    return {
        "id": f"{pid}_c0", "doc_id": pid, "_distance": score_distance,
        "text": "body", "field_name": "simple/content",
        "template_tag": "simple", "chunk_type": "content",
        "chunk_index": 0, "lang": "en", "vector": [0.5, 0.5],
    }


def test_happy_path(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter

    async def fake_embed(**kw):
        class R:
            vectors = [[0.5, 0.5]]; model = "bge-m3"; usage_tokens = 0
        return R()

    def fake_query_vector(db, vec, *, top_k, where=None):
        return [_dense_row("p1")]

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_query_vector)

    async def fake_rerank(**kw):
        class O:
            success = True; reason = None
            chunks = kw["chunks"]; scores = [0.9]
        return O()
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    ctx = _Ctx(dense_rows=[_dense_row("p1")], paper_rows=[_dense_row("p1")])
    req = RequestSpec(
        query_raw="vision", top_n=10, mmr_lambda=0.6,
        rerank_mode="auto", filter_params={}, trace_id="t-1",
    )
    out = asyncio.run(run_advanced_search(
        request_spec=req, ctx=ctx, conn=conn, client=object(),
    ))
    assert out["success"] is True
    assert out["degraded"] is False
    assert out["results"][0]["paper_id"] == "p1"


def test_dense_failure_degrades_to_sparse_only(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter

    async def raise_embed(**kw):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)

    async def fake_rerank(**kw):
        class O:
            success = True; reason = None
            chunks = kw["chunks"]; scores = [0.9 for _ in kw["chunks"]]
        return O()
    monkeypatch.setattr(rerank_adapter, "rerank_with_timeout", fake_rerank)

    ctx = _Ctx(dense_rows=[], paper_rows=[_dense_row("p1")])
    req = RequestSpec(
        query_raw="vision", top_n=10, mmr_lambda=0.6,
        rerank_mode="auto", filter_params={}, trace_id="t-2",
    )
    out = asyncio.run(run_advanced_search(
        request_spec=req, ctx=ctx, conn=conn, client=object(),
    ))
    assert out["degraded"] is True
    assert out["degradation"]["reason"] == "embedding_failed"


def test_rerank_failure_degrades(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense
    from deepresearch_flow.paper.snapshot.advanced import rerank_adapter

    async def fake_embed(**kw):
        class R: vectors=[[0.5,0.5]]; model="bge-m3"; usage_tokens=0
        return R()
    def fake_qv(db,vec,*,top_k,where=None): return [_dense_row("p1")]
    monkeypatch.setattr(retrieve_dense,"call_embedding_with_route_pool",fake_embed)
    monkeypatch.setattr(retrieve_dense,"query_vector",fake_qv)

    async def fake_rerank(**kw):
        class O:
            success=False; reason="reranker_failed"
            chunks=kw["chunks"]; scores=[]
        return O()
    monkeypatch.setattr(rerank_adapter,"rerank_with_timeout",fake_rerank)

    ctx = _Ctx(dense_rows=[], paper_rows=[])
    req = RequestSpec(
        query_raw="vision", top_n=10, mmr_lambda=0.6,
        rerank_mode="auto", filter_params={}, trace_id="t-3",
    )
    out = asyncio.run(run_advanced_search(
        request_spec=req, ctx=ctx, conn=conn, client=object(),
    ))
    assert out["degraded"] is True
    assert out["degradation"]["reason"] == "reranker_failed"


def test_total_failure_raises(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def raise_embed(**kw):
        raise RuntimeError("embed down")
    monkeypatch.setattr(retrieve_dense,"call_embedding_with_route_pool",raise_embed)

    # empty FTS — sparse produces zero rows, embed raises → total failure
    ctx = _Ctx(dense_rows=[], paper_rows=[])
    req = RequestSpec(
        query_raw="nonexistentqueryxyz", top_n=10, mmr_lambda=0.6,
        rerank_mode="auto", filter_params={}, trace_id="t-4",
    )
    with pytest.raises(TotalFailureError):
        asyncio.run(run_advanced_search(
            request_spec=req, ctx=ctx, conn=conn, client=object(),
        ))


def test_lance_unavailable_in_chunk_select(conn, monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def raise_embed(**kw):
        raise RuntimeError("embed down")
    monkeypatch.setattr(retrieve_dense,"call_embedding_with_route_pool",raise_embed)

    ctx = _Ctx(dense_rows=[], paper_rows=[], lance_ok=False)
    req = RequestSpec(
        query_raw="vision", top_n=10, mmr_lambda=0.6,
        rerank_mode="auto", filter_params={}, trace_id="t-5",
    )
    with pytest.raises(VectorStoreUnavailableError):
        asyncio.run(run_advanced_search(
            request_spec=req, ctx=ctx, conn=conn, client=object(),
        ))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_pipeline.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement pipeline**

Create `python/deepresearch_flow/paper/snapshot/advanced/pipeline.py`:

```python
"""Pipeline orchestrator: runs Stages 1-8 with degradation paths."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.advanced import (
    chunk_select,
    dedup,
    filters as filters_mod,
    fusion,
    mmr as mmr_mod,
    normalize,
    rerank_adapter,
    response as response_mod,
    retrieve_dense,
    retrieve_sparse,
)
from deepresearch_flow.paper.snapshot.advanced.errors import (
    InvalidQueryError,
    TotalFailureError,
    VectorStoreUnavailableError,
)


@dataclass(frozen=True)
class RequestSpec:
    query_raw: str
    top_n: int
    mmr_lambda: float
    rerank_mode: str  # "auto" | "always" | "never"
    filter_params: dict[str, list[str]]
    trace_id: str


def _ms() -> int:
    return int(time.monotonic() * 1000)


async def run_advanced_search(
    *,
    request_spec: RequestSpec,
    ctx: Any,
    conn: Any,
    client: Any,
) -> dict[str, Any]:
    t_total = _ms()
    cfg = ctx.search_config
    paper_cfg = ctx.paper_config

    if not request_spec.query_raw.strip():
        raise InvalidQueryError("q is empty")
    if len(request_spec.query_raw) > cfg.advanced_max_query_length:
        raise InvalidQueryError("q exceeds max length")

    # Stage 1 - normalize
    t0 = _ms()
    nq = normalize.normalize(request_spec.query_raw)
    if not nq.normalized:
        raise InvalidQueryError("q empty after normalization")
    latency: dict[str, int] = {"normalize": _ms() - t0}

    # Stage 2 - filters
    t0 = _ms()
    parsed = filters_mod.parse_filters(request_spec.filter_params)
    latency["filter"] = _ms() - t0

    embed_prov, embed_model = paper_cfg.embedding.resolve_active()
    reranker_model_name: str | None = None
    reranker = None
    if paper_cfg.rerank.enabled and ctx.rerank_route_pool is not None:
        from deepresearch_flow.paper.reranker import RoutedReranker
        reranker = RoutedReranker(route_pool=ctx.rerank_route_pool)
        rr_prov, rr_model = paper_cfg.rerank.resolve_active() if hasattr(paper_cfg.rerank, "resolve_active") else (None, None)
        if rr_model is not None:
            reranker_model_name = rr_model.model_name

    # Stage 3a/3b - parallel dense + sparse
    t0 = _ms()
    dense_task = retrieve_dense.dense_retrieve(
        query_text=nq.normalized,
        lance_db=ctx.lance_db,
        embedding_route_pool=ctx.embedding_route_pool,
        client=client,
        dimensions=paper_cfg.embedding.dimensions,
        top_k=cfg.advanced_dense_top_k,
        lance_where=parsed.lance_where,
    )
    sparse_coro = asyncio.to_thread(
        retrieve_sparse.sparse_retrieve,
        conn=conn,
        fts_expr=nq.fts_expr,
        filters=parsed,
        top_k=cfg.advanced_sparse_top_k,
        lang=nq.lang,
    )
    dense_result, sparse_result = await asyncio.gather(
        dense_task, sparse_coro, return_exceptions=True,
    )
    dense_latency = _ms() - t0
    latency["retrieve"] = dense_latency

    dense_hits: list[retrieve_dense.ChunkHit] = []
    sparse_hits: list[retrieve_sparse.PaperHit] = []
    degraded = False
    degradation_reason: str | None = None

    dense_failed = isinstance(dense_result, Exception)
    sparse_failed = isinstance(sparse_result, Exception)
    if not dense_failed:
        dense_hits = dense_result  # type: ignore[assignment]
    if not sparse_failed:
        sparse_hits = sparse_result  # type: ignore[assignment]

    if dense_failed and sparse_failed:
        raise TotalFailureError("both retrieval branches failed")

    if dense_failed and not dense_hits and not sparse_hits:
        raise TotalFailureError("embedding failed and sparse returned empty")

    if dense_failed:
        degraded = True
        degradation_reason = "embedding_failed"
    elif sparse_failed:
        degraded = True
        degradation_reason = "fts_unavailable"

    # Stage 4 - fuse
    t0 = _ms()
    fused = fusion.fuse_paper_level(
        dense_chunks=dense_hits,
        sparse_papers=sparse_hits,
        k=cfg.advanced_rrf_k,
        w_dense=1.0, w_sparse=1.0,
    )
    latency["fusion"] = _ms() - t0

    if not fused:
        raise TotalFailureError("no fused papers")

    # Stage 4.5 - chunk select
    t0 = _ms()
    selected = chunk_select.select_chunks(
        fused_papers=fused,
        dense_chunks=dense_hits,
        lance_db=ctx.lance_db,
        max_papers=cfg.advanced_post_fusion_top_k,
    )
    latency["chunk_select"] = _ms() - t0

    # Stage 5 - dedup
    t0 = _ms()
    deduped = dedup.dedup(selected, cosine_threshold=cfg.advanced_dedup_cosine_threshold)
    latency["dedup"] = _ms() - t0

    # Stage 6 - rerank
    rerank_applied = False
    rerank_scores: list[float] = []
    rerank_input = deduped[: cfg.advanced_rerank_top_n * 2] if deduped else []
    if request_spec.rerank_mode != "never" and reranker is not None and rerank_input:
        t0 = _ms()
        outcome = await rerank_adapter.rerank_with_timeout(
            reranker=reranker,
            query=nq.normalized,
            chunks=rerank_input,
            top_n=cfg.advanced_rerank_top_n,
            timeout_ms=cfg.advanced_rerank_timeout_ms,
            client=client,
        )
        latency["rerank"] = _ms() - t0
        if outcome.success:
            rerank_applied = True
            deduped = outcome.chunks
            rerank_scores = outcome.scores
        else:
            if not degraded:
                degraded = True
                degradation_reason = outcome.reason or "reranker_failed"

    # Stage 7 - MMR
    t0 = _ms()
    final_chunks = mmr_mod.mmr_select(
        deduped,
        relevance_scores=rerank_scores if rerank_applied else None,
        lambda_=request_spec.mmr_lambda,
        top_n=request_spec.top_n,
    )
    # Shrink rerank_scores to match final ordering
    final_rerank_scores: list[float] = []
    if rerank_applied and rerank_scores:
        score_by_id = {
            c.chunk_id: s for c, s in zip(deduped, rerank_scores)
        }
        final_rerank_scores = [
            score_by_id.get(c.chunk_id, c.fused_score) for c in final_chunks
        ]
    latency["mmr"] = _ms() - t0

    counts = {
        "dense_papers": len({h.paper_id for h in dense_hits}),
        "sparse_papers": len(sparse_hits),
        "fused_papers": len(fused),
        "selected_chunks": len(selected),
        "deduped": len(deduped),
        "reranked": len(deduped) if rerank_applied else 0,
        "returned": len(final_chunks),
    }
    latency["total"] = _ms() - t_total

    return response_mod.assemble_response(
        chunks=final_chunks,
        rerank_scores=final_rerank_scores,
        conn=conn,
        rerank_applied=rerank_applied,
        mmr_applied=request_spec.mmr_lambda < 1.0,
        mmr_lambda=request_spec.mmr_lambda,
        fusion_label="rrf",
        embedding_model=paper_cfg.embedding.default_model,
        embedding_dimensions=paper_cfg.embedding.dimensions,
        reranker_model=reranker_model_name,
        query_raw=request_spec.query_raw,
        query_normalized=nq.normalized,
        applied_filters=parsed.applied,
        counts=counts,
        latency_ms=latency,
        trace_id=request_spec.trace_id,
        degraded=degraded,
        degradation_reason=degradation_reason,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_pipeline.py -v`
Expected: five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/pipeline.py \
  python/deepresearch_flow/paper/snapshot/advanced/tests/test_pipeline.py
git commit -m "feat(advanced): pipeline orchestrator with degradation paths"
```

---

### Task 16: HTTP handlers

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/handler.py`

Interface contract: two Starlette handlers — `_api_verify_token(request)` returns 200/401 per §3.1; `_api_search_advanced(request)` returns full payload. Both extract `advanced_config` from `request.app.state.advanced`. Open a read-only `sqlite3.Connection` per request via `_open_ro_conn`. Map all `AdvancedSearchError` subclasses to their `http_status` + `error.code` envelope.

- [ ] **Step 1: Implement handlers**

Create `python/deepresearch_flow/paper/snapshot/advanced/handler.py`:

```python
"""Starlette HTTP handlers for advanced search."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from deepresearch_flow.paper.snapshot.advanced.auth import verify_bearer
from deepresearch_flow.paper.snapshot.advanced.errors import (
    AdvancedSearchError,
    InvalidFilterError,
    InvalidQueryError,
    UnauthorizedError,
)
from deepresearch_flow.paper.snapshot.advanced.pipeline import (
    RequestSpec,
    run_advanced_search,
)
from deepresearch_flow.paper.snapshot.common import _open_ro_conn


def _trace_id(request: Request) -> str:
    return request.headers.get("x-request-id") or uuid.uuid4().hex


def _error_response(exc: AdvancedSearchError, trace_id: str) -> JSONResponse:
    details: dict[str, Any] = {}
    if isinstance(exc, UnauthorizedError):
        details["reason"] = exc.reason
    return JSONResponse(
        {
            "success": False,
            "trace_id": trace_id,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": details,
            },
        },
        status_code=exc.http_status,
    )


async def _api_verify_token(request: Request) -> JSONResponse:
    trace_id = _trace_id(request)
    ctx = getattr(request.app.state, "advanced", None)
    if ctx is None:
        return JSONResponse(
            {"valid": False, "reason": "advanced_disabled"}, status_code=503,
            headers={"X-Request-Id": trace_id},
        )
    try:
        verify_bearer(request.headers.get("authorization"), ctx.search_access_token)
    except UnauthorizedError as exc:
        return JSONResponse(
            {"valid": False, "reason": exc.reason},
            status_code=401,
            headers={"X-Request-Id": trace_id},
        )
    return JSONResponse(
        {"valid": True}, status_code=200,
        headers={"X-Request-Id": trace_id},
    )


def _collect_filter_params(request: Request) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in request.query_params.keys():
        if key.startswith("filters."):
            result[key] = request.query_params.getlist(key)
    return result


async def _api_search_advanced(request: Request) -> JSONResponse:
    trace_id = _trace_id(request)
    ctx = getattr(request.app.state, "advanced", None)
    if ctx is None:
        return JSONResponse(
            {"success": False, "trace_id": trace_id,
             "error": {"code": "ADVANCED_DISABLED",
                       "message": "advanced search not configured",
                       "details": {}}},
            status_code=503, headers={"X-Request-Id": trace_id},
        )

    try:
        verify_bearer(request.headers.get("authorization"), ctx.search_access_token)
    except UnauthorizedError as exc:
        return _error_response(exc, trace_id)

    q = request.query_params.get("q", "")
    cfg = ctx.search_config
    try:
        top_n = int(request.query_params.get("top_n", "10"))
        if top_n < 1 or top_n > cfg.advanced_top_n_max:
            raise InvalidQueryError(
                f"top_n must be in [1, {cfg.advanced_top_n_max}]"
            )
        mmr_lambda = float(
            request.query_params.get(
                "mmr_lambda", str(cfg.advanced_mmr_lambda_default)
            )
        )
        if not (0.0 <= mmr_lambda <= 1.0):
            raise InvalidQueryError("mmr_lambda must be in [0,1]")
        rerank_mode = request.query_params.get("rerank", "auto")
        if rerank_mode not in {"auto", "always", "never"}:
            raise InvalidQueryError("rerank must be auto|always|never")
        filter_params = _collect_filter_params(request)
        req = RequestSpec(
            query_raw=q,
            top_n=top_n,
            mmr_lambda=mmr_lambda,
            rerank_mode=rerank_mode,
            filter_params=filter_params,
            trace_id=trace_id,
        )
    except (InvalidQueryError, InvalidFilterError) as exc:
        return _error_response(exc, trace_id)
    except ValueError as exc:
        return _error_response(InvalidQueryError(str(exc)), trace_id)

    conn = _open_ro_conn(ctx.paper_config_snapshot_db if hasattr(ctx, "paper_config_snapshot_db") else request.app.state.cfg.snapshot_db)
    try:
        async with httpx.AsyncClient() as client:
            try:
                payload = await run_advanced_search(
                    request_spec=req, ctx=ctx, conn=conn, client=client,
                )
            except AdvancedSearchError as exc:
                return _error_response(exc, trace_id)
    finally:
        conn.close()
    return JSONResponse(payload, headers={"X-Request-Id": trace_id})
```

- [ ] **Step 2: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/handler.py
git commit -m "feat(advanced): Starlette handlers for verify-token and search"
```

(Handler tests are covered end-to-end in Task 20.)

---

### Task 17: Routes factory + package export

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/advanced/__init__.py`

- [ ] **Step 1: Implement `create_advanced_routes`**

Overwrite `python/deepresearch_flow/paper/snapshot/advanced/__init__.py`:

```python
"""Advanced search endpoint on snapshot API (token-gated hybrid retrieval)."""

from starlette.routing import Route

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext
from deepresearch_flow.paper.snapshot.advanced.handler import (
    _api_search_advanced,
    _api_verify_token,
)

__all__ = ["AdvancedSearchContext", "create_advanced_routes"]


def create_advanced_routes(ctx: AdvancedSearchContext) -> list[Route]:
    """Return the Starlette routes for the advanced search endpoints."""
    return [
        Route("/api/v1/search/advanced", _api_search_advanced, methods=["GET"]),
        Route(
            "/api/v1/search/advanced/verify-token",
            _api_verify_token,
            methods=["POST"],
        ),
    ]
```

- [ ] **Step 2: Smoke test import**

Run: `uv run python -c "from deepresearch_flow.paper.snapshot.advanced import create_advanced_routes, AdvancedSearchContext; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/__init__.py
git commit -m "feat(advanced): package exports create_advanced_routes"
```

---

### Task 18: Wire advanced routes into `snapshot/api.py::create_app`

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/api.py`

- [ ] **Step 1: Extend `create_app` signature and registration**

Open `python/deepresearch_flow/paper/snapshot/api.py`. Import at top:

```python
from deepresearch_flow.paper.snapshot.advanced import (
    AdvancedSearchContext,
    create_advanced_routes,
)
```

Change `create_app` signature (around line 993-1000) to add `advanced_config`:

```python
def create_app(
    *,
    snapshot_db: Path,
    static_base_url: str,
    cors_allowed_origins: list[str] | None = None,
    limits: ApiLimits | None = None,
    admin_token: str | None = None,
    advanced_config: AdvancedSearchContext | None = None,
) -> Starlette:
```

In the current body, the route list is assembled into a local `routes` variable, then the admin sub-app is conditionally appended, and finally `app = Starlette(routes=routes, ...)` is called. The right insertion points are:

1. **Before** the `app = Starlette(...)` call, extend the local `routes` list so advanced routes are part of the app at construction time:

   ```python
   if advanced_config is not None:
       routes.extend(create_advanced_routes(advanced_config))
   ```
   Place this immediately after the existing `if admin_token: ... routes.append(...)` block and immediately before `app = Starlette(routes=routes, lifespan=mcp_lifespan)`.

2. **After** `app.state.cfg = cfg` (and after any CORS middleware add), set the advanced-search state on the constructed app:

   ```python
   if advanced_config is not None:
       app.state.advanced = advanced_config
   ```
   Place this right before the final `return app`.

Do **not** use `app.routes.extend(...)` — when `app` has not been created yet, that dereference fails. The two-block split above avoids touching `app` before it exists.

- [ ] **Step 2: Smoke test**

Run: `uv run python -c "from deepresearch_flow.paper.snapshot.api import create_app; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Existing snapshot API tests still pass**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/ -v`
Expected: all existing tests PASS (no behavior change when `advanced_config` is `None`).

- [ ] **Step 4: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/api.py
git commit -m "feat(api): mount advanced search routes when advanced_config present"
```

---

### Task 19: CLI extensions on `paper db api serve`

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py` (the `api_serve` command around line 888-963)

- [ ] **Step 1: Add CLI options and startup logic**

Find the `api_serve` command in `python/deepresearch_flow/paper/db.py`. Add three `@click.option` decorators above the function definition:

```python
    @click.option("--embed-db", "embed_db", default=None,
                  help="LanceDB directory (overrides config.search.vector_dir)")
    @click.option("--config", "config_path", default="config.toml",
                  show_default=True, help="Path to paper config TOML")
    @click.option(
        "--search-access-token", "search_access_token_cli",
        default=None, envvar="SEARCH_ACCESS_TOKEN",
        help="Bearer token for advanced search endpoint",
    )
```

Add matching parameters to the function signature: `embed_db: str | None`, `config_path: str`, `search_access_token_cli: str | None`.

Inside the function body, after the existing `static_base_url_value` resolution and before `app = create_app(...)`, add:

```python
        from deepresearch_flow.paper.config import load_config, resolve_key_value
        from deepresearch_flow.paper.routing import RoutePool
        from deepresearch_flow.paper.vector_store import (
            load_index_meta, validate_index_meta,
        )
        from deepresearch_flow.paper.snapshot.advanced import AdvancedSearchContext

        advanced_ctx: AdvancedSearchContext | None = None
        paper_config = load_config(config_path)
        if paper_config.search is not None and paper_config.search.advanced_enabled:
            lance_dir = embed_db or paper_config.search.vector_dir
            if not lance_dir:
                raise click.ClickException(
                    "Advanced search requires --embed-db or config.search.vector_dir"
                )
            if embed_db and paper_config.search.vector_dir and \
                    str(Path(embed_db).resolve()) != str(Path(paper_config.search.vector_dir).resolve()):
                click.echo(
                    f"[WARN] --embed-db ({embed_db}) differs from "
                    f"config.search.vector_dir ({paper_config.search.vector_dir}); using CLI",
                    err=True,
                )

            token = search_access_token_cli or paper_config.search.access_token
            if not token:
                raise click.ClickException(
                    "Advanced search requires a token via --search-access-token, "
                    "SEARCH_ACCESS_TOKEN, or config.search.access_token"
                )

            import lancedb
            lance_db = lancedb.connect(lance_dir)
            provider, model = paper_config.embedding.resolve_active()
            try:
                validate_index_meta(
                    Path(lance_dir),
                    model=model.model_name,
                    canonical_model=model.canonical_name,
                    dimensions=model.dimensions,
                    normalized=paper_config.embedding.normalized,
                    provider=provider.name,
                )
            except Exception as exc:
                raise click.ClickException(f"Advanced search INDEX_MISMATCH: {exc}") from exc

            embedding_pool = RoutePool.from_embedding_provider(paper_config.embedding)
            rerank_pool = None
            if paper_config.rerank is not None and paper_config.rerank.enabled:
                rerank_pool = RoutePool.from_rerank_provider(paper_config.rerank)

            advanced_ctx = AdvancedSearchContext(
                embed_db_path=Path(lance_dir),
                lance_db=lance_db,
                paper_config=paper_config,
                embedding_route_pool=embedding_pool,
                rerank_route_pool=rerank_pool,
                search_access_token=token,
                search_config=paper_config.search,
            )
```

And extend the `create_app(...)` call to pass `advanced_config=advanced_ctx`.

- [ ] **Step 2: Run CLI help to confirm flags wired**

Run: `uv run python -m deepresearch_flow paper db api serve --help`
Expected: output includes `--embed-db`, `--config`, `--search-access-token` lines.

- [ ] **Step 3: Commit**

```bash
git add python/deepresearch_flow/paper/db.py
git commit -m "feat(cli): add --embed-db --config --search-access-token to api serve"
```

---

## Phase 4 — Backend integration tests

### Task 20: End-to-end integration test

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/advanced/tests/test_advanced_api.py`

Coverage: happy path (request-level), 401 missing, 401 invalid, 400 invalid query, 400 invalid filter, request-level 503 `TOTAL_FAILURE` (both dense+sparse raise), request-level 503 `VECTOR_STORE_UNAVAILABLE` (LanceDB raises at chunk-select), plus a CLI-level startup test that drives `validate_index_meta` failure to `ClickException` (see Step 5 below). `INDEX_MISMATCH` is strictly a startup-time fail-fast — it is *not* a request-level status in the runtime error table, so there is no TestClient fixture for it.

- [ ] **Step 1: Write integration tests**

Create `python/deepresearch_flow/paper/snapshot/advanced/tests/test_advanced_api.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.advanced import (
    AdvancedSearchContext,
    create_advanced_routes,
)


class _FakeLance:
    def __init__(self, rows):
        self.rows = rows
    def open_table(self, name):
        return self
    def search(self, *a, **kw):
        return self
    def where(self, clause):
        self._sel = list(self.rows)
        return self
    def limit(self, n):
        self._sel = self._sel[:n]
        return self
    def to_list(self):
        return list(getattr(self, "_sel", self.rows))


class _EmbedModel:
    model_name = "bge-m3"
    canonical_name = "bge-m3"
    dimensions = 2


class _EmbedProv:
    name = "ollama"


class _EmbedCfg:
    default_model = "bge-m3"
    default_provider = "ollama"
    dimensions = 2
    normalized = True
    def resolve_active(self):
        return _EmbedProv(), _EmbedModel()


class _RerankCfg:
    enabled = False


class _PaperCfg:
    embedding = _EmbedCfg()
    rerank = _RerankCfg()


class _SearchCfg:
    advanced_rrf_k = 60
    advanced_dense_top_k = 50
    advanced_sparse_top_k = 30
    advanced_post_fusion_top_k = 50
    advanced_dedup_cosine_threshold = 0.95
    advanced_rerank_top_n = 20
    advanced_mmr_lambda_default = 0.6
    advanced_rerank_timeout_ms = 1500
    advanced_top_n_max = 50
    advanced_max_query_length = 500


def _build_app(tmp_path: Path, monkeypatch) -> tuple[Starlette, Path]:
    db_path = tmp_path / "snap.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE paper (paper_id TEXT PRIMARY KEY, title TEXT, year TEXT,
          venue TEXT, source_hash TEXT, output_language TEXT);
        CREATE VIRTUAL TABLE paper_fts USING fts5(
          paper_id UNINDEXED, title, summary, source, translated, metadata,
          tokenize='unicode61');
        CREATE VIRTUAL TABLE paper_fts_trigram USING fts5(
          paper_id UNINDEXED, title, venue, tokenize='trigram');
        CREATE TABLE author (author_id INTEGER PRIMARY KEY, value TEXT UNIQUE);
        CREATE TABLE paper_author (paper_id TEXT, author_id INTEGER);
        INSERT INTO paper VALUES ('p1','Vision','2023','ICLR','h','en');
        INSERT INTO paper_fts(paper_id,title,summary,source,translated,metadata)
          VALUES ('p1','Vision','vision transformer','','','meta');
    """)
    conn.commit()
    conn.close()

    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    async def fake_embed(**kw):
        return SimpleNamespace(vectors=[[0.5, 0.5]], model="bge-m3", usage_tokens=0)

    def fake_qv(db, vec, *, top_k, where=None):
        return [{
            "id": "p1_c0", "doc_id": "p1", "_distance": 0.1,
            "text": "body", "field_name": "simple/content",
            "template_tag": "simple", "chunk_type": "content",
            "chunk_index": 0, "lang": "en", "vector": [0.5, 0.5],
        }]

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", fake_embed)
    monkeypatch.setattr(retrieve_dense, "query_vector", fake_qv)

    ctx = AdvancedSearchContext(
        embed_db_path=tmp_path / "lance",
        lance_db=_FakeLance([]),
        paper_config=_PaperCfg(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token="secret",
        search_config=_SearchCfg(),
    )

    app = Starlette(routes=create_advanced_routes(ctx))
    app.state.advanced = ctx
    app.state.cfg = SimpleNamespace(snapshot_db=db_path)
    return app, db_path


def test_verify_token_missing(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post("/api/v1/search/advanced/verify-token")
    assert res.status_code == 401
    assert res.json() == {"valid": False, "reason": "missing"}


def test_verify_token_invalid(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post(
        "/api/v1/search/advanced/verify-token",
        headers={"Authorization": "Bearer wrong"},
    )
    assert res.status_code == 401
    assert res.json() == {"valid": False, "reason": "invalid"}


def test_verify_token_ok(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.post(
        "/api/v1/search/advanced/verify-token",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 200
    assert res.json() == {"valid": True}


def test_search_happy_path(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["degraded"] is False
    assert body["results"][0]["paper_id"] == "p1"


def test_search_missing_token(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get("/api/v1/search/advanced?q=vision")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"
    assert res.json()["error"]["details"]["reason"] == "missing"


def test_search_invalid_token(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer bad"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["details"]["reason"] == "invalid"


def test_search_empty_query(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_QUERY"


def test_search_bad_filter_venue(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision&filters.venue=drop;table",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_FILTER"


def test_trace_id_echoed(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret", "X-Request-Id": "my-trace"},
    )
    assert res.headers.get("X-Request-Id") == "my-trace"
    assert res.json()["trace_id"] == "my-trace"


def test_search_total_failure_503(tmp_path, monkeypatch) -> None:
    """When both dense and sparse branches raise, endpoint returns 503 TOTAL_FAILURE."""
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense, retrieve_sparse

    app, _ = _build_app(tmp_path, monkeypatch)

    async def raise_embed(**kw):
        raise RuntimeError("embedding down")
    def raise_sparse(**kw):
        raise RuntimeError("fts busted")

    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)
    monkeypatch.setattr(retrieve_sparse, "sparse_retrieve", raise_sparse)

    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "TOTAL_FAILURE"


def test_search_vector_store_unavailable_503(tmp_path, monkeypatch) -> None:
    """When LanceDB raises during chunk selection, return 503 VECTOR_STORE_UNAVAILABLE."""
    from deepresearch_flow.paper.snapshot.advanced import retrieve_dense

    app, _ = _build_app(tmp_path, monkeypatch)

    async def raise_embed(**kw):
        # Force sparse-only path (embedding failed) so chunk_select must hit LanceDB
        raise RuntimeError("embed down")
    monkeypatch.setattr(retrieve_dense, "call_embedding_with_route_pool", raise_embed)

    class _BadLance:
        def open_table(self, name):
            raise RuntimeError("lance file corrupted")
    app.state.advanced = app.state.advanced  # type: ignore[assignment]
    # Replace the lance_db on the context with a broken one
    ctx = app.state.advanced
    object.__setattr__(ctx, "lance_db", _BadLance())

    client = TestClient(app)
    res = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "VECTOR_STORE_UNAVAILABLE"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_advanced_api.py -v`
Expected: all eleven tests PASS (nine original + `test_search_total_failure_503` + `test_search_vector_store_unavailable_503`).

- [ ] **Step 3: Run the full advanced test suite for regression**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/advanced/ -v`
Expected: all tests across the advanced package PASS.

- [ ] **Step 4: Commit the request-level tests**

```bash
git add python/deepresearch_flow/paper/snapshot/advanced/tests/test_advanced_api.py
git commit -m "test(advanced): end-to-end integration tests for both endpoints"
```

- [ ] **Step 5: Write CLI startup test for `INDEX_MISMATCH`**

Append to `python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py` (create if absent):

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

# Entry: the root click group exposed from the package-level CLI module.
# paper/db.py does not export a `main` or a click.Command; it only provides
# `register_db_commands(...)`. The real tree is
# deepresearch_flow.cli:cli → paper → db → api → serve.
from deepresearch_flow.cli import cli


def test_api_serve_fails_fast_on_index_mismatch(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "snap.db"
    db.write_text("")  # placeholder — validate_index_meta raises before DB is read
    config = tmp_path / "config.toml"
    config.write_text(
        """
main_model = [ { model = "ollama/m", weight = 1 } ]

[extract]
output = "o.json"
errors = "e.json"

[render]

[[providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "m" } ]

[embedding]
default_provider = "ollama"
default_model = "bge-m3"
dimensions = 1024
normalized = true
batch_size = 16
chunk_max_tokens = 512
chunk_overlap_tokens = 64

[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base = [ { url = "http://x", weight = 1, key = [{ value = "k", weight = 1 }] } ]
models = [ { model_name = "bge-m3", dimensions = 1024, max_context = 8192 } ]

[search]
vector_dir = "./nope"
vector_top_k = 50
keyword_top_k = 30
hybrid = true
advanced_enabled = true
"""
    )

    # NOTE: Task 19 imports validate_index_meta as a function-local name inside
    # api_serve(). Patching the module attribute on `paper.db` would not replace
    # the function-local binding. Patch the *source* module instead — the
    # function-local import still references the same object, so replacing the
    # source-module attribute intercepts the real call.
    with patch(
        "deepresearch_flow.paper.vector_store.validate_index_meta",
        side_effect=ValueError("dimensions mismatch"),
    ):
        res = runner.invoke(
            cli,
            [
                "paper", "db", "api", "serve",
                "--snapshot-db", str(db),
                "--config", str(config),
                "--embed-db", str(tmp_path / "lance"),
                "--search-access-token", "t",
            ],
        )
    assert res.exit_code != 0
    assert "INDEX_MISMATCH" in res.output or "dimensions mismatch" in res.output
```

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit the CLI startup test**

```bash
git add python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py
git commit -m "test(advanced): CLI fails fast on INDEX_MISMATCH at startup"
```

---

## Phase 5 — Frontend

### Task 21: `token-db.ts` — IndexedDB wrapper

**Files:**
- Create: `frontend/src/lib/token-db.ts`
- Create: `frontend/src/__tests__/tokenDb.test.ts`

Interface contract: `getToken(): Promise<string | null>`, `setToken(t: string): Promise<void>`, `clearToken(): Promise<void>`. Uses DB `deepresearch_flow` v1, store `settings`, key `search_access_token`. Writes objects `{token, saved_at: ISO-string}`. Reads accept object-with-`token` OR bare string OR missing.

- [ ] **Step 1: Write failing test**

Create `frontend/src/__tests__/tokenDb.test.ts`:

```typescript
import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { clearToken, getToken, setToken } from '@/lib/token-db'

async function rawWrite(value: unknown): Promise<void> {
  const dbReq = indexedDB.open('deepresearch_flow', 1)
  await new Promise<void>((resolve, reject) => {
    dbReq.onupgradeneeded = () => {
      dbReq.result.createObjectStore('settings')
    }
    dbReq.onsuccess = () => {
      const db = dbReq.result
      const tx = db.transaction('settings', 'readwrite')
      tx.objectStore('settings').put(value, 'search_access_token')
      tx.oncomplete = () => { db.close(); resolve() }
      tx.onerror = () => reject(tx.error)
    }
    dbReq.onerror = () => reject(dbReq.error)
  })
}

async function wipe(): Promise<void> {
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase('deepresearch_flow')
    req.onsuccess = req.onerror = req.onblocked = () => resolve()
  })
}

beforeEach(wipe)
afterEach(wipe)

describe('token-db', () => {
  it('returns null when unset', async () => {
    expect(await getToken()).toBeNull()
  })

  it('round-trips a token via setToken/getToken', async () => {
    await setToken('abc123')
    expect(await getToken()).toBe('abc123')
  })

  it('clears the token', async () => {
    await setToken('abc')
    await clearToken()
    expect(await getToken()).toBeNull()
  })

  it('reads legacy bare-string form', async () => {
    await rawWrite('legacy-token')
    expect(await getToken()).toBe('legacy-token')
  })

  it('reads object form {token, saved_at}', async () => {
    await rawWrite({ token: 'obj-form', saved_at: '2026-01-01T00:00:00Z' })
    expect(await getToken()).toBe('obj-form')
  })

  it('returns null for malformed object', async () => {
    await rawWrite({ not_token: 'x' })
    expect(await getToken()).toBeNull()
  })

  it('writes object form', async () => {
    await setToken('fresh')
    const dbReq = indexedDB.open('deepresearch_flow', 1)
    const stored = await new Promise<unknown>((resolve) => {
      dbReq.onsuccess = () => {
        const db = dbReq.result
        const tx = db.transaction('settings', 'readonly')
        const req = tx.objectStore('settings').get('search_access_token')
        req.onsuccess = () => { db.close(); resolve(req.result) }
      }
    })
    expect(stored).toMatchObject({ token: 'fresh' })
    expect((stored as { saved_at: string }).saved_at).toMatch(/^\d{4}-/)
  })
})
```

- [ ] **Step 2: Add `fake-indexeddb` dev dep if missing**

Run: `cd frontend && npm install --save-dev fake-indexeddb`
Expected: `fake-indexeddb` appears in `package.json`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/__tests__/tokenDb.test.ts`
Expected: FAIL with `Cannot find module '@/lib/token-db'`.

- [ ] **Step 4: Implement `token-db.ts`**

Create `frontend/src/lib/token-db.ts`:

```typescript
// IndexedDB-backed storage for the advanced-search access token.
// Shared triple with paper/web/static/js/index.js for same-origin token reuse.

const DB_NAME = 'deepresearch_flow'
const DB_VERSION = 1
const STORE_NAME = 'settings'
const KEY = 'search_access_token'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error || new Error('IDB open failed'))
  })
}

export async function getToken(): Promise<string | null> {
  try {
    const db = await openDb()
    try {
      const value = await new Promise<unknown>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const req = tx.objectStore(STORE_NAME).get(KEY)
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      })
      if (value && typeof value === 'object' && 'token' in value &&
          typeof (value as { token: unknown }).token === 'string') {
        return (value as { token: string }).token
      }
      if (typeof value === 'string') return value
      return null
    } finally {
      db.close()
    }
  } catch {
    return null
  }
}

export async function setToken(token: string): Promise<void> {
  const db = await openDb()
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put(
        { token, saved_at: new Date().toISOString() },
        KEY,
      )
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

export async function clearToken(): Promise<void> {
  try {
    const db = await openDb()
    try {
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        tx.objectStore(STORE_NAME).delete(KEY)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
      })
    } finally {
      db.close()
    }
  } catch {
    /* noop */
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/__tests__/tokenDb.test.ts`
Expected: seven tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json \
  frontend/src/lib/token-db.ts frontend/src/__tests__/tokenDb.test.ts
git commit -m "feat(frontend): token-db IndexedDB wrapper with legacy read-compat"
```

---

### Task 22: `advanced-search.ts` — API client

**Files:**
- Create: `frontend/src/lib/advanced-search.ts`
- Create: `frontend/src/__tests__/advancedSearch.test.ts`

Interface contract:
- `verifyToken(token: string): Promise<{valid: true} | {valid: false, reason: 'missing' | 'invalid'}>`
- `advancedSearch(params: AdvancedSearchParams, token: string): Promise<AdvancedSearchResponse>` — returns parsed success payload; on non-2xx throws `AdvancedSearchHTTPError` with `status`, `code`, `details`.

- [ ] **Step 1: Write failing test**

Create `frontend/src/__tests__/advancedSearch.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AdvancedSearchHTTPError,
  advancedSearch,
  verifyToken,
} from '@/lib/advanced-search'

const origFetch = globalThis.fetch

beforeEach(() => { globalThis.fetch = vi.fn() as unknown as typeof fetch })
afterEach(() => { globalThis.fetch = origFetch })

function stubJson(status: number, body: unknown) {
  ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status, headers: { 'content-type': 'application/json' },
    }),
  )
}

describe('verifyToken', () => {
  it('returns {valid: true} on 200', async () => {
    stubJson(200, { valid: true })
    expect(await verifyToken('ok')).toEqual({ valid: true })
  })

  it('returns {valid: false, reason: "invalid"} on 401 invalid', async () => {
    stubJson(401, { valid: false, reason: 'invalid' })
    expect(await verifyToken('bad')).toEqual({ valid: false, reason: 'invalid' })
  })

  it('returns {valid: false, reason: "missing"} on 401 missing', async () => {
    stubJson(401, { valid: false, reason: 'missing' })
    expect(await verifyToken('')).toEqual({ valid: false, reason: 'missing' })
  })

  it('sends Authorization: Bearer header', async () => {
    stubJson(200, { valid: true })
    await verifyToken('tok')
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(call[0]).toBe('/api/v1/search/advanced/verify-token')
    const init = call[1] as RequestInit
    expect(init.method).toBe('POST')
    const headers = new Headers(init.headers)
    expect(headers.get('authorization')).toBe('Bearer tok')
  })
})

describe('advancedSearch', () => {
  const ok = {
    success: true, trace_id: 't', query: { raw: 'q', normalized: 'q', applied_filters: {} },
    results: [], metadata: { counts: {}, fusion: 'rrf',
      reranker: { applied: false, model: null },
      mmr: { applied: true, lambda: 0.6 },
      embedding: { model: 'bge-m3', dimensions: 1024 },
      latency_ms: {} },
    degraded: false, degradation: null,
  }

  it('builds query string and header', async () => {
    stubJson(200, ok)
    await advancedSearch(
      { q: 'vision', topN: 5, filters: { year: '2020..2022', venues: ['ICLR'] }, mmrLambda: 0.6, rerank: 'auto' },
      'secret',
    )
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain('q=vision')
    expect(url).toContain('top_n=5')
    expect(url).toContain('filters.year=2020..2022')
    expect(url).toContain('filters.venue=ICLR')
    expect(url).toContain('mmr_lambda=0.6')
    expect(url).toContain('rerank=auto')
  })

  it('returns parsed payload on 200', async () => {
    stubJson(200, ok)
    const out = await advancedSearch({ q: 'q' }, 'secret')
    expect(out.success).toBe(true)
  })

  it('throws AdvancedSearchHTTPError on 401', async () => {
    stubJson(401, { success: false, trace_id: 't',
      error: { code: 'UNAUTHORIZED', message: 'invalid', details: { reason: 'invalid' } } })
    await expect(advancedSearch({ q: 'q' }, 'bad'))
      .rejects.toBeInstanceOf(AdvancedSearchHTTPError)
  })

  it('throws AdvancedSearchHTTPError on 400 invalid filter', async () => {
    stubJson(400, { success: false, trace_id: 't',
      error: { code: 'INVALID_FILTER', message: 'bad venue', details: {} } })
    try {
      await advancedSearch({ q: 'q', filters: { venues: ['drop;table'] } }, 'x')
      expect.fail('should throw')
    } catch (e) {
      expect((e as AdvancedSearchHTTPError).status).toBe(400)
      expect((e as AdvancedSearchHTTPError).code).toBe('INVALID_FILTER')
    }
  })

  it('throws AdvancedSearchHTTPError on 503', async () => {
    stubJson(503, { success: false, trace_id: 't',
      error: { code: 'VECTOR_STORE_UNAVAILABLE', message: '', details: {} } })
    await expect(advancedSearch({ q: 'q' }, 'x'))
      .rejects.toBeInstanceOf(AdvancedSearchHTTPError)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/__tests__/advancedSearch.test.ts`
Expected: FAIL with `Cannot find module '@/lib/advanced-search'`.

- [ ] **Step 3: Implement client**

Create `frontend/src/lib/advanced-search.ts`:

```typescript
// Advanced search API client — verify token + hybrid retrieval.

export type VerifyResult =
  | { valid: true }
  | { valid: false; reason: 'missing' | 'invalid' }

export interface AdvancedSearchFilters {
  year?: string
  venues?: string[]
  authors?: string[]
  keywords?: string[]
  tags?: string[]
  lang?: string
}

export interface AdvancedSearchParams {
  q: string
  topN?: number
  filters?: AdvancedSearchFilters
  mmrLambda?: number
  rerank?: 'auto' | 'always' | 'never'
}

export interface AdvancedSearchResult {
  chunk_id: string
  paper_id: string
  paper: {
    title: string
    authors: string[]
    year: string
    venue: string
    doi: string
    source_hash: string
  }
  chunk: {
    text: string
    field_name: string
    template_tag: string
    chunk_type: string
    chunk_index: number
    lang: string
  }
  scores: {
    dense?: number
    sparse?: number
    fused: number
    reranker?: number
    final: number
  }
}

export interface AdvancedSearchResponse {
  success: true
  trace_id: string
  query: {
    raw: string
    normalized: string
    applied_filters: Record<string, unknown>
  }
  results: AdvancedSearchResult[]
  metadata: {
    counts: Record<string, number>
    fusion: string
    reranker: { applied: boolean; model: string | null }
    mmr: { applied: boolean; lambda: number }
    embedding: { model: string; dimensions: number }
    latency_ms: Record<string, number>
  }
  degraded: boolean
  degradation: { reason: string } | null
}

export class AdvancedSearchHTTPError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly traceId: string
  constructor(status: number, code: string, message: string,
              details: Record<string, unknown>, traceId: string) {
    super(`${status} ${code}: ${message}`)
    this.status = status
    this.code = code
    this.details = details
    this.traceId = traceId
  }
}

export async function verifyToken(token: string): Promise<VerifyResult> {
  const res = await fetch('/api/v1/search/advanced/verify-token', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  const body = await res.json() as VerifyResult
  return body
}

function buildQueryString(p: AdvancedSearchParams): string {
  const parts: string[] = [`q=${encodeURIComponent(p.q)}`]
  if (p.topN !== undefined) parts.push(`top_n=${p.topN}`)
  if (p.mmrLambda !== undefined) parts.push(`mmr_lambda=${p.mmrLambda}`)
  if (p.rerank !== undefined) parts.push(`rerank=${p.rerank}`)
  const f = p.filters
  if (f) {
    if (f.year) parts.push(`filters.year=${encodeURIComponent(f.year)}`)
    for (const v of f.venues ?? []) parts.push(`filters.venue=${encodeURIComponent(v)}`)
    for (const a of f.authors ?? []) parts.push(`filters.authors=${encodeURIComponent(a)}`)
    for (const k of f.keywords ?? []) parts.push(`filters.keywords=${encodeURIComponent(k)}`)
    for (const t of f.tags ?? []) parts.push(`filters.tags=${encodeURIComponent(t)}`)
    if (f.lang) parts.push(`filters.lang=${encodeURIComponent(f.lang)}`)
  }
  return parts.join('&')
}

export async function advancedSearch(
  params: AdvancedSearchParams,
  token: string,
): Promise<AdvancedSearchResponse> {
  const url = `/api/v1/search/advanced?${buildQueryString(params)}`
  const res = await fetch(url, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as {
      success?: false
      trace_id?: string
      error?: { code?: string; message?: string; details?: Record<string, unknown> }
    }
    throw new AdvancedSearchHTTPError(
      res.status,
      body.error?.code ?? 'UNKNOWN',
      body.error?.message ?? '',
      body.error?.details ?? {},
      body.trace_id ?? '',
    )
  }
  return await res.json() as AdvancedSearchResponse
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/__tests__/advancedSearch.test.ts`
Expected: eight tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/advanced-search.ts \
  frontend/src/__tests__/advancedSearch.test.ts
git commit -m "feat(frontend): advanced-search API client with typed error"
```

---

### Task 23: `useAdvancedSearchToken` composable (state machine)

**Files:**
- Create: `frontend/src/composables/useAdvancedSearchToken.ts`
- Create: `frontend/src/__tests__/useAdvancedSearchToken.test.ts`

Interface contract: `useAdvancedSearchToken()` returns `{ state: Ref<'not-verified' | 'verifying' | 'verified'>, token: Ref<string | null>, hydrate(): Promise<void>, verify(input: string): Promise<boolean>, clear(): Promise<void>, onAuthFailure(): Promise<void> }`. **State is module-level singleton**: calling `useAdvancedSearchToken()` from different components returns the same reactive `state` / `token` refs, so the panel and `SearchView` share one source of truth. `hydrate` pulls from IndexedDB and verifies server-side. `verify` validates and stores on success, clears on failure. `onAuthFailure` clears storage + state.

- [ ] **Step 1: Write failing test**

Create `frontend/src/__tests__/useAdvancedSearchToken.test.ts`:

```typescript
import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
import * as tokenDb from '@/lib/token-db'
import * as api from '@/lib/advanced-search'

beforeEach(async () => {
  await tokenDb.clearToken()
  vi.restoreAllMocks()
})

afterEach(async () => {
  await tokenDb.clearToken()
})

describe('useAdvancedSearchToken', () => {
  it('starts in not-verified', () => {
    const t = useAdvancedSearchToken()
    expect(t.state.value).toBe('not-verified')
    expect(t.token.value).toBeNull()
  })

  it('hydrate with no stored token stays not-verified', async () => {
    const t = useAdvancedSearchToken()
    await t.hydrate()
    expect(t.state.value).toBe('not-verified')
  })

  it('hydrate with valid stored token → verified', async () => {
    await tokenDb.setToken('good')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const t = useAdvancedSearchToken()
    await t.hydrate()
    expect(t.state.value).toBe('verified')
    expect(t.token.value).toBe('good')
  })

  it('hydrate with invalid stored token → not-verified + cleared', async () => {
    await tokenDb.setToken('bad')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: false, reason: 'invalid' })
    const t = useAdvancedSearchToken()
    await t.hydrate()
    expect(t.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('verify valid token → verified and stores', async () => {
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const t = useAdvancedSearchToken()
    expect(await t.verify('abc')).toBe(true)
    expect(t.state.value).toBe('verified')
    expect(await tokenDb.getToken()).toBe('abc')
  })

  it('verify invalid token → not-verified and clears', async () => {
    await tokenDb.setToken('previous')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: false, reason: 'invalid' })
    const t = useAdvancedSearchToken()
    expect(await t.verify('wrong')).toBe(false)
    expect(t.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('onAuthFailure clears stored token and flips state', async () => {
    await tokenDb.setToken('live')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const t = useAdvancedSearchToken()
    await t.hydrate()
    await t.onAuthFailure()
    expect(t.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('state is verifying during in-flight verify()', async () => {
    let resolveVerify: (v: { valid: boolean; reason?: 'missing' | 'invalid' }) => void = () => {}
    vi.spyOn(api, 'verifyToken').mockReturnValueOnce(new Promise((r) => { resolveVerify = r as typeof resolveVerify }))
    const t = useAdvancedSearchToken()
    const p = t.verify('x')
    expect(t.state.value).toBe('verifying')
    resolveVerify({ valid: true })
    await p
    expect(t.state.value).toBe('verified')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/__tests__/useAdvancedSearchToken.test.ts`
Expected: FAIL with `Cannot find module`.

- [ ] **Step 3: Implement composable**

Create `frontend/src/composables/useAdvancedSearchToken.ts`:

```typescript
import { ref, type Ref } from 'vue'
import { verifyToken } from '@/lib/advanced-search'
import { clearToken, getToken, setToken } from '@/lib/token-db'

export type TokenState = 'not-verified' | 'verifying' | 'verified'

export interface AdvancedSearchTokenAPI {
  state: Ref<TokenState>
  token: Ref<string | null>
  hydrate: () => Promise<void>
  verify: (candidate: string) => Promise<boolean>
  clear: () => Promise<void>
  onAuthFailure: () => Promise<void>
}

// Module-level singleton state: shared across every caller of the composable.
// The panel (which drives verification) and SearchView (which consumes the
// token for the advanced search call) both read and write the same refs.
const _state = ref<TokenState>('not-verified')
const _token = ref<string | null>(null)

export function useAdvancedSearchToken(): AdvancedSearchTokenAPI {
  const state = _state
  const token = _token

  async function hydrate(): Promise<void> {
    const stored = await getToken()
    if (!stored) {
      state.value = 'not-verified'
      token.value = null
      return
    }
    state.value = 'verifying'
    const res = await verifyToken(stored)
    if (res.valid) {
      state.value = 'verified'
      token.value = stored
    } else {
      await clearToken()
      token.value = null
      state.value = 'not-verified'
    }
  }

  async function verify(candidate: string): Promise<boolean> {
    state.value = 'verifying'
    const res = await verifyToken(candidate)
    if (res.valid) {
      await setToken(candidate)
      token.value = candidate
      state.value = 'verified'
      return true
    }
    await clearToken()
    token.value = null
    state.value = 'not-verified'
    return false
  }

  async function clear(): Promise<void> {
    await clearToken()
    token.value = null
    state.value = 'not-verified'
  }

  async function onAuthFailure(): Promise<void> {
    await clear()
  }

  return { state, token, hydrate, verify, clear, onAuthFailure }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/__tests__/useAdvancedSearchToken.test.ts`
Expected: eight tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/useAdvancedSearchToken.ts \
  frontend/src/__tests__/useAdvancedSearchToken.test.ts
git commit -m "feat(frontend): useAdvancedSearchToken composable (state machine)"
```

---

### Task 24: `AdvancedSearchPanel.vue` component

**Files:**
- Create: `frontend/src/components/AdvancedSearchPanel.vue`
- Create: `frontend/src/__tests__/AdvancedSearchPanel.test.ts`

Interface contract: panel emits a `search` event with `AdvancedSearchParams`. Prop: `searching: boolean` (driven by the parent, which owns the async fetch). Uses the singleton `useAdvancedSearchToken` composable. Collapsed by default; on mount, calls `hydrate()` so a token stored in IndexedDB is auto-verified without requiring user interaction. Search button is disabled unless state is `verified` AND `searching` is false. While `searching` is true the button shows a loading label.

- [ ] **Step 1: Write failing test**

Create `frontend/src/__tests__/AdvancedSearchPanel.test.ts`:

```typescript
import 'fake-indexeddb/auto'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AdvancedSearchPanel from '@/components/AdvancedSearchPanel.vue'
import * as api from '@/lib/advanced-search'
import * as tokenDb from '@/lib/token-db'

beforeEach(async () => {
  await tokenDb.clearToken()
  vi.restoreAllMocks()
  // Reset the composable's module-level state by clearing any stored token
  // and forcing onAuthFailure via a fresh instance (safe because it mutates
  // the same singleton refs).
  const { useAdvancedSearchToken } = await import('@/composables/useAdvancedSearchToken')
  await useAdvancedSearchToken().clear()
})
afterEach(async () => {
  await tokenDb.clearToken()
})

describe('AdvancedSearchPanel', () => {
  it('starts collapsed', () => {
    const w = mount(AdvancedSearchPanel)
    expect(w.find('[data-testid="advanced-panel-body"]').exists()).toBe(false)
  })

  it('expands on toggle', async () => {
    const w = mount(AdvancedSearchPanel)
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    expect(w.find('[data-testid="advanced-panel-body"]').exists()).toBe(true)
  })

  it('search button disabled when not verified', async () => {
    const w = mount(AdvancedSearchPanel)
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    const btn = w.find('[data-testid="advanced-search-button"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('verify then search emits event', async () => {
    vi.spyOn(api, 'verifyToken').mockResolvedValue({ valid: true })
    const w = mount(AdvancedSearchPanel)
    await flushPromises()  // let onMounted's hydrate() settle (no stored token)
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await w.find('[data-testid="advanced-token-input"]').setValue('secret')
    await w.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="advanced-query-input"]').setValue('vision transformer')
    await w.find('[data-testid="advanced-search-button"]').trigger('click')
    const events = w.emitted('search')
    expect(events).toBeTruthy()
    expect((events as unknown[][])[0][0]).toMatchObject({ q: 'vision transformer' })
  })

  it('invalid token shows error indicator', async () => {
    vi.spyOn(api, 'verifyToken').mockResolvedValue({ valid: false, reason: 'invalid' })
    const w = mount(AdvancedSearchPanel)
    await flushPromises()
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await w.find('[data-testid="advanced-token-input"]').setValue('bad')
    await w.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(true)
    const btn = w.find('[data-testid="advanced-search-button"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('auto-verifies a stored token on mount (hydrate wiring)', async () => {
    await tokenDb.setToken('stored-good')
    const spy = vi.spyOn(api, 'verifyToken').mockResolvedValue({ valid: true })
    const w = mount(AdvancedSearchPanel)
    await flushPromises()
    expect(spy).toHaveBeenCalledWith('stored-good')
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    // Token was auto-verified → input and button are enabled without any
    // user interaction.
    const input = w.find('[data-testid="advanced-query-input"]')
    expect((input.element as HTMLInputElement).disabled).toBe(false)
  })

  it('searching prop puts button in loading state and disables it', async () => {
    vi.spyOn(api, 'verifyToken').mockResolvedValue({ valid: true })
    const w = mount(AdvancedSearchPanel, { props: { searching: false } })
    await flushPromises()
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await w.find('[data-testid="advanced-token-input"]').setValue('secret')
    await w.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await w.setProps({ searching: true })
    const btn = w.find('[data-testid="advanced-search-button"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    expect(btn.text()).toContain('Searching')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/__tests__/AdvancedSearchPanel.test.ts`
Expected: FAIL with `Cannot find module`.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/AdvancedSearchPanel.vue`:

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
import type { AdvancedSearchParams } from '@/lib/advanced-search'

const props = defineProps<{ searching?: boolean }>()
const emit = defineEmits<(e: 'search', params: AdvancedSearchParams) => void>()

const expanded = ref(false)
const tokenInput = ref('')
const queryInput = ref('')
const lastVerifyInvalid = ref(false)
const { state, verify, hydrate } = useAdvancedSearchToken()

onMounted(async () => {
  await hydrate()
})

async function onVerify() {
  lastVerifyInvalid.value = false
  const ok = await verify(tokenInput.value)
  if (!ok) lastVerifyInvalid.value = true
  else tokenInput.value = ''
}

function onSearch() {
  if (state.value !== 'verified' || props.searching) return
  emit('search', { q: queryInput.value })
}

function toggle() {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="advanced-panel">
    <button
      type="button"
      data-testid="advanced-panel-toggle"
      @click="toggle"
    >
      <span>{{ expanded ? '▼' : '▶' }} Advanced search</span>
    </button>

    <div v-if="expanded" data-testid="advanced-panel-body" class="advanced-panel__body">
      <div class="advanced-panel__row">
        <input
          v-model="tokenInput"
          type="password"
          placeholder="Access token"
          data-testid="advanced-token-input"
        />
        <button
          type="button"
          data-testid="advanced-verify-button"
          :disabled="state === 'verifying'"
          @click="onVerify"
        >
          {{ state === 'verifying' ? 'Verifying…' : 'Verify token' }}
        </button>
        <span
          v-if="state === 'verified'"
          data-testid="advanced-token-status-verified"
        >✓ verified</span>
        <span
          v-else-if="lastVerifyInvalid"
          data-testid="advanced-token-status-invalid"
        >✗ invalid</span>
      </div>

      <div class="advanced-panel__row">
        <input
          v-model="queryInput"
          type="text"
          placeholder="Advanced query"
          :disabled="state !== 'verified'"
          data-testid="advanced-query-input"
        />
        <button
          type="button"
          data-testid="advanced-search-button"
          :disabled="state !== 'verified' || !!props.searching"
          @click="onSearch"
        >
          {{ props.searching ? 'Searching…' : 'Advanced search' }}
        </button>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/__tests__/AdvancedSearchPanel.test.ts`
Expected: seven tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AdvancedSearchPanel.vue \
  frontend/src/__tests__/AdvancedSearchPanel.test.ts
git commit -m "feat(frontend): AdvancedSearchPanel component with token + search controls"
```

---

### Task 25: `AdvancedSearchResults.vue` component

**Files:**
- Create: `frontend/src/components/AdvancedSearchResults.vue`
- Create: `frontend/src/__tests__/AdvancedSearchResults.test.ts`

Interface contract: props `{ results: AdvancedSearchResult[], degraded?: boolean, degradationReason?: string | null }`. Renders one card per result with paper title, authors, year, venue; chunk excerpt; scores panel; degradation banner if applicable.

- [ ] **Step 1: Write failing test**

Create `frontend/src/__tests__/AdvancedSearchResults.test.ts`:

```typescript
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AdvancedSearchResults from '@/components/AdvancedSearchResults.vue'
import type { AdvancedSearchResult } from '@/lib/advanced-search'

function sample(): AdvancedSearchResult {
  return {
    chunk_id: 'p1_c0',
    paper_id: 'p1',
    paper: {
      title: 'An Image is Worth 16x16 Words',
      authors: ['Dosovitskiy A.'],
      year: '2020', venue: 'ICLR', doi: '10.x', source_hash: 'h',
    },
    chunk: {
      text: 'body...',
      field_name: 'simple/content', template_tag: 'simple',
      chunk_type: 'content', chunk_index: 0, lang: 'en',
    },
    scores: { dense: 0.84, sparse: 12.37, fused: 0.016, reranker: 0.912, final: 0.912 },
  }
}

describe('AdvancedSearchResults', () => {
  it('renders one card per result', () => {
    const w = mount(AdvancedSearchResults, { props: { results: [sample(), sample()] } })
    expect(w.findAll('[data-testid="advanced-result-card"]')).toHaveLength(2)
  })

  it('renders paper title and authors', () => {
    const w = mount(AdvancedSearchResults, { props: { results: [sample()] } })
    expect(w.text()).toContain('An Image is Worth 16x16 Words')
    expect(w.text()).toContain('Dosovitskiy A.')
  })

  it('renders degradation banner when degraded', () => {
    const w = mount(AdvancedSearchResults, {
      props: { results: [], degraded: true, degradationReason: 'reranker_failed' },
    })
    expect(w.find('[data-testid="advanced-degraded-banner"]').exists()).toBe(true)
    expect(w.text()).toContain('reranker_failed')
  })

  it('hides degradation banner when not degraded', () => {
    const w = mount(AdvancedSearchResults, { props: { results: [sample()] } })
    expect(w.find('[data-testid="advanced-degraded-banner"]').exists()).toBe(false)
  })

  it('renders empty-state when no results', () => {
    const w = mount(AdvancedSearchResults, { props: { results: [] } })
    expect(w.find('[data-testid="advanced-results-empty"]').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/__tests__/AdvancedSearchResults.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement component**

Create `frontend/src/components/AdvancedSearchResults.vue`:

```vue
<script setup lang="ts">
import type { AdvancedSearchResult } from '@/lib/advanced-search'

defineProps<{
  results: AdvancedSearchResult[]
  degraded?: boolean
  degradationReason?: string | null
}>()
</script>

<template>
  <section class="advanced-results">
    <div
      v-if="degraded"
      data-testid="advanced-degraded-banner"
      class="advanced-results__banner"
    >
      Results are degraded: {{ degradationReason ?? 'unknown' }}
    </div>

    <div
      v-if="results.length === 0"
      data-testid="advanced-results-empty"
      class="advanced-results__empty"
    >
      No results.
    </div>

    <article
      v-for="r in results"
      :key="r.chunk_id"
      data-testid="advanced-result-card"
      class="advanced-results__card"
    >
      <h3>{{ r.paper.title }}</h3>
      <p class="advanced-results__meta">
        {{ r.paper.authors.join(', ') }} · {{ r.paper.year }} · {{ r.paper.venue }}
      </p>
      <p class="advanced-results__chunk">{{ r.chunk.text }}</p>
      <dl class="advanced-results__scores">
        <template v-if="r.scores.dense !== undefined">
          <dt>dense</dt><dd>{{ r.scores.dense.toFixed(4) }}</dd>
        </template>
        <template v-if="r.scores.sparse !== undefined">
          <dt>sparse</dt><dd>{{ r.scores.sparse.toFixed(2) }}</dd>
        </template>
        <dt>fused</dt><dd>{{ r.scores.fused.toFixed(4) }}</dd>
        <template v-if="r.scores.reranker !== undefined">
          <dt>rerank</dt><dd>{{ r.scores.reranker.toFixed(3) }}</dd>
        </template>
        <dt>final</dt><dd>{{ r.scores.final.toFixed(3) }}</dd>
      </dl>
    </article>
  </section>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/__tests__/AdvancedSearchResults.test.ts`
Expected: five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AdvancedSearchResults.vue \
  frontend/src/__tests__/AdvancedSearchResults.test.ts
git commit -m "feat(frontend): AdvancedSearchResults renders chunk-shaped hits"
```

---

### Task 26: Wire into `SearchView.vue` + re-export in `api.ts`

**Files:**
- Modify: `frontend/src/views/SearchView.vue`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Re-export helpers in `api.ts`**

Append to `frontend/src/lib/api.ts`:

```typescript
export {
  advancedSearch,
  verifyToken,
  AdvancedSearchHTTPError,
} from '@/lib/advanced-search'
export type {
  AdvancedSearchParams,
  AdvancedSearchResult,
  AdvancedSearchResponse,
  AdvancedSearchFilters,
  VerifyResult,
} from '@/lib/advanced-search'
```

- [ ] **Step 2: Mount advanced panel + results in `SearchView.vue`**

Open `frontend/src/views/SearchView.vue`. In the `<script setup>` block, add:

```typescript
import { ref } from 'vue'
import AdvancedSearchPanel from '@/components/AdvancedSearchPanel.vue'
import AdvancedSearchResults from '@/components/AdvancedSearchResults.vue'
import {
  advancedSearch,
  AdvancedSearchHTTPError,
  type AdvancedSearchParams,
  type AdvancedSearchResult,
} from '@/lib/api'
import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
// The existing SearchView already imports useUiStore earlier in the file;
// reuse that `ui` binding rather than creating a second one.

// Singleton: same instance as inside AdvancedSearchPanel. After the panel
// verifies a token, `advancedToken.value` here is populated automatically.
const { token: advancedToken, onAuthFailure } = useAdvancedSearchToken()

const advancedResults = ref<AdvancedSearchResult[]>([])
const advancedDegraded = ref(false)
const advancedDegradationReason = ref<string | null>(null)
const advancedSearching = ref(false)

async function onAdvancedSearch(params: AdvancedSearchParams) {
  if (!advancedToken.value) return
  advancedSearching.value = true
  try {
    const body = await advancedSearch(params, advancedToken.value)
    advancedResults.value = body.results
    advancedDegraded.value = body.degraded
    advancedDegradationReason.value = body.degradation?.reason ?? null
    if (body.degraded) {
      ui.pushToast(
        `Advanced search degraded: ${body.degradation?.reason ?? 'unknown'}`,
        'warning',
      )
    }
  } catch (e) {
    advancedResults.value = []
    advancedDegraded.value = false
    advancedDegradationReason.value = null
    if (e instanceof AdvancedSearchHTTPError) {
      if (e.status === 401) {
        await onAuthFailure()
        ui.pushToast('Advanced search token is invalid. Please re-verify.', 'error')
      } else if (e.status === 400) {
        ui.pushToast(`Invalid request: ${e.message}`, 'error')
      } else if (e.status === 503) {
        ui.pushToast('Advanced search temporarily unavailable.', 'error')
      } else {
        ui.pushToast(`Advanced search failed (${e.status}).`, 'error')
      }
    } else {
      ui.pushToast('Advanced search failed. Please try again.', 'error')
    }
  } finally {
    advancedSearching.value = false
  }
}
```

Note: `ui` is the existing `useUiStore()` binding already present in `SearchView.vue` (see `frontend/src/views/SearchView.vue:6,24`); no new import or re-declaration is required. Every non-2xx path — 401 / 400 / 503 / fallback — produces a user-visible toast, satisfying the spec's frontend network-layer contract. Degraded-but-200 responses also surface a warning toast so users know some results are partial.

In the `<template>`, below the existing basic search block, add:

```vue
<AdvancedSearchPanel
  :searching="advancedSearching"
  @search="onAdvancedSearch"
/>
<AdvancedSearchResults
  :results="advancedResults"
  :degraded="advancedDegraded"
  :degradation-reason="advancedDegradationReason"
/>
```

- [ ] **Step 3: Typecheck and run existing tests**

Run: `cd frontend && npx vue-tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

Run: `cd frontend && npx vitest run`
Expected: all existing + new tests PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/views/SearchView.vue
git commit -m "feat(frontend): wire advanced panel + results into SearchView"
```

---

### Task 27: Integration test — full verify + search flow

**Files:**
- Create: `frontend/src/__tests__/advancedSearchFlow.test.ts`

Covers the five E2E scenarios documented in spec §9 via Vitest with stubbed `fetch` and fake IndexedDB.

- [ ] **Step 1: Write the integration test**

Create `frontend/src/__tests__/advancedSearchFlow.test.ts`:

```typescript
import 'fake-indexeddb/auto'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AdvancedSearchPanel from '@/components/AdvancedSearchPanel.vue'
import { clearToken, getToken, setToken } from '@/lib/token-db'

const origFetch = globalThis.fetch

function stubJson(status: number, body: unknown) {
  return (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status, headers: { 'content-type': 'application/json' },
    }),
  )
}

beforeEach(async () => {
  await clearToken()
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})
afterEach(async () => {
  globalThis.fetch = origFetch
  await clearToken()
})

describe('advanced search flow', () => {
  it('new user: enter token → verify ok → search emits params', async () => {
    const w = mount(AdvancedSearchPanel)
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await w.find('[data-testid="advanced-token-input"]').setValue('secret')
    stubJson(200, { valid: true })
    await w.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(true)
    expect(await getToken()).toBe('secret')

    await w.find('[data-testid="advanced-query-input"]').setValue('transformer')
    await w.find('[data-testid="advanced-search-button"]').trigger('click')
    const events = w.emitted('search') as unknown[][]
    expect(events[0][0]).toMatchObject({ q: 'transformer' })
  })

  it('returning user: stored token triggers auto-verify on panel mount', async () => {
    await setToken('saved')
    stubJson(200, { valid: true })
    const w = mount(AdvancedSearchPanel)
    await flushPromises()  // let onMounted's hydrate complete
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    // Token was verified by the panel's own onMounted→hydrate, without a
    // manual .hydrate() call. The UI should reflect verified state.
    expect(w.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(true)
    const queryInput = w.find('[data-testid="advanced-query-input"]')
    expect((queryInput.element as HTMLInputElement).disabled).toBe(false)
    expect(await getToken()).toBe('saved')
  })

  it('token revoked mid-session: panel flips back to not-verified', async () => {
    await setToken('live')
    stubJson(200, { valid: true })
    const w = mount(AdvancedSearchPanel)
    await flushPromises()
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    expect(w.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(true)
    // Simulate mid-session 401 via the shared composable's onAuthFailure —
    // exactly what SearchView's onAdvancedSearch catch block invokes.
    const { useAdvancedSearchToken } = await import('@/composables/useAdvancedSearchToken')
    await useAdvancedSearchToken().onAuthFailure()
    await flushPromises()
    expect(w.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(false)
    const btn = w.find('[data-testid="advanced-search-button"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    expect(await getToken()).toBeNull()
  })

  it('invalid token: verify returns 401 → status ✗ invalid, IndexedDB untouched', async () => {
    const w = mount(AdvancedSearchPanel)
    await w.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await w.find('[data-testid="advanced-token-input"]').setValue('bad')
    stubJson(401, { valid: false, reason: 'invalid' })
    await w.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(true)
    expect(await getToken()).toBeNull()
    const btn = w.find('[data-testid="advanced-search-button"]')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('basic regression: advanced panel does not affect basic DOM', async () => {
    const w = mount(AdvancedSearchPanel)
    // No basic search inputs are inside this panel; just confirm the panel
    // starts collapsed and no state leaks into the document.
    expect(w.find('[data-testid="advanced-panel-body"]').exists()).toBe(false)
    expect(document.querySelectorAll('input').length).toBe(0)
  })
})
```

- [ ] **Step 2: Run the integration test**

Run: `cd frontend && npx vitest run src/__tests__/advancedSearchFlow.test.ts`
Expected: five scenarios PASS.

- [ ] **Step 3: Run the full frontend test suite**

Run: `cd frontend && npm test`
Expected: all tests PASS (new + existing).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/__tests__/advancedSearchFlow.test.ts
git commit -m "test(frontend): integration coverage for verify + search flow"
```

---

## Final validation

- [ ] **Run the full backend test suite**

Run: `uv run pytest python/deepresearch_flow/ -q`
Expected: all tests PASS.

- [ ] **Run the full frontend test suite + typecheck**

Run: `cd frontend && npm test && npx vue-tsc -p tsconfig.app.json --noEmit`
Expected: all tests PASS; typecheck clean.

- [ ] **Smoke test CLI help**

Run: `uv run python -m deepresearch_flow paper db api serve --help`
Expected: output includes `--embed-db`, `--config`, `--search-access-token`.

- [ ] **Confirm no untracked files beyond plan expectations**

Run: `git status --short`
Expected: clean, or a coherent set of files matching the task commits above.

---

## Self-review

The plan covers every numbered section of the spec:

- §1 Goals → Tasks 17–19 (routing) + 24 (panel) + 26 (SearchView mount)
- §2 Non-Goals → zero schema/data/web changes enforced by the file scope; shared primitives imported but not modified
- §3 Endpoint contract → Task 16 (handlers) + Task 20 (integration test covers 401/400/503 paths and 200 shape)
- §4 Retrieval pipeline stages 1–8 → Tasks 4 (normalize), 5 (filters), 7 (dense), 8 (sparse), 9 (fusion), 10 (chunk select), 11 (dedup), 12 (rerank), 13 (MMR), 14 (response)
- §4 Failure paths → Task 15 pipeline tests cover `fts_unavailable`, `embedding_failed`, `reranker_failed`, `TOTAL_FAILURE`, `VECTOR_STORE_UNAVAILABLE`
- §4 Startup validation → Task 19 CLI block invokes `validate_index_meta` and fails with `ClickException`
- §5 File map → every listed path is created or modified by exactly one task
- §6 Configuration → Task 1 (fields) + Task 19 (loading + env precedence)
- §7 CLI and integration → Task 18 (`create_app` signature) + Task 19 (flags + startup sequence) + Task 17 (routes factory)
- §8 Backend tests → every row in the per-module table becomes a test file in Tasks 3–15; §8 e2e → Task 20
- §9 Frontend tests → Tasks 21–25 plus Task 27 integration; all specified scenarios covered

Interface consistency checked: `ChunkHit`, `PaperHit`, `FusedPaper`, `SelectedChunk`, `RerankOutcome`, `RequestSpec`, `AdvancedSearchContext`, `NormalizedQuery`, `ParsedFilters`, `YearRange` have consistent field names across all tasks that consume them.

No placeholders remain.



