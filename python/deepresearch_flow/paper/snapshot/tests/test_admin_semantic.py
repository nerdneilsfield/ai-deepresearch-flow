from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.vector_store import compute_group_hash, encode_vector_b64


def _make_app(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    snapshot_db = tmp_path / "test.db"
    embed_dir = tmp_path / "embed_vectors"
    embed_dir.mkdir()
    app = create_admin_app(
        snapshot_db=snapshot_db,
        admin_token="test-token",
        embed_db=embed_dir,
        embed_dimensions=4,
    )
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


def _meta() -> dict:
    return {"model": "m", "dimensions": 4, "normalized": True, "provider": "p", "index_version": 1}


def _body(doc_id: str, tag: str, chunks: list[dict], *, group_hash: str | None = None, part_index: int = 0, part_count: int = 1) -> dict:
    return {
        "index_meta": _meta(),
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
