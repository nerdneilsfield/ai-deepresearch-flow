"""Black-box tests for ``paper db api import-local`` and ``paper db snapshot recompute``."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.paper.snapshot.admin import apply_paper_batch
from deepresearch_flow.paper.snapshot.import_local import import_semantic
from deepresearch_flow.paper.snapshot.schema import recompute_facet_edges
from deepresearch_flow.paper.snapshot.transaction import open_snapshot_connection
from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    embed_write_lock,
    load_index_meta,
    open_store,
    read_group_fingerprints,
    save_index_meta,
    write_chunks,
)


def _paper(title: str, keywords: list[str], source_hash: str | None = None) -> dict:
    return {
        "paper_title": title,
        "doi": f"10.1/{title.lower()}",
        "paper_authors": ["Alice", "Bob"],
        "publication_date": "2020-01",
        "keywords": keywords,
        "ai_generated_tags": ["tag-x"],
        "source_hash": source_hash or title,
        "templates": {"simple": {"paper_title": title, "summary": f"{title} summary"}},
    }


def _source_hash_by_title(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return dict(conn.execute("SELECT title, source_hash FROM paper").fetchall())
    finally:
        conn.close()


def _facet_edges(db_path: Path) -> set[tuple[int, int, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return set(
            conn.execute("SELECT node_id_a, node_id_b, paper_count FROM facet_edge").fetchall()
        )
    finally:
        conn.close()


def _recomputed_facet_edges(db_path: Path) -> set[tuple[int, int, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        recompute_facet_edges(conn)
        conn.commit()
    finally:
        conn.close()
    return _facet_edges(db_path)


def _chunk(doc_id: str, index: int, dim: int) -> ChunkRow:
    return ChunkRow(
        id=f"{doc_id}__shared_content_{index}",
        doc_id=doc_id,
        source_path="",
        template_tag="",
        chunk_type="content",
        chunk_index=index,
        field_name="",
        lang="",
        text=f"text {index}",
        content_hash=f"{doc_id}-{index}",
        vector=[0.1 * (index + 1)] * dim,
        title="T",
        year=2020,
        authors="",
        venue="",
        tags="",
    )


def _meta(dim: int) -> dict:
    return {
        "model": "m",
        "dimensions": dim,
        "normalized": True,
        "provider": "p",
        "index_version": 1,
        "doc_count": 0,
        "template_count": 0,
        "chunk_count": 0,
        "last_updated": None,
    }


def _build_source(tmp_path: Path, dim: int = 4) -> tuple[Path, Path]:
    snapshot = tmp_path / "src.db"
    papers = [
        _paper("One", ["kw-a", "kw-b"]),
        _paper("Two", ["kw-a", "kw-c"]),
        _paper("Three", ["kw-b", "kw-c"]),
    ]
    assert apply_paper_batch(snapshot, papers, False).added == 3
    embed = tmp_path / "src_vec"
    save_index_meta(embed, _meta(dim))
    write_chunks(
        open_store(embed),
        [_chunk(doc_id, index, dim) for doc_id in ("doc-a", "doc-b") for index in range(3)],
        dimensions=dim,
    )
    return snapshot, embed


def _import_args(src: tuple[Path, Path], target: tuple[Path, Path], *extra: str) -> list[str]:
    return [
        "db",
        "api",
        "import-local",
        "--from-snapshot-db",
        str(src[0]),
        "--from-embed-db",
        str(src[1]),
        "--snapshot-db",
        str(target[0]),
        "--embed-db",
        str(target[1]),
        *extra,
    ]


def _new_target(tmp_path: Path) -> tuple[Path, Path]:
    """An existing, empty server snapshot DB plus a not-yet-created embed dir."""
    snapshot = tmp_path / "live.db"
    open_snapshot_connection(snapshot).close()
    return snapshot, tmp_path / "live_vec"


def _table_version(embed: Path) -> int:
    return open_store(embed).open_table("paper_chunks").version


def test_import_local_is_complete_and_reentrant(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    target = _new_target(tmp_path)
    runner = CliRunner()

    first = runner.invoke(paper, _import_args(src, target))

    assert first.exit_code == 0, first.output
    assert "added=3" in first.output
    assert "written=2" in first.output
    assert set(_source_hash_by_title(target[0])) == {"One", "Two", "Three"}
    assert read_group_fingerprints(open_store(target[1])) == read_group_fingerprints(
        open_store(src[1])
    )
    assert load_index_meta(target[1])["chunk_count"] == 6
    incremental_edges = _facet_edges(target[0])
    assert incremental_edges
    assert incremental_edges == _recomputed_facet_edges(target[0])

    version = _table_version(target[1])
    second = runner.invoke(paper, _import_args(src, target))

    assert second.exit_code == 0, second.output
    assert "added=0 updated=0" in second.output
    assert "written=0" in second.output
    assert "optimize" not in second.output
    assert _table_version(target[1]) == version


def test_import_local_overwrite_updates_papers_with_different_source_hash(
    tmp_path: Path,
) -> None:
    src = _build_source(tmp_path)
    target = _new_target(tmp_path)
    runner = CliRunner()
    assert runner.invoke(paper, _import_args(src, target)).exit_code == 0
    apply_paper_batch(src[0], [_paper("Two", ["kw-z"], source_hash="Two-v2")], True)

    without_overwrite = runner.invoke(paper, _import_args(src, target))
    assert "added=0 updated=0" in without_overwrite.output
    assert _source_hash_by_title(target[0])["Two"] == "Two"

    with_overwrite = runner.invoke(paper, _import_args(src, target, "--overwrite"))

    assert with_overwrite.exit_code == 0, with_overwrite.output
    assert "added=0 updated=1" in with_overwrite.output
    assert _source_hash_by_title(target[0]) == {
        "One": "One",
        "Two": "Two-v2",
        "Three": "Three",
    }
    assert _facet_edges(target[0]) == _recomputed_facet_edges(target[0])


def test_import_local_rejects_embed_dimension_mismatch(tmp_path: Path) -> None:
    src = _build_source(tmp_path, dim=4)
    target = _new_target(tmp_path)
    save_index_meta(target[1], _meta(8))

    result = CliRunner().invoke(paper, _import_args(src, target))

    assert result.exit_code != 0
    assert "dimensions mismatch" in result.output
    assert _source_hash_by_title(target[0]) == {}


def test_snapshot_recompute_repairs_facet_edge(tmp_path: Path) -> None:
    db_path = tmp_path / "snap.db"
    apply_paper_batch(
        db_path,
        [_paper("One", ["kw-a", "kw-b"]), _paper("Two", ["kw-a", "kw-c"])],
        False,
    )
    correct_edges = _facet_edges(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM facet_edge WHERE rowid % 2 = 0")
    conn.execute("UPDATE facet_edge SET paper_count = paper_count + 5")
    conn.commit()
    conn.close()
    assert _facet_edges(db_path) != correct_edges

    result = CliRunner().invoke(
        paper, ["db", "snapshot", "recompute", "--snapshot-db", str(db_path)]
    )

    assert result.exit_code == 0, result.output
    assert "papers: 2" in result.output
    assert f"facet_edge rows: {len(correct_edges)}" in result.output
    assert _facet_edges(db_path) == correct_edges


def _paper_summary_tags(db_path: Path) -> dict[str, set[str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        tags: dict[str, set[str]] = {}
        for title, tag in conn.execute(
            "SELECT p.title, s.template_tag FROM paper p "
            "JOIN paper_summary s ON s.paper_id = p.paper_id"
        ):
            tags.setdefault(title, set()).add(tag)
        return tags
    finally:
        conn.close()


def test_import_local_keeps_template_tags_of_source_papers(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    two_templates = _paper("Multi", ["kw-a"])
    two_templates["templates"] = {
        "simple": {"paper_title": "Multi", "summary": "alpha beta"},
        "deep": {"paper_title": "Multi", "summary": "gamma delta"},
    }
    apply_paper_batch(src[0], [two_templates], False)
    target = _new_target(tmp_path)
    runner = CliRunner()

    result = runner.invoke(paper, _import_args(src, target))

    assert result.exit_code == 0, result.output
    assert _paper_summary_tags(target[0]) == _paper_summary_tags(src[0])
    assert _paper_summary_tags(target[0])["Multi"] == {"simple", "deep"}
    conn = sqlite3.connect(str(target[0]))
    preview = conn.execute("SELECT summary_preview FROM paper WHERE title = 'Multi'").fetchone()[0]
    conn.close()
    assert "alpha beta" in preview or "gamma delta" in preview

    apply_paper_batch(src[0], [{**two_templates, "source_hash": "Multi-v2"}], True)
    assert runner.invoke(paper, _import_args(src, target, "--overwrite")).exit_code == 0
    assert _paper_summary_tags(target[0])["Multi"] == {"simple", "deep"}
    assert _source_hash_by_title(target[0])["Multi"] == "Multi-v2"


def _rewrite_group(embed: Path, doc_id: str, content_hash: str) -> None:
    table = open_store(embed).open_table("paper_chunks")
    table.update(where=f"doc_id = '{doc_id}'", values={"content_hash": content_hash})


def test_import_local_semantic_overwrite_only_with_flag(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    target = _new_target(tmp_path)
    runner = CliRunner()
    assert runner.invoke(paper, _import_args(src, target)).exit_code == 0
    before = read_group_fingerprints(open_store(target[1]))
    _rewrite_group(src[1], "doc-a", "changed")
    assert read_group_fingerprints(open_store(src[1])) != before

    without_overwrite = runner.invoke(paper, _import_args(src, target))

    assert without_overwrite.exit_code == 0, without_overwrite.output
    assert "written=0" in without_overwrite.output
    assert read_group_fingerprints(open_store(target[1])) == before

    with_overwrite = runner.invoke(paper, _import_args(src, target, "--overwrite"))

    assert with_overwrite.exit_code == 0, with_overwrite.output
    assert "written=1" in with_overwrite.output
    assert read_group_fingerprints(open_store(target[1])) == read_group_fingerprints(
        open_store(src[1])
    )
    assert load_index_meta(target[1])["chunk_count"] == 6


def test_import_local_requires_existing_target_snapshot_db(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    missing = tmp_path / "typo.db"
    runner = CliRunner()

    explicit = runner.invoke(paper, _import_args(src, (missing, tmp_path / "live_vec")))
    from_env = runner.invoke(
        paper,
        [
            "db", "api", "import-local",
            "--from-snapshot-db", str(src[0]),
        ],
        env={"PAPER_DB_SNAPSHOT_DB": str(missing)},
    )  # fmt: skip

    assert explicit.exit_code != 0
    assert from_env.exit_code != 0
    assert not missing.exists()
    assert not (tmp_path / "live_vec").exists()


def test_import_local_announces_new_embed_db(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    target = _new_target(tmp_path)

    result = CliRunner().invoke(paper, _import_args(src, target))

    assert result.exit_code == 0, result.output
    assert f"Creating new embed db at {target[1]}" in result.output
    existing = CliRunner().invoke(paper, _import_args(src, target))
    assert "Creating new embed db" not in existing.output


def test_import_local_rejects_source_index_meta_missing_dimensions(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    broken = _meta(4)
    del broken["dimensions"]
    save_index_meta(src[1], broken)
    target = _new_target(tmp_path)

    result = CliRunner().invoke(paper, _import_args(src, target))

    assert result.exit_code != 0
    assert "dimensions" in result.output
    assert "Traceback" not in result.output
    assert _source_hash_by_title(target[0]) == {}


def test_import_local_checks_dimensions_of_target_table_without_index_meta(
    tmp_path: Path,
) -> None:
    src = _build_source(tmp_path, dim=4)
    target = _new_target(tmp_path)
    write_chunks(open_store(target[1]), [_chunk("other", 0, 8)], dimensions=8)
    assert not (target[1] / "index_meta.json").exists()

    result = CliRunner().invoke(paper, _import_args(src, target))

    assert result.exit_code != 0
    assert "dimensions mismatch" in result.output
    assert _source_hash_by_title(target[0]) == {}


def test_embed_write_lock_serializes_import_and_server_semantic_writes(tmp_path: Path) -> None:
    src = _build_source(tmp_path)
    target = _new_target(tmp_path)
    meta = _meta(4)
    finished = threading.Event()

    def run_import() -> None:
        import_semantic(src[1], target[1], meta, overwrite=False)
        finished.set()

    with embed_write_lock(target[1]):
        thread = threading.Thread(target=run_import)
        thread.start()
        assert not finished.wait(0.5)
    assert finished.wait(30)
    thread.join(30)
    assert read_group_fingerprints(open_store(target[1])) == read_group_fingerprints(
        open_store(src[1])
    )


def test_concurrent_semantic_imports_keep_index_meta_consistent(tmp_path: Path) -> None:
    sources = []
    for name in ("a", "b"):
        embed = tmp_path / f"src_{name}"
        save_index_meta(embed, _meta(4))
        write_chunks(
            open_store(embed),
            [_chunk(f"doc-{name}{n}", i, 4) for n in range(4) for i in range(3)],
            dimensions=4,
        )
        sources.append(embed)
    target = tmp_path / "live_vec"
    errors: list[BaseException] = []

    def run_import(embed: Path) -> None:
        try:
            import_semantic(embed, target, _meta(4), overwrite=False)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run_import, args=(embed,)) for embed in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert not errors
    rows = open_store(target).open_table("paper_chunks").count_rows()
    assert rows == 24
    meta = load_index_meta(target)
    assert meta["chunk_count"] == rows
    assert meta["doc_count"] == 8
