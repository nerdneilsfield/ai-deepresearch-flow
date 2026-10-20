from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from deepresearch_flow.paper import vector_store
from deepresearch_flow.paper.chunker import Chunk
from deepresearch_flow.paper.vector_store import (
    INDEX_VERSION,
    ChunkRow,
    build_chunk_id,
    embed_write_lock,
    _chunks_schema,
    load_index_meta,
    open_store,
    optimize_store,
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


def test_validate_index_meta_allows_same_canonical_model_with_different_provider_names(
    tmp_path: Path,
) -> None:
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


def _unit_rows(vectors: np.ndarray, start: int = 0) -> list[ChunkRow]:
    return [
        ChunkRow(
            id=f"row-{start + i}",
            doc_id=f"doc-{(start + i) // 20}",
            source_path="",
            template_tag="",
            chunk_type="content",
            chunk_index=i,
            field_name="",
            lang="",
            text="",
            content_hash="h",
            vector=vector.tolist(),
            title="",
            year=0,
            authors="",
            venue="",
            tags="",
        )
        for i, vector in enumerate(vectors)
    ]


def test_optimize_store_indexes_large_table_and_queries_match_brute_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vector_store, "_VECTOR_INDEX_MIN_ROWS", 500)
    rng = np.random.default_rng(0)
    dim = 16
    first = rng.normal(size=(600, dim)).astype("float32")
    second = rng.normal(size=(100, dim)).astype("float32")
    vector_dir = tmp_path / "vectors"
    db = open_store(vector_dir)
    write_chunks(db, _unit_rows(first), dimensions=dim)

    optimize_store(vector_dir)
    write_chunks(open_store(vector_dir), _unit_rows(second, start=600), dimensions=dim)
    optimize_store(vector_dir)

    table = open_store(vector_dir).open_table("paper_chunks")
    assert any(index.columns == ["vector"] for index in table.list_indices())
    everything = np.vstack([first, second])
    normalized = everything / np.linalg.norm(everything, axis=1, keepdims=True)
    for row in (3, 250, 599, 600, 699):
        query = everything[row] + 0.01 * rng.normal(size=dim).astype("float32")
        expected = int(np.argmax(normalized @ (query / np.linalg.norm(query))))
        top = query_vector(open_store(vector_dir), query.tolist(), top_k=1)
        assert top[0]["id"] == f"row-{expected}"


def test_optimize_store_leaves_small_table_without_vector_index(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    vector_dir = tmp_path / "vectors"
    write_chunks(
        open_store(vector_dir), _unit_rows(rng.normal(size=(50, 8)).astype("float32")), dimensions=8
    )

    optimize_store(vector_dir)

    table = open_store(vector_dir).open_table("paper_chunks")
    assert not any(index.columns == ["vector"] for index in table.list_indices())


def test_query_vector_filter_on_distant_cluster_returns_all_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vector_store, "_VECTOR_INDEX_MIN_ROWS", 500)
    rng = np.random.default_rng(2)
    dim = 16
    near = np.ones(dim, dtype="float32") + 0.05 * rng.normal(size=(1500, dim)).astype("float32")
    far = -np.ones(dim, dtype="float32") + 0.05 * rng.normal(size=(8, dim)).astype("float32")
    vector_dir = tmp_path / "vectors"
    rows = _unit_rows(np.vstack([near, far]))
    write_chunks(open_store(vector_dir), rows, dimensions=dim)
    optimize_store(vector_dir)
    db = open_store(vector_dir)
    assert any(i.columns == ["vector"] for i in db.open_table("paper_chunks").list_indices())
    far_ids = [f"row-{i}" for i in range(1500, 1508)]
    where = "id IN (" + ", ".join(f"'{i}'" for i in far_ids) + ")"
    query = np.ones(dim, dtype="float32").tolist()

    hits = query_vector(db, query, top_k=5, where=where)
    all_hits = query_vector(db, query, top_k=50, where=where)

    assert len(hits) == min(5, len(far_ids))
    assert {hit["id"] for hit in all_hits} == set(far_ids)


def test_save_index_meta_leaves_no_partial_file_and_replaces_atomically(tmp_path: Path) -> None:
    save_index_meta(tmp_path, {"chunk_count": 1})
    save_index_meta(tmp_path, {"chunk_count": 2})

    assert json.loads((tmp_path / "index_meta.json").read_text()) == {"chunk_count": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["index_meta.json"]


def test_embed_write_lock_blocks_second_holder_until_release(tmp_path: Path) -> None:
    entered = threading.Event()

    def second_holder() -> None:
        with embed_write_lock(tmp_path):
            entered.set()

    thread = threading.Thread(target=second_holder)
    with embed_write_lock(tmp_path):
        thread.start()
        assert not entered.wait(0.5)
    assert entered.wait(10)
    thread.join(30)


def test_optimize_store_waits_for_embed_write_lock(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    vector_dir = tmp_path / "vectors"
    write_chunks(
        open_store(vector_dir), _unit_rows(rng.normal(size=(20, 8)).astype("float32")), dimensions=8
    )
    finished = threading.Event()

    def run() -> None:
        optimize_store(vector_dir)
        finished.set()

    thread = threading.Thread(target=run)
    with embed_write_lock(vector_dir):
        thread.start()
        assert not finished.wait(0.5)
    assert finished.wait(30)
    thread.join(30)
