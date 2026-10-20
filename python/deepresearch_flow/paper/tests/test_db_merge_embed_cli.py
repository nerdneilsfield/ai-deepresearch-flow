"""Black-box tests for ``paper db api merge-embed``."""

from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    load_index_meta,
    open_store,
    save_index_meta,
    write_chunks,
)


def _chunk(doc_id: str, text: str, dim: int) -> ChunkRow:
    return ChunkRow(
        id=f"{doc_id}__shared_content_0",
        doc_id=doc_id,
        source_path="",
        template_tag="",
        chunk_type="content",
        chunk_index=0,
        field_name="",
        lang="",
        text=text,
        content_hash=f"{doc_id}-{text}",
        vector=[0.1] * dim,
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


def _write_embed(path: Path, rows: list[tuple[str, str]], dim: int = 4) -> Path:
    save_index_meta(path, _meta(dim))
    write_chunks(
        open_store(path),
        [_chunk(doc_id, text, dim) for doc_id, text in rows],
        dimensions=dim,
    )
    return path


def _rows(embed: Path) -> set[tuple[str, str]]:
    table = open_store(embed).open_table("paper_chunks")
    return {(str(row["doc_id"]), str(row["text"])) for row in table.to_arrow().to_pylist()}


def _merge(target: Path, *sources: Path, overwrite: bool = False) -> list[str]:
    args = ["db", "api", "merge-embed", "--embed-db", str(target)]
    for source in sources:
        args.extend(["--from-embed-db", str(source)])
    if overwrite:
        args.append("--overwrite")
    return args


def test_merge_embed_combines_several_sources_and_skips_same_groups(tmp_path: Path) -> None:
    left = _write_embed(tmp_path / "left", [("doc-a", "alpha"), ("doc-shared", "shared")])
    right = _write_embed(tmp_path / "right", [("doc-b", "beta"), ("doc-shared", "shared")])
    target = tmp_path / "merged"
    runner = CliRunner()

    first = runner.invoke(paper, _merge(target, left, right))

    assert first.exit_code == 0, first.output
    assert f"Creating new embed db at {target}" in first.output
    assert "written=2" in first.output
    assert "written=1" in first.output
    assert "optimize done" in first.output
    assert _rows(target) == {("doc-a", "alpha"), ("doc-b", "beta"), ("doc-shared", "shared")}
    assert load_index_meta(target)["chunk_count"] == 3

    second = runner.invoke(paper, _merge(target, left, right))

    assert second.exit_code == 0, second.output
    assert second.output.count("written=0") == 2
    assert "optimize" not in second.output
    assert _rows(target) == {("doc-a", "alpha"), ("doc-b", "beta"), ("doc-shared", "shared")}


def test_merge_embed_overwrite_replaces_changed_group(tmp_path: Path) -> None:
    target = _write_embed(tmp_path / "target", [("doc-a", "old text")])
    source = _write_embed(tmp_path / "source", [("doc-a", "old text")])
    runner = CliRunner()

    kept = runner.invoke(paper, _merge(target, source))

    assert kept.exit_code == 0, kept.output
    assert "written=0" in kept.output
    assert _rows(target) == {("doc-a", "old text")}

    shutil.rmtree(source)
    _write_embed(source, [("doc-a", "new text")])
    replaced = runner.invoke(paper, _merge(target, source, overwrite=True))

    assert replaced.exit_code == 0, replaced.output
    assert "written=1" in replaced.output
    assert _rows(target) == {("doc-a", "new text")}


def test_merge_embed_rejects_incompatible_dimensions_and_self(tmp_path: Path) -> None:
    source = _write_embed(tmp_path / "source", [("doc-a", "alpha")], dim=4)
    other = _write_embed(tmp_path / "other", [("doc-b", "beta")], dim=8)
    target = tmp_path / "merged"
    runner = CliRunner()

    mismatch = runner.invoke(paper, _merge(target, source, other))
    same = runner.invoke(paper, _merge(source, source))

    assert mismatch.exit_code != 0
    assert "compatible" in mismatch.output
    assert _rows(target) == {("doc-a", "alpha")}
    assert same.exit_code != 0
    assert "is the target" in same.output
