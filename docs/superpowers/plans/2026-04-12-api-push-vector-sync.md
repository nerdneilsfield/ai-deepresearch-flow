# API Push Vector Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional `--embed-db` to `paper db api push` that pushes local LanceDB chunk rows (with base64-encoded vectors) to a new remote admin endpoint. Server stores in LanceDB via shared `vector_store.py`. No changes to local embed/search/build.

**Architecture:** Client reads chunks from local LanceDB, groups by `(doc_id, template_tag)`, encodes vectors as base64 float32, splits oversized groups into numbered parts, and pushes to `POST /api/v1/admin/semantic/chunks/batch`. Server validates index_meta, stages multi-part uploads in SQLite, and on group completion decodes vectors and writes to server-side LanceDB via existing `vector_store.write_chunks()` / `delete_groups()`.

**Tech Stack:** Python 3.14, httpx, click, Starlette, LanceDB, SQLite (staging only), struct + base64, pytest via `uv run pytest`

**Spec:** `docs/superpowers/specs/2026-04-12-api-push-vector-sync-design.md`

**Hard boundary:** This plan only adds a transport channel. It does NOT modify `paper embed`, `paper search`, `/api/papers/semantic`, `paper db snapshot build`, or any existing `api push` default behavior.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/snapshot/push_semantic.py` | Create | Client-side: read LanceDB, group, encode vectors to base64, split parts, push via httpx, collect stats |
| `python/deepresearch_flow/paper/snapshot/admin.py` | Modify | Server-side: new `_admin_ingest_semantic_chunks` handler — validate, stage parts, reconcile complete groups via `vector_store` |
| `python/deepresearch_flow/paper/snapshot/schema.py` | Modify | Add `semantic_staging` table DDL (multi-part staging only; chunk storage is LanceDB) |
| `python/deepresearch_flow/paper/db.py` | Modify | Add `--embed-db` option to `api push`, wire semantic push phase after metadata+static |
| `python/deepresearch_flow/paper/vector_store.py` | Modify | Add `read_all_chunks()` function |
| `python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py` | Create | Client-side grouping, base64 encoding, push tests |
| `python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py` | Create | Server-side ingest, upsert, cleanup, multi-part staging tests |

---

### Task 1: Vector Base64 Encoding Helpers + `read_all_chunks`

**Files:**
- Modify: `python/deepresearch_flow/paper/vector_store.py`
- Create: `python/deepresearch_flow/paper/tests/test_vector_store_read.py`

- [ ] **Step 1: Write failing tests**

Create `python/deepresearch_flow/paper/tests/test_vector_store_read.py`:

```python
from __future__ import annotations

import base64
import struct
from pathlib import Path

from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    encode_vector_b64,
    decode_vector_b64,
    open_store,
    read_all_chunks,
    write_chunks,
)


def test_encode_decode_vector_b64_roundtrip() -> None:
    original = [0.1, 0.2, 0.3, 1.0]
    encoded = encode_vector_b64(original)
    assert isinstance(encoded, str)
    decoded = decode_vector_b64(encoded, 4)
    for a, b in zip(original, decoded):
        assert abs(a - b) < 1e-6


def test_decode_vector_b64_wrong_dim_raises() -> None:
    encoded = encode_vector_b64([0.1, 0.2])
    import pytest
    with pytest.raises(ValueError, match="dimension"):
        decode_vector_b64(encoded, 10)


def test_read_all_chunks_returns_dicts(tmp_path: Path) -> None:
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
            text="Test Title",
            content_hash="abc",
            vector=[0.1] * 4,
            title="Test Title",
            year=2024,
            authors="Author A",
            venue="NeurIPS",
            tags="ml",
        ),
    ]
    write_chunks(db, rows, dimensions=4)
    chunks = read_all_chunks(db)
    assert len(chunks) == 1
    assert chunks[0]["doc_id"] == "doc1"
    assert isinstance(chunks[0]["vector"], list)
    assert len(chunks[0]["vector"]) == 4


