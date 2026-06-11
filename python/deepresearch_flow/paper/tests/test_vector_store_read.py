from __future__ import annotations

from pathlib import Path

import lancedb
import pytest

from deepresearch_flow.paper.vector_store import (
    _reset_ensured_scalar_index_cache,
    AdminIngestState,
    ChunkRow,
    decode_vector_b64,
    encode_vector_b64,
    delete_groups,
    ensure_admin_scalar_indices,
    open_store,
    read_admin_ingest_state,
    read_all_chunks,
    read_chunks_for_group,
    write_chunks,
)


@pytest.fixture(autouse=True)
def _clear_scalar_index_cache() -> None:
    _reset_ensured_scalar_index_cache()
    yield
    _reset_ensured_scalar_index_cache()


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
    assert [chunk["doc_id"] for chunk in chunks] == ["docb"]


def test_read_chunks_for_group_returns_only_matching_doc_and_template(tmp_path: Path) -> None:
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
            id="doc1_deep_title_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="deep",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="B",
            content_hash="hb",
            vector=[0.2] * 4,
            title="B",
            year=2024,
            authors="A",
            venue="V",
            tags="t",
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
            text="C",
            content_hash="hc",
            vector=[0.3] * 4,
            title="C",
            year=2024,
            authors="B",
            venue="V",
            tags="t",
        ),
    ]
    write_chunks(db, rows, dimensions=4)

    chunks = read_chunks_for_group(db, "doc1", "simple")

    assert len(chunks) == 1
    assert chunks[0]["id"] == "doc1_simple_title_0"


def test_read_chunks_for_group_handles_shared_template(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1__shared_title_0",
            doc_id="doc1",
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
            id="doc1_simple_title_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="simple",
            chunk_type="title",
            chunk_index=0,
            field_name="title",
            lang="",
            text="B",
            content_hash="hb",
            vector=[0.2] * 4,
            title="B",
            year=2024,
            authors="A",
            venue="V",
            tags="t",
        ),
    ]
    write_chunks(db, rows, dimensions=4)

    chunks = read_chunks_for_group(db, "doc1", "")

    assert len(chunks) == 1
    assert chunks[0]["id"] == "doc1__shared_title_0"


