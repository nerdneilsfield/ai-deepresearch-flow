import json
from pathlib import Path

import pytest

from deepresearch_flow.paper.snapshot.builder import SnapshotBuildOptions, build_snapshot


def _opts(input_path: Path, output_db: Path, static_dir: Path) -> SnapshotBuildOptions:
    return SnapshotBuildOptions(
        input_paths=[input_path],
        bibtex_path=None,
        md_roots=[],
        md_translated_roots=[],
        pdf_roots=[],
        output_db=output_db,
        static_export_dir=static_dir,
        previous_snapshot_db=None,
    )


def test_failed_build_keeps_existing_db(tmp_path: Path) -> None:
    output_db = tmp_path / "out.db"
    output_db.write_bytes(b"previous database")
    bad_input = tmp_path / "bad.json"
    bad_input.write_text("not json", encoding="utf-8")

    with pytest.raises(Exception):
        build_snapshot(_opts(bad_input, output_db, tmp_path / "static"))

    assert output_db.read_bytes() == b"previous database"


def test_successful_build_replaces_db_and_leaves_no_temp_file(tmp_path: Path) -> None:
    output_db = tmp_path / "out.db"
    output_db.write_bytes(b"previous database")
    good_input = tmp_path / "good.json"
    good_input.write_text(
        json.dumps(
            {
                "template_tag": "simple",
                "papers": [
                    {
                        "paper_title": "A Paper",
                        "paper_authors": ["Alice Example"],
                        "publication_date": "2024",
                        "publication_venue": "ACL",
                        "summary": "body",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_snapshot(_opts(good_input, output_db, tmp_path / "static"))

    assert output_db.read_bytes()[:15] == b"SQLite format 3"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
