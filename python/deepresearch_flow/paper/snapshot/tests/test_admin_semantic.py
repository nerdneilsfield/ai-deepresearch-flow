from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

import httpx
import pytest
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.vector_store import ChunkRow, compute_group_hash, encode_vector_b64, load_index_meta, open_store, read_chunks_for_group, save_index_meta, write_chunks


def _make_admin_app(tmp_path: Path):
    snapshot_db = tmp_path / "test.db"
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    app = create_admin_app(
        snapshot_db=snapshot_db,
        admin_token="test-token",
        embed_db=embed_dir,
        embed_dimensions=4,
    )
    return app, embed_dir


def _make_app(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    app, embed_dir = _make_admin_app(tmp_path)
    headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
    return TestClient(app, raise_server_exceptions=False), headers, embed_dir


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


def _meta(*, model: str = "m", canonical_model: str | None = None) -> dict:
    payload = {"model": model, "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}
    if canonical_model is not None:
        payload["canonical_model"] = canonical_model
    return payload


def _body(
    doc_id: str,
    tag: str,
    chunks: list[dict],
    *,
    group_hash: str | None = None,
    part_index: int = 0,
    part_count: int = 1,
    index_meta: dict | None = None,
) -> dict:
    return {
        "index_meta": index_meta or _meta(),
        "group": {
            "doc_id": doc_id,
            "template_tag": tag,
            "group_hash": group_hash or compute_group_hash([str(chunk["content_hash"]) for chunk in chunks]),
            "part_index": part_index,
            "part_count": part_count,
            "is_final_part": part_index == part_count - 1,
        },
        "chunks": chunks,
    }


def test_inserts_new_chunks(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', 0), _chunk('d1', 'simple', 1)]), headers=headers)
    assert resp.status_code == 200
    assert resp.json()['inserted'] == 2


def test_skips_unchanged(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    body = _body('d1', 'simple', [_chunk('d1', 'simple', 0)])
    client.post('/semantic/chunks/batch', json=body, headers=headers)
    resp = client.post('/semantic/chunks/batch', json=body, headers=headers)
    assert resp.json()['skipped'] == 1
    assert resp.json()['inserted'] == 0


def test_updates_changed(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', 0, content_hash='old')]), headers=headers)
    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', 0, content_hash='new')]), headers=headers)
    assert resp.json()['updated'] == 1


def test_deletes_orphans_on_complete_group(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', i) for i in range(3)]), headers=headers)
    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', 0, content_hash='new')]), headers=headers)
    assert resp.json()['deleted'] == 2


def test_requires_auth(tmp_path: Path) -> None:
    client, _, _ = _make_app(tmp_path)
    resp = client.post('/semantic/chunks/batch', json=_body('d1', '', []))
    assert resp.status_code == 401


def test_multi_part_staging(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    group_hash = compute_group_hash(['h_0', 'h_1'])
    resp0 = client.post('/semantic/chunks/batch', json=_body('d1', 'deep', [_chunk('d1', 'deep', 0)], group_hash=group_hash, part_index=0, part_count=2), headers=headers)
    assert resp0.status_code == 200
    assert resp0.json()['inserted'] == 0

    resp1 = client.post('/semantic/chunks/batch', json=_body('d1', 'deep', [_chunk('d1', 'deep', 1)], group_hash=group_hash, part_index=1, part_count=2), headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()['inserted'] == 2


def test_logs_semantic_batch_phase_timings(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    client, headers, _ = _make_app(tmp_path)
    caplog.set_level("INFO", logger="deepresearch_flow.paper.snapshot.admin")

    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [_chunk('d1', 'simple', 0)]), headers=headers)

    assert resp.status_code == 200
    assert "semantic batch request doc=d1 tag=simple part=1/1 chunk_count=1" in caplog.text
    assert "phase=validate_index_meta" in caplog.text
    assert "phase=open_store" in caplog.text
    assert "phase=read_existing_state" in caplog.text
    assert "phase=delete_groups" in caplog.text
    assert "phase=write_chunks" in caplog.text
    assert "phase=save_index_meta" in caplog.text
    assert "semantic batch done doc=d1 tag=simple" in caplog.text


def test_logs_staging_progress_before_final_apply(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    client, headers, _ = _make_app(tmp_path)
    caplog.set_level("INFO", logger="deepresearch_flow.paper.snapshot.admin")
    group_hash = compute_group_hash(['h_0', 'h_1'])

    resp0 = client.post(
        '/semantic/chunks/batch',
        json=_body('d1', 'deep', [_chunk('d1', 'deep', 0)], group_hash=group_hash, part_index=0, part_count=2),
        headers=headers,
    )
    resp1 = client.post(
        '/semantic/chunks/batch',
        json=_body('d1', 'deep', [_chunk('d1', 'deep', 1)], group_hash=group_hash, part_index=1, part_count=2),
        headers=headers,
    )

    assert resp0.status_code == 200
    assert resp1.status_code == 200
    assert "semantic batch staged doc=d1 tag=deep part=1/2 staged_parts=1/2 waiting_for_more_parts=1" in caplog.text
    assert "semantic batch ready doc=d1 tag=deep part=2/2 staged_parts=2/2" in caplog.text


def test_rejects_oversized_payload(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    huge_chunks = [_chunk('d1', 'simple', i) for i in range(5)]
    for chunk in huge_chunks:
        chunk['text'] = 'x' * 8_000_000
    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', huge_chunks), headers=headers)
    assert resp.status_code == 413


def test_rejects_dimension_mismatch(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    body = _body('d1', 'simple', [_chunk('d1', 'simple', 0)])
    body['index_meta']['dimensions'] = 999
    resp = client.post('/semantic/chunks/batch', json=body, headers=headers)
    assert resp.status_code == 400
    assert 'dimension' in resp.json().get('error', '').lower()


def test_rejects_group_hash_mismatch(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    body = _body('d1', 'simple', [_chunk('d1', 'simple', 0)], group_hash='wrong')
    resp = client.post('/semantic/chunks/batch', json=body, headers=headers)
    assert resp.status_code == 400
    assert 'group_hash' in resp.json()['detail']


def test_rejects_invalid_vector_payload_per_chunk(tmp_path: Path) -> None:
    client, headers, _ = _make_app(tmp_path)
    bad = _chunk('d1', 'simple', 0)
    bad['vector_b64'] = 'not-base64'
    resp = client.post('/semantic/chunks/batch', json=_body('d1', 'simple', [bad]), headers=headers)
    assert resp.status_code == 400
    assert 'invalid vector payload' in resp.json()['detail']


def test_accepts_alias_model_when_canonical_model_matches_existing_index(tmp_path: Path) -> None:
    client, headers, embed_dir = _make_app(tmp_path)
    (embed_dir / "index_meta.json").write_text(
        json.dumps(
            {
                "model": "Qwen3-Embedding-4B",
                "canonical_model": "Qwen3-Embedding-4B",
                "dimensions": 4,
                "normalized": True,
                "provider": "siliconflow",
                "index_version": 1,
            }
        ),
        encoding="utf-8",
    )

    resp = client.post(
        '/semantic/chunks/batch',
        json=_body(
            'd1',
            'simple',
            [_chunk('d1', 'simple', 0)],
            index_meta=_meta(model="qwen3-embedding:4b", canonical_model="Qwen3-Embedding-4B"),
        ),
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()['inserted'] == 1


def test_model_mismatch_returns_400_instead_of_500(tmp_path: Path) -> None:
    client, headers, embed_dir = _make_app(tmp_path)
    (embed_dir / "index_meta.json").write_text(
        json.dumps(
            {
                "model": "Qwen3-Embedding-4B",
                "canonical_model": "Qwen3-Embedding-4B",
                "dimensions": 4,
                "normalized": True,
                "provider": "siliconflow",
                "index_version": 1,
            }
        ),
        encoding="utf-8",
    )

    resp = client.post(
        '/semantic/chunks/batch',
        json=_body(
            'd1',
            'simple',
            [_chunk('d1', 'simple', 0)],
            index_meta=_meta(model="other-model", canonical_model="other-model"),
        ),
        headers=headers,
    )

    assert resp.status_code == 400
    assert "Index model mismatch" in resp.json()["detail"]


def test_multi_part_write_failure_keeps_staged_parts(tmp_path: Path) -> None:
    import sqlite3
    from unittest.mock import patch

    client, headers, _ = _make_app(tmp_path)
    group_hash = compute_group_hash(['h_0', 'h_1'])
    resp0 = client.post('/semantic/chunks/batch', json=_body('d1', 'deep', [_chunk('d1', 'deep', 0)], group_hash=group_hash, part_index=0, part_count=2), headers=headers)
    assert resp0.status_code == 200

    with patch('deepresearch_flow.paper.vector_store.write_chunks', side_effect=RuntimeError('boom')):
        resp1 = client.post('/semantic/chunks/batch', json=_body('d1', 'deep', [_chunk('d1', 'deep', 1)], group_hash=group_hash, part_index=1, part_count=2), headers=headers)
    assert resp1.status_code == 500

    conn = sqlite3.connect(tmp_path / 'test.db')
    try:
        count = conn.execute('SELECT COUNT(*) FROM semantic_staging WHERE doc_id = ? AND template_tag = ? AND group_hash = ?', ('d1', 'deep', group_hash)).fetchone()[0]
    finally:
        conn.close()
    assert count == 2


@pytest.mark.anyio
async def test_concurrent_same_group_writes_do_not_duplicate_rows(tmp_path: Path, monkeypatch) -> None:
    app, embed_dir = _make_admin_app(tmp_path)
    headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}

    original_write_chunks = write_chunks

    def slow_write_chunks(db, rows, *, dimensions):  # noqa: ANN001
        time.sleep(0.05)
        return original_write_chunks(db, rows, dimensions=dimensions)

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.write_chunks", slow_write_chunks)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp_a, resp_b = await asyncio.gather(
            client.post(
                "/semantic/chunks/batch",
                json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="old")]),
                headers=headers,
            ),
            client.post(
                "/semantic/chunks/batch",
                json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="new")]),
                headers=headers,
            ),
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    rows = read_chunks_for_group(open_store(embed_dir), "d1", "simple")
    assert len(rows) == 1
    assert rows[0]["content_hash"] in {"old_0", "new_0"}


@pytest.mark.anyio
async def test_concurrent_writes_preserve_index_meta_counts(tmp_path: Path, monkeypatch) -> None:
    app, embed_dir = _make_admin_app(tmp_path)
    headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}

    db = open_store(embed_dir)
    write_chunks(
        db,
        [
            ChunkRow(
                id="seed_simple_content_0",
                doc_id="seed",
                source_path="seed.md",
                template_tag="simple",
                chunk_type="content",
                chunk_index=0,
                field_name="summary",
                lang="",
                text="seed",
                content_hash="seed_0",
                vector=[0.1, 0.2, 0.3, 0.4],
                title="Seed",
                year=2024,
                authors="A",
                venue="V",
                tags="t",
            )
        ],
        dimensions=4,
    )
    save_index_meta(
        embed_dir,
        {
            "model": "m",
            "canonical_model": "m",
            "dimensions": 4,
            "normalized": True,
            "provider": "p",
            "index_version": 1,
            "doc_count": 1,
            "template_count": 1,
            "chunk_count": 1,
            "last_updated": None,
        },
    )

    original_write_chunks = write_chunks

    def slow_write_chunks(db, rows, *, dimensions):  # noqa: ANN001
        time.sleep(0.05)
        return original_write_chunks(db, rows, dimensions=dimensions)

    monkeypatch.setattr("deepresearch_flow.paper.vector_store.write_chunks", slow_write_chunks)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp_a, resp_b = await asyncio.gather(
            client.post(
                "/semantic/chunks/batch",
                json=_body("d1", "simple", [_chunk("d1", "simple", 0, content_hash="a")]),
                headers=headers,
            ),
            client.post(
                "/semantic/chunks/batch",
                json=_body("d2", "simple", [_chunk("d2", "simple", 0, content_hash="b")]),
                headers=headers,
            ),
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    meta = load_index_meta(embed_dir)
    assert meta["doc_count"] == 3
    assert meta["template_count"] == 1
    assert meta["chunk_count"] == 3