def test_read_all_chunks_empty_store(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    assert read_all_chunks(db) == []
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store_read.py -v`

Expected: FAIL

- [ ] **Step 3: Implement helpers**

In `python/deepresearch_flow/paper/vector_store.py`, add:

```python
import base64
import struct


def encode_vector_b64(vector: list[float]) -> str:
    """Encode float list to base64 little-endian float32 bytes."""
    packed = struct.pack(f"<{len(vector)}f", *vector)
    return base64.b64encode(packed).decode("ascii")


def decode_vector_b64(b64: str, dimensions: int) -> list[float]:
    """Decode base64 little-endian float32 bytes to float list."""
    raw = base64.b64decode(b64)
    expected_bytes = dimensions * 4
    if len(raw) != expected_bytes:
        raise ValueError(
            f"Vector dimension mismatch: expected {dimensions} floats "
            f"({expected_bytes} bytes), got {len(raw)} bytes"
        )
    return list(struct.unpack(f"<{dimensions}f", raw))


def read_all_chunks(db: "lancedb.DBConnection") -> list[dict[str, Any]]:
    """Read all chunk rows from LanceDB as list of dicts."""
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    return table.to_arrow().to_pylist()
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_vector_store_read.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/vector_store.py python/deepresearch_flow/paper/tests/test_vector_store_read.py
git commit -m "feat: add vector base64 encode/decode and read_all_chunks"
```

---

### Task 2: Client-Side Semantic Push

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/push_semantic.py`
- Create: `python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py`

- [ ] **Step 1: Write failing tests**

Create `python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py`:

```python
from __future__ import annotations

import json
from unittest import mock

import pytest

from deepresearch_flow.paper.snapshot.push_semantic import (
    PushSemanticStats,
    group_chunks_for_push,
    push_semantic_chunks,
)
from deepresearch_flow.paper.vector_store import decode_vector_b64


def _make_chunk(doc_id: str, template_tag: str, idx: int, *, text: str = "test") -> dict:
    return {
        "id": f"{doc_id}_{template_tag or '_shared'}_content_{idx}",
        "doc_id": doc_id,
        "source_path": "test.md",
        "template_tag": template_tag,
        "chunk_type": "content",
        "chunk_index": idx,
        "field_name": "summary",
        "lang": "",
        "text": text,
        "content_hash": f"hash_{idx}",
        "vector": [0.1, 0.2, 0.3, 0.4],
        "title": "Test",
        "year": 2024,
        "authors": "A",
        "venue": "V",
        "tags": "t",
    }


def test_group_single_group_one_part() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(5)]
    batches = group_chunks_for_push(chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) == 1
    b = batches[0]
    assert b["group"]["doc_id"] == "doc1"
    assert b["group"]["part_index"] == 0
    assert b["group"]["part_count"] == 1
    assert b["group"]["is_final_part"] is True
    assert len(b["chunks"]) == 5
    # Verify vectors are base64 encoded
    assert "vector_b64" in b["chunks"][0]
    assert "vector_dim" in b["chunks"][0]
    assert "vector" not in b["chunks"][0]
    decoded = decode_vector_b64(b["chunks"][0]["vector_b64"], b["chunks"][0]["vector_dim"])
    assert len(decoded) == 4


def test_group_large_group_splits() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(20)]
    batches = group_chunks_for_push(chunks, max_rows=5, max_payload_bytes=16_000_000)
    assert len(batches) == 4
    for i, b in enumerate(batches):
        assert b["group"]["doc_id"] == "doc1"
        assert b["group"]["part_index"] == i
        assert b["group"]["part_count"] == 4
        assert b["group"]["is_final_part"] == (i == 3)


def test_group_splits_by_payload_size_not_just_rows() -> None:
    # Each chunk has a huge text field — 5 chunks exceed 16MB even though max_rows=100
    big_chunks = [_make_chunk("doc1", "simple", i, text="x" * 4_000_000) for i in range(5)]
    batches = group_chunks_for_push(big_chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) > 1  # must split despite only 5 rows
    for b in batches:
        assert b["group"]["doc_id"] == "doc1"
        assert b["group"]["part_count"] == len(batches)


def test_group_multiple_groups_separate_batches() -> None:
    chunks = [
        *[_make_chunk("doc1", "simple", i) for i in range(3)],
        *[_make_chunk("doc2", "", i) for i in range(2)],
    ]
    batches = group_chunks_for_push(chunks, max_rows=100, max_payload_bytes=16_000_000)
    assert len(batches) == 2
    assert {b["group"]["doc_id"] for b in batches} == {"doc1", "doc2"}


def test_push_sends_requests_and_accumulates_stats() -> None:
    chunks = [_make_chunk("doc1", "simple", i) for i in range(3)]
    index_meta = {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}

    with mock.patch("deepresearch_flow.paper.snapshot.push_semantic.httpx.Client") as mock_cls:
        mock_client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"received": 3, "inserted": 3, "updated": 0, "skipped": 0, "deleted": 0}
        mock_resp.raise_for_status = mock.MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = mock.MagicMock(return_value=mock_client)
        mock_client.__exit__ = mock.MagicMock(return_value=False)
        mock_cls.return_value = mock_client

        from deepresearch_flow.paper.snapshot.push import RemoteConfig
        config = RemoteConfig(api_base_url="http://localhost", admin_token="tok")

        stats = push_semantic_chunks(chunks, index_meta, config)
        assert stats.inserted == 3
        assert stats.batches_sent == 1

        call_body = mock_client.post.call_args[1]["json"]
        assert "index_meta" in call_body
        assert "group" in call_body
        assert "chunks" in call_body
        assert "vector_b64" in call_body["chunks"][0]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py -v`

Expected: FAIL

- [ ] **Step 3: Implement push_semantic.py**

Create `python/deepresearch_flow/paper/snapshot/push_semantic.py`:

```python
"""Client-side semantic chunk push to remote admin API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from deepresearch_flow.paper.snapshot.push import RemoteConfig
from deepresearch_flow.paper.vector_store import compute_group_hash, encode_vector_b64

logger = logging.getLogger(__name__)


@dataclass
class PushSemanticStats:
    received: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    batches_sent: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def _encode_chunk_for_wire(chunk: dict[str, Any]) -> dict[str, Any]:
    """Convert a LanceDB chunk row to wire format: replace vector with vector_b64 + vector_dim."""
    wire = {k: v for k, v in chunk.items() if k != "vector"}
    vector = chunk["vector"]
    wire["vector_b64"] = encode_vector_b64(vector)
    wire["vector_dim"] = len(vector)
    return wire


def _estimate_chunk_bytes(chunk: dict[str, Any]) -> int:
    """Rough estimate of a single encoded chunk's JSON size."""
    return len(json.dumps(chunk, ensure_ascii=False).encode())


def group_chunks_for_push(
    chunks: list[dict[str, Any]],
    *,
    max_rows: int = 100,
    max_payload_bytes: int = 16_000_000,
) -> list[dict[str, Any]]:
    """Group chunks by (doc_id, template_tag), encode vectors, split into request payloads.

    Uses dual-gate splitting: accumulates chunks until EITHER max_rows or
    max_payload_bytes is exceeded, then cuts a new part. This ensures every
    part respects both limits even when individual chunks are large.

    A fixed 256KB overhead is reserved for index_meta, group metadata, and
    JSON structure wrapping, so the effective chunk budget per part is
    max_payload_bytes - 256KB.
    """
    _OVERHEAD_BYTES = 256 * 1024
    effective_limit = max(max_payload_bytes - _OVERHEAD_BYTES, 1)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in chunks:
        key = (chunk["doc_id"], chunk["template_tag"])
        groups.setdefault(key, []).append(chunk)

    batches: list[dict[str, Any]] = []
    for (doc_id, template_tag), group_chunks in groups.items():
        group_hash = compute_group_hash([c["content_hash"] for c in group_chunks])
        encoded = [_encode_chunk_for_wire(c) for c in group_chunks]

        # Dual-gate split: accumulate until max_rows or effective payload limit exceeded
        parts: list[list[dict[str, Any]]] = []
        current_part: list[dict[str, Any]] = []
        current_bytes = 0
        for chunk in encoded:
            chunk_bytes = _estimate_chunk_bytes(chunk)
            would_exceed_rows = len(current_part) >= max_rows
            would_exceed_bytes = current_part and (current_bytes + chunk_bytes > effective_limit)
            if would_exceed_rows or would_exceed_bytes:
                parts.append(current_part)
                current_part = []
                current_bytes = 0
            current_part.append(chunk)
            current_bytes += chunk_bytes
        if current_part:
            parts.append(current_part)

        part_count = len(parts)
        for part_idx, part_chunks in enumerate(parts):
            batches.append({
                "group": {
                    "doc_id": doc_id,
                    "template_tag": template_tag,
                    "group_hash": group_hash,
                    "part_index": part_idx,
                    "part_count": part_count,
                    "is_final_part": part_idx == part_count - 1,
                },
                "chunks": part_chunks,
            })

    return batches


def push_semantic_chunks(
    chunks: list[dict[str, Any]],
    index_meta: dict[str, Any],
    config: RemoteConfig,
    *,
    max_rows: int = 100,
    max_payload_bytes: int = 16_000_000,
    timeout: float = 120.0,
    on_batch: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> PushSemanticStats:
    """Push semantic chunks to remote admin API."""
    batches = group_chunks_for_push(chunks, max_rows=max_rows, max_payload_bytes=max_payload_bytes)
    stats = PushSemanticStats()
    url = f"{config.api_base_url}/api/v1/admin/semantic/chunks/batch"
    headers = {
        "Authorization": f"Bearer {config.admin_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        for batch_idx, batch in enumerate(batches):
            body = {
                "index_meta": index_meta,
                "group": batch["group"],
                "chunks": batch["chunks"],
            }
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            stats.received += data.get("received", 0)
            stats.inserted += data.get("inserted", 0)
            stats.updated += data.get("updated", 0)
            stats.skipped += data.get("skipped", 0)
            stats.deleted += data.get("deleted", 0)
            stats.batches_sent += 1

            if on_batch:
                on_batch(batch_idx, len(batch["chunks"]), data)

    return stats
```

- [ ] **Step 4: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/push_semantic.py python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py
git commit -m "feat: add client-side semantic push with base64 vector encoding"
```

---

### Task 3: Server-Side Semantic Ingest Endpoint

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/schema.py`
- Modify: `python/deepresearch_flow/paper/snapshot/admin.py`
- Create: `python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py`

- [ ] **Step 1: Write failing tests**

Create `python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app, AdminConfig
from deepresearch_flow.paper.vector_store import encode_vector_b64


def _make_app(tmp_path: Path) -> tuple[TestClient, dict]:
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    cfg = AdminConfig(
        admin_token="test-token",
        snapshot_db=tmp_path / "test.db",
        embed_db=embed_dir,
        embed_dimensions=4,
    )
    app = create_admin_app(cfg)
    headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
    return TestClient(app, raise_server_exceptions=False), headers


def _chunk(doc_id: str, tag: str, idx: int, *, content_hash: str = "h") -> dict:
    return {
        "id": f"{doc_id}_{tag or '_shared'}_content_{idx}",
        "doc_id": doc_id,
        "source_path": "t.md",
        "template_tag": tag,
        "chunk_type": "content",
        "chunk_index": idx,
        "field_name": "summary",
        "lang": "",
        "text": f"Text {idx}",
        "content_hash": f"{content_hash}_{idx}",
        "vector_b64": encode_vector_b64([0.1, 0.2, 0.3, 0.4]),
        "vector_dim": 4,
        "title": "T",
        "year": 2024,
        "authors": "A",
        "venue": "V",
        "tags": "t",
    }


def _meta() -> dict:
    return {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}


def _body(doc_id: str, tag: str, chunks: list, *, group_hash: str = "gh", part_index: int = 0, part_count: int = 1) -> dict:
    return {
        "index_meta": _meta(),
        "group": {
            "doc_id": doc_id,
            "template_tag": tag,
            "group_hash": group_hash,
            "part_index": part_index,
            "part_count": part_count,
            "is_final_part": part_index == part_count - 1,
        },
        "chunks": chunks,
    }


def test_inserts_new_chunks(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    resp = client.post(
        "/semantic/chunks/batch",
        json=_body("d1", "simple", [_chunk("d1", "simple", 0), _chunk("d1", "simple", 1)]),
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 2


def test_skips_unchanged(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    body = _body("d1", "simple", [_chunk("d1", "simple", 0)])
    client.post("/semantic/chunks/batch", json=body, headers=headers)
    resp = client.post("/semantic/chunks/batch", json=body, headers=headers)
    assert resp.json()["skipped"] == 1
    assert resp.json()["inserted"] == 0


def test_updates_changed(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    client.post("/semantic/chunks/batch", json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="old")]), headers=headers)
    resp = client.post("/semantic/chunks/batch", json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="new")], group_hash="gh2"), headers=headers)
    assert resp.json()["updated"] == 1


def test_deletes_orphans_on_complete_group(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    client.post("/semantic/chunks/batch", json=_body("d1", "simple", [_chunk("d1", "simple", i) for i in range(3)]), headers=headers)
    resp = client.post("/semantic/chunks/batch", json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="new")], group_hash="gh2"), headers=headers)
    assert resp.json()["deleted"] == 2


def test_requires_auth(tmp_path: Path) -> None:
    client, _ = _make_app(tmp_path)
    resp = client.post("/semantic/chunks/batch", json=_body("d1", "", []))
    assert resp.status_code == 401


def test_multi_part_staging(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    # Part 0 of 2 — staged, not yet reconciled
    resp0 = client.post(
        "/semantic/chunks/batch",
        json=_body("d1", "deep", [_chunk("d1", "deep", 0)], group_hash="gh", part_index=0, part_count=2),
        headers=headers,
    )
    assert resp0.status_code == 200
    assert resp0.json()["inserted"] == 0  # staged only

    # Part 1 of 2 — triggers reconciliation
    resp1 = client.post(
        "/semantic/chunks/batch",
        json=_body("d1", "deep", [_chunk("d1", "deep", 1)], group_hash="gh", part_index=1, part_count=2),
        headers=headers,
    )
    assert resp1.status_code == 200
    assert resp1.json()["inserted"] == 2  # both parts written to LanceDB


def test_rejects_oversized_payload(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    # Send a body that exceeds 32MB hard limit
    huge_chunks = [_chunk("d1", "simple", i) for i in range(5)]
    for c in huge_chunks:
        c["text"] = "x" * 8_000_000  # ~40MB total
    body = _body("d1", "simple", huge_chunks)
    resp = client.post("/semantic/chunks/batch", json=body, headers=headers)
    assert resp.status_code == 413


def test_rejects_dimension_mismatch(tmp_path: Path) -> None:
    client, headers = _make_app(tmp_path)
    body = _body("d1", "simple", [_chunk("d1", "simple", 0)])
    body["index_meta"]["dimensions"] = 999
    resp = client.post("/semantic/chunks/batch", json=body, headers=headers)
    assert resp.status_code == 400
    assert "dimension" in resp.json().get("error", "").lower()
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py -v`

Expected: FAIL

- [ ] **Step 3: Add `semantic_staging` table to schema.py**

In `python/deepresearch_flow/paper/snapshot/schema.py`, add:

```sql
CREATE TABLE IF NOT EXISTS semantic_staging (
    staging_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    template_tag TEXT NOT NULL,
    group_hash TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    part_count INTEGER NOT NULL,
    chunk_data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(doc_id, template_tag, group_hash, part_index)
);
```

This is only for multi-part staging. Actual chunk storage is LanceDB.

- [ ] **Step 4: Extend `AdminConfig` and implement the handler**

In `python/deepresearch_flow/paper/snapshot/admin.py`:

Add to `AdminConfig`:

```python
embed_db: Path | None = None
embed_dimensions: int | None = None
```

Implement `_admin_ingest_semantic_chunks`:

```python
async def _admin_ingest_semantic_chunks(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    cfg: AdminConfig = request.app.state.admin_cfg
    if cfg.embed_db is None:
        return JSONResponse({"error": "Semantic storage not configured"}, status_code=503)

    # Hard payload size limit (32MB)
    content_length = int(request.headers.get("content-length", 0))
    if content_length > 32_000_000:
        return JSONResponse({"error": "Payload Too Large"}, status_code=413)

    body = await request.json()

    # Also check parsed body size as fallback (content-length may be absent)
    estimated_size = len(json.dumps(body, ensure_ascii=False).encode()) if content_length == 0 else content_length
    if estimated_size > 32_000_000:
        return JSONResponse({"error": "Payload Too Large"}, status_code=413)

    index_meta = body.get("index_meta", {})
    group_info = body.get("group", {})
    chunks = body.get("chunks", [])

    # Validate dimensions
    if cfg.embed_dimensions and index_meta.get("dimensions") != cfg.embed_dimensions:
        return JSONResponse(
            {"error": f"Dimension mismatch: server expects {cfg.embed_dimensions}, got {index_meta.get('dimensions')}"},
            status_code=400,
        )

    doc_id = group_info["doc_id"]
    template_tag = group_info["template_tag"]
    group_hash = group_info["group_hash"]
    part_index = group_info["part_index"]
    part_count = group_info["part_count"]

    if part_count > 1:
        # Stage this part
        _stage_part(cfg, doc_id, template_tag, group_hash, part_index, part_count, chunks)
        staged_count = _count_staged_parts(cfg, doc_id, template_tag, group_hash)
        if staged_count < part_count:
            return JSONResponse({"received": len(chunks), "inserted": 0, "updated": 0, "skipped": 0, "deleted": 0})
        # All parts arrived — reconstruct and process
        all_chunks = _collect_staged_parts(cfg, doc_id, template_tag, group_hash)
        _clear_staged_parts(cfg, doc_id, template_tag, group_hash)
    else:
        all_chunks = chunks

    # Decode vectors and build ChunkRows
    from deepresearch_flow.paper.vector_store import (
        ChunkRow, decode_vector_b64, delete_groups, open_store, read_all_chunks, write_chunks,
    )
    dimensions = index_meta["dimensions"]
    db = open_store(cfg.embed_db)

    # Read existing chunks for this group
    existing = {}  # id -> content_hash
    all_existing = read_all_chunks(db)
    for row in all_existing:
        if row["doc_id"] == doc_id and row["template_tag"] == template_tag:
            existing[row["id"]] = row["content_hash"]

    incoming_ids = set()
    to_write: list[ChunkRow] = []
    inserted = 0
    updated = 0
    skipped = 0

    for c in all_chunks:
        chunk_id = c["id"]
        incoming_ids.add(chunk_id)
        vector = decode_vector_b64(c["vector_b64"], c["vector_dim"])

        if chunk_id in existing:
            if existing[chunk_id] == c["content_hash"]:
                skipped += 1
                continue
            else:
                updated += 1
        else:
            inserted += 1

        to_write.append(ChunkRow(
            id=chunk_id, doc_id=c["doc_id"], source_path=c.get("source_path", ""),
            template_tag=c["template_tag"], chunk_type=c["chunk_type"],
            chunk_index=c["chunk_index"], field_name=c.get("field_name", ""),
            lang=c.get("lang", ""), text=c["text"], content_hash=c["content_hash"],
            vector=vector, title=c.get("title", ""), year=c.get("year", 0),
            authors=c.get("authors", ""), venue=c.get("venue", ""), tags=c.get("tags", ""),
        ))

    # Delete orphans in this group
    orphan_ids = set(existing.keys()) - incoming_ids
    deleted = len(orphan_ids)

    # Apply: delete group, write new
    if to_write or orphan_ids:
        template_key = template_tag if template_tag else "_shared"
        delete_groups(db, [(doc_id, template_key)])
        # Re-add all current chunks (including unchanged ones we skipped counting)
        all_current = []
        for c in all_chunks:
            vector = decode_vector_b64(c["vector_b64"], c["vector_dim"])
            all_current.append(ChunkRow(
                id=c["id"], doc_id=c["doc_id"], source_path=c.get("source_path", ""),
                template_tag=c["template_tag"], chunk_type=c["chunk_type"],
                chunk_index=c["chunk_index"], field_name=c.get("field_name", ""),
                lang=c.get("lang", ""), text=c["text"], content_hash=c["content_hash"],
                vector=vector, title=c.get("title", ""), year=c.get("year", 0),
                authors=c.get("authors", ""), venue=c.get("venue", ""), tags=c.get("tags", ""),
            ))
        if all_current:
            write_chunks(db, all_current, dimensions=dimensions)

    return JSONResponse({
        "received": len(all_chunks),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "deleted": deleted,
    })
```

Register route in the admin app routes list:

```python
Route("/semantic/chunks/batch", _admin_ingest_semantic_chunks, methods=["POST"]),
```

- [ ] **Step 5: Implement staging helpers**

In `admin.py`, add SQLite staging functions:

```python
def _ensure_staging_table(cfg: AdminConfig) -> None:
    conn = _open_rw_conn(cfg.snapshot_db)
    conn.execute(SEMANTIC_STAGING_DDL)
    conn.commit()
    conn.close()

def _stage_part(cfg, doc_id, template_tag, group_hash, part_index, part_count, chunks):
    _ensure_staging_table(cfg)
    conn = _open_rw_conn(cfg.snapshot_db)

    # Consistency check: if parts already exist for this group, verify part_count and group_hash match
    existing = conn.execute(
        "SELECT part_count, group_hash FROM semantic_staging WHERE doc_id=? AND template_tag=? LIMIT 1",
        (doc_id, template_tag),
    ).fetchone()
    if existing:
        if existing[0] != part_count or existing[1] != group_hash:
            # Stale parts from a previous attempt — clear them before staging new group
            conn.execute(
                "DELETE FROM semantic_staging WHERE doc_id=? AND template_tag=?",
                (doc_id, template_tag),
            )

    conn.execute(
        "INSERT OR REPLACE INTO semantic_staging (doc_id, template_tag, group_hash, part_index, part_count, chunk_data) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, template_tag, group_hash, part_index, part_count, json.dumps(chunks)),
    )
    conn.commit()
    conn.close()

def _count_staged_parts(cfg, doc_id, template_tag, group_hash) -> int:
    conn = _open_ro_conn(cfg.snapshot_db)
    row = conn.execute(
        "SELECT COUNT(*) FROM semantic_staging WHERE doc_id=? AND template_tag=? AND group_hash=?",
        (doc_id, template_tag, group_hash),
    ).fetchone()
    conn.close()
    return row[0]

def _collect_staged_parts(cfg, doc_id, template_tag, group_hash) -> list[dict]:
    conn = _open_ro_conn(cfg.snapshot_db)
    rows = conn.execute(
        "SELECT chunk_data FROM semantic_staging WHERE doc_id=? AND template_tag=? AND group_hash=? ORDER BY part_index",
        (doc_id, template_tag, group_hash),
    ).fetchall()
    conn.close()
    all_chunks = []
    for row in rows:
        all_chunks.extend(json.loads(row[0]))
    return all_chunks

def _clear_staged_parts(cfg, doc_id, template_tag, group_hash) -> None:
    conn = _open_rw_conn(cfg.snapshot_db)
    conn.execute(
        "DELETE FROM semantic_staging WHERE doc_id=? AND template_tag=? AND group_hash=?",
        (doc_id, template_tag, group_hash),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 6: Run tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add \
  python/deepresearch_flow/paper/snapshot/schema.py \
  python/deepresearch_flow/paper/snapshot/admin.py \
  python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py
git commit -m "feat: add server-side semantic ingest with LanceDB storage and multi-part staging"
```

---

### Task 4: Wire `--embed-db` into `paper db api push`

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py`

- [ ] **Step 1: Add `--embed-db` option to `api push`**

In `python/deepresearch_flow/paper/db.py`, find the `api_push` command and add:

```python
@click.option("--embed-db", "embed_db", default=None, help="LanceDB directory for semantic chunk push")
```

- [ ] **Step 2: Define `--embed-db` interaction with existing flags**

Before the semantic push phase, add guard logic:

```python
    # --embed-db interaction with existing flags
    if embed_db and only_storage:
        raise click.ClickException("--embed-db cannot be combined with --only-storage")
    if embed_db and dry_run:
        embed_db = None  # dry-run skips semantic push silently
```

Rules:
- `--only-storage --embed-db` → error (semantic push is an API operation, not storage)
- `--only-api --embed-db` → allowed (both are API operations)
- `--dry-run --embed-db` → semantic push silently skipped (consistent with dry-run skipping real API calls)

- [ ] **Step 3: Add semantic push phase after metadata + static**

At the end of `api_push`, after existing metadata/static phases:

```python
    if embed_db:
        from pathlib import Path as _Path
        from deepresearch_flow.paper.vector_store import load_index_meta, open_store, read_all_chunks
        from deepresearch_flow.paper.snapshot.push_semantic import push_semantic_chunks

        embed_path = _Path(embed_db)
        if not embed_path.exists():
            raise click.ClickException(f"Embed DB not found: {embed_path}")

        index_meta = load_index_meta(embed_path)
        db = open_store(embed_path)
        all_chunks = read_all_chunks(db)

        if all_chunks:
            console.print(f"[cyan]Pushing {len(all_chunks)} semantic chunks...[/cyan]")
            semantic_stats = push_semantic_chunks(
                all_chunks,
                index_meta,
                config,
                on_batch=lambda idx, count, data: console.print(
                    f"  batch {idx + 1}: {count} chunks → "
                    f"ins={data.get('inserted', 0)} upd={data.get('updated', 0)} "
                    f"skip={data.get('skipped', 0)} del={data.get('deleted', 0)}"
                ),
            )
            console.print(
                f"[green]Semantic:[/green] batches={semantic_stats.batches_sent} "
                f"ins={semantic_stats.inserted} upd={semantic_stats.updated} "
                f"skip={semantic_stats.skipped} del={semantic_stats.deleted}"
            )
        else:
            console.print("[yellow]No semantic chunks to push.[/yellow]")
```

- [ ] **Step 4: Verify CLI help**

Run: `uv run python -m deepresearch_flow paper db api push --help`

Expected: `--embed-db` visible.

- [ ] **Step 5: Run existing push tests for regression**

Run: `uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push.py -v`

Expected: PASS — existing push behavior unaffected.

- [ ] **Step 6: Commit**

```bash
git add python/deepresearch_flow/paper/db.py
git commit -m "feat: wire --embed-db into paper db api push"
```

---

### Task 5: Full Verification

**Files:** No code changes.

- [ ] **Step 1: Run all new tests**

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_vector_store_read.py \
  python/deepresearch_flow/paper/snapshot/tests/test_push_semantic.py \
  python/deepresearch_flow/paper/snapshot/tests/test_admin_semantic.py -v
```

Expected: PASS

- [ ] **Step 2: Run full regression**

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/ \
  python/deepresearch_flow/paper/snapshot/tests/ \
  python/deepresearch_flow/translator/tests/ -q
```

Expected: all PASS — no existing behavior changed.

- [ ] **Step 3: CLI smoke tests**

```bash
uv run python -m deepresearch_flow paper db api push --help
uv run python -m deepresearch_flow paper --help
uv run python -m deepresearch_flow paper embed --help
uv run python -m deepresearch_flow paper search --help
```

Expected: all help outputs correct; embed/search unchanged.

- [ ] **Step 4: Commit if needed**

```bash
git add <files>
git commit -m "test: verification fixes for semantic push"
```