def test_write_chunks_creates_admin_scalar_indices_on_first_table_create(tmp_path: Path) -> None:
    db = open_store(tmp_path)

    write_chunks(
        db,
        [
            ChunkRow(
                id="doc1_simple_title_0",
                doc_id="doc1",
                source_path="a.md",
                template_tag="simple",
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
        ],
        dimensions=4,
    )

    table = db.open_table("paper_chunks")
    indexed_columns = {
        column for index in table.list_indices() for column in getattr(index, "columns", [])
    }
    assert {"doc_id", "template_tag"} <= indexed_columns


def test_ensure_admin_scalar_indices_upgrades_existing_table(tmp_path: Path) -> None:
    db = lancedb.connect(str(tmp_path))
    db.create_table(
        "paper_chunks",
        data=[
            {
                "id": "1",
                "doc_id": "doc1",
                "template_tag": "simple",
                "content_hash": "h",
                "vector": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        mode="overwrite",
    )

    table = db.open_table("paper_chunks")
    assert list(table.list_indices()) == []

    result = ensure_admin_scalar_indices(db, vector_dir=tmp_path)

    assert set(result.created_names) == {"idx_chunks_doc_id", "idx_chunks_template_tag"}

    refreshed_table = db.open_table("paper_chunks")
    indexed_columns = {
        column
        for index in refreshed_table.list_indices()
        for column in getattr(index, "columns", [])
    }
    assert {"doc_id", "template_tag"} <= indexed_columns


def test_ensure_admin_scalar_indices_rebuilds_after_table_overwrite(tmp_path: Path) -> None:
    db = lancedb.connect(str(tmp_path))
    db.create_table(
        "paper_chunks",
        data=[
            {
                "id": "1",
                "doc_id": "doc1",
                "template_tag": "simple",
                "content_hash": "h",
                "vector": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        mode="overwrite",
    )

    first_result = ensure_admin_scalar_indices(db, vector_dir=tmp_path)
    assert set(first_result.created_names) == {"idx_chunks_doc_id", "idx_chunks_template_tag"}

    db.create_table(
        "paper_chunks",
        data=[
            {
                "id": "2",
                "doc_id": "doc2",
                "template_tag": "deep",
                "content_hash": "h2",
                "vector": [0.4, 0.3, 0.2, 0.1],
            }
        ],
        mode="overwrite",
    )
    assert list(db.open_table("paper_chunks").list_indices()) == []

    second_result = ensure_admin_scalar_indices(db, vector_dir=tmp_path)

    assert set(second_result.created_names) == {"idx_chunks_doc_id", "idx_chunks_template_tag"}


def test_read_admin_ingest_state_for_cold_doc_with_existing_template_elsewhere(
    tmp_path: Path,
) -> None:
    db = open_store(tmp_path)
    write_chunks(
        db,
        [
            ChunkRow(
                id="doc2_simple_title_0",
                doc_id="doc2",
                source_path="a.md",
                template_tag="simple",
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
        ],
        dimensions=4,
    )

    state = read_admin_ingest_state(db, "doc1", "simple")

    assert state == AdminIngestState(
        existing_by_id={},
        previous_template_keys=set(),
        had_template_elsewhere=True,
        doc_had_any_rows=False,
    )


def test_read_admin_ingest_state_for_empty_target_group_with_other_doc_templates(
    tmp_path: Path,
) -> None:
    db = open_store(tmp_path)
    write_chunks(
        db,
        [
            ChunkRow(
                id="doc1_deep_title_0",
                doc_id="doc1",
                source_path="a.md",
                template_tag="deep",
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
                id="doc2_simple_title_0",
                doc_id="doc2",
                source_path="b.md",
                template_tag="simple",
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
        ],
        dimensions=4,
    )

    state = read_admin_ingest_state(db, "doc1", "simple")

    assert state == AdminIngestState(
        existing_by_id={},
        previous_template_keys={"deep"},
        had_template_elsewhere=True,
        doc_had_any_rows=True,
    )


def test_read_admin_ingest_state_with_many_other_template_docs(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    rows = [
        ChunkRow(
            id="doc1_deep_title_0",
            doc_id="doc1",
            source_path="a.md",
            template_tag="deep",
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
    ]
    for index in range(250):
        rows.append(
            ChunkRow(
                id=f"doc{index + 2}_simple_title_0",
                doc_id=f"doc{index + 2}",
                source_path=f"{index}.md",
                template_tag="simple",
                chunk_type="title",
                chunk_index=0,
                field_name="title",
                lang="",
                text=f"Doc {index}",
                content_hash=f"h{index}",
                vector=[0.2] * 4,
                title=f"Doc {index}",
                year=2024,
                authors="B",
                venue="V",
                tags="t",
            )
        )
    write_chunks(db, rows, dimensions=4)

    state = read_admin_ingest_state(db, "doc1", "simple")

    assert state == AdminIngestState(
        existing_by_id={},
        previous_template_keys={"deep"},
        had_template_elsewhere=True,
        doc_had_any_rows=True,
    )


def test_read_admin_ingest_state_for_existing_target_group(tmp_path: Path) -> None:
    db = open_store(tmp_path)
    write_chunks(
        db,
        [
            ChunkRow(
                id="doc1_simple_title_0",
                doc_id="doc1",
                source_path="a.md",
                template_tag="simple",
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
                id="doc1_simple_content_1",
                doc_id="doc1",
                source_path="a.md",
                template_tag="simple",
                chunk_type="content",
                chunk_index=1,
                field_name="summary",
                lang="",
                text="B",
                content_hash="hb",
                vector=[0.2] * 4,
                title="A",
                year=2024,
                authors="A",
                venue="V",
                tags="t",
            ),
            ChunkRow(
                id="doc1_deep_title_0",
                doc_id="doc1",
                source_path="a.md",
                template_tag="deep",
                chunk_type="title",
                chunk_index=0,
                field_name="title",
                lang="",
                text="C",
                content_hash="hc",
                vector=[0.3] * 4,
                title="A",
                year=2024,
                authors="A",
                venue="V",
                tags="t",
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
                text="D",
                content_hash="hd",
                vector=[0.4] * 4,
                title="B",
                year=2024,
                authors="B",
                venue="V",
                tags="t",
            ),
        ],
        dimensions=4,
    )

    state = read_admin_ingest_state(db, "doc1", "simple")

    assert state == AdminIngestState(
        existing_by_id={
            "doc1_simple_title_0": "ha",
            "doc1_simple_content_1": "hb",
        },
        previous_template_keys={"simple", "deep"},
        had_template_elsewhere=True,
        doc_had_any_rows=True,
    )
