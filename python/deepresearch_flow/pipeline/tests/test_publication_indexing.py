from __future__ import annotations

from pathlib import Path

from deepresearch_flow.pipeline.publication import build_publication_bundle
from deepresearch_flow.pipeline.publication_indexing import LanceDBIndexer


def test_lancedb_indexer_stages_one_bundle_and_removes_private_temp_files(
    tmp_path: Path,
) -> None:
    bundle = build_publication_bundle(
        "job-1",
        {
            "paper_title": "Indexed paper",
            "paper_authors": ["Ada Lovelace"],
            "templates": {"simple": {"summary": "A summary."}},
        },
        resources={
            "pdf": b"%PDF-1.7 indexed",
            "source_markdown": b"# Indexed paper\n",
            "summary_json": b'{"summary":"A summary."}\n',
            "translated_markdown": b"# Indexed paper\n",
        },
        work_dir=tmp_path / "private-work",
    )
    seen: dict[str, object] = {}

    def fake_embed_runner(**kwargs: object) -> None:
        root = Path(str(kwargs["static_export_dir"]))
        seen["root"] = root
        seen["paper_ids"] = kwargs["snapshot_paper_ids"]
        for resource in bundle.resources:
            assert (root / resource.relative_path).read_bytes() == resource.content

    indexer = LanceDBIndexer(
        config=object(),
        snapshot_db=tmp_path / "snapshot.sqlite3",
        static_root=tmp_path / "static",
        vector_dir=tmp_path / "vectors",
        embed_runner=fake_embed_runner,
    )

    indexer(bundle)

    assert seen["paper_ids"] == (bundle.paper_id,)
    assert not Path(str(seen["root"])).exists()
