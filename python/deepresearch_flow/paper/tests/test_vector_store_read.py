from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    decode_vector_b64,
    encode_vector_b64,
    delete_groups,
    open_store,
    read_all_chunks,
    write_chunks,
)


def test_encode_decode_vector_b64_roundtrip() -> None:
    original = [0.1, 0.2, 0.3, 1.0]
    encoded = encode_vector_b64(original)
    assert isinstance(encoded, str)
    decoded = decode_vector_b64(encoded, 4)
    for left, right in zip(original, decoded, strict=True):
        assert abs(left - right) < 1e-6


def test_decode_vector_b64_wrong_dim_raises() -> None:
    encoded = encode_vector_b64([0.1, 0.2])
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


def test_delete_groups_escapes_filter_literals(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc'a__shared_title_0",
            doc_id="doc'a",
            source_path="a.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="A",
            content_hash="ha",
            vector=[0.1] * 4,
            title="A",
            year=2024,
            authors="A",
            venue="V",
            tags="t",
        ),
        ChunkRow(
            id="docb__shared_title_0",
            doc_id="docb",
            source_path="b.md",
            template_tag="",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="B",
            content_hash="hb",
            vector=[0.2] * 4,
            title="B",
            year=2024,
            authors="B",
            venue="V",
            tags="t",
        ),
    ]
    write_chunks(db, rows, dimensions=4)
    delete_groups(db, [("doc'a", "_shared")])
    chunks = read_all_chunks(db)
    assert [chunk['doc_id'] for chunk in chunks] == ['docb']
