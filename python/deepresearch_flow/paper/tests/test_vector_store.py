from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.chunker import Chunk
from deepresearch_flow.paper.vector_store import (
    INDEX_VERSION,
    ChunkRow,
    build_chunk_id,
    _chunks_schema,
    load_index_meta,
    open_store,
    query_vector,
    read_group_hashes_for_doc,
    read_group_keys,
    save_index_meta,
    validate_index_meta,
    write_chunks,
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
    save_index_meta(
        tmp_path,
        {
            "model": "bge-m3",
            "dimensions": 1024,
            "normalized": True,
            "provider": "ollama",
            "index_version": INDEX_VERSION,
        },
    )
    with pytest.raises(ValueError, match="model"):
        validate_index_meta(
            tmp_path,
            model="different-model",
            dimensions=1024,
            normalized=True,
            provider="ollama",
        )


def test_validate_index_meta_allows_same_canonical_model_with_different_provider_names(tmp_path: Path) -> None:
    save_index_meta(
        tmp_path,
        {
            "model": "Qwen3-Embedding-4B",
            "canonical_model": "Qwen3-Embedding-4B",
            "dimensions": 2560,
            "normalized": True,
            "provider": "siliconflow",
            "index_version": INDEX_VERSION,
        },
    )
    validate_index_meta(
        tmp_path,
        model="qwen3-embedding:4b",
        canonical_model="Qwen3-Embedding-4B",
        dimensions=2560,
        normalized=True,
        provider="ollama",
    )


def test_validate_index_meta_missing_creates(tmp_path: Path) -> None:
    validate_index_meta(
        tmp_path,
        model="bge-m3",
        dimensions=1024,
        normalized=True,
        provider="ollama",
    )
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
    write_chunks(db, rows, dimensions=1024)
    results = query_vector(db, [0.1] * 1024, top_k=5)
    assert len(results) >= 1
    assert results[0]["doc_id"] == "doc1"


def test_chunks_schema_uses_requested_dimensions() -> None:
    schema = _chunks_schema(2560)
    assert schema.field("vector").type.list_size == 2560


def test_read_group_hashes_for_doc_returns_only_requested_doc(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1_simple_title_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="simple",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Doc 1",
            content_hash="hash-a",
            vector=[0.1] * 4,
            title="Doc 1",
            year=2024,
            authors="A",
            venue="ACL",
            tags="x",
        ),
        ChunkRow(
            id="doc2_simple_title_0",
            doc_id="doc2",
            source_path="b.md",
            template_tag="simple",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Doc 2",
            content_hash="hash-b",
            vector=[0.2] * 4,
            title="Doc 2",
            year=2024,
            authors="B",
            venue="EMNLP",
            tags="y",
        ),
    ]
    write_chunks(db, rows, dimensions=4)
    hashes = read_group_hashes_for_doc(db, "doc1")
    assert set(hashes) == {"simple"}


def test_read_group_keys_returns_unique_group_keys(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1_simple_title_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="simple",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="Doc 1",
            content_hash="hash-a",
            vector=[0.1] * 4,
            title="Doc 1",
            year=2024,
            authors="A",
            venue="ACL",
            tags="x",
        ),
        ChunkRow(
            id="doc1_simple_abstract_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="simple",
            chunk_type="abstract",
            chunk_index=0,
            field_name="summary",
            lang="",
            text="Abstract",
            content_hash="hash-b",
            vector=[0.1] * 4,
            title="Doc 1",
            year=2024,
            authors="A",
            venue="ACL",
            tags="x",
        ),
    ]
    write_chunks(db, rows, dimensions=4)
    assert read_group_keys(db) == {("doc1", "simple")}
