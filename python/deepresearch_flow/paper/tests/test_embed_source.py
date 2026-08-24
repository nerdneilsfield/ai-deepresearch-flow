from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deepresearch_flow.paper.embed_source import (
    DocumentMetadata,
    EmbedDocument,
    load_from_json,
    load_from_snapshot,
    resolve_template_tag,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


def test_resolve_template_tag_priority_and_error() -> None:
    record = {"template_tag": "record-template", "prompt_template": "prompt-template"}

    assert resolve_template_tag(record, "cli-template") == "cli-template"
    assert resolve_template_tag(record, None) == "record-template"
    assert resolve_template_tag({"prompt_template": "prompt-template"}, None) == "prompt-template"

    with pytest.raises(ValueError, match="template"):
        resolve_template_tag({}, None)


def test_load_from_json_merges_records_and_prefers_paper_title(tmp_path: Path) -> None:
    json_a = tmp_path / "a.json"
    json_b = tmp_path / "b.json"
    md_root = tmp_path / "md"
    translated_root = tmp_path / "translated"

    md_file = md_root / "nested" / "paper.md"
    md_file.parent.mkdir(parents=True)
    md_file.write_text("source markdown", encoding="utf-8")

    (translated_root / "en").mkdir(parents=True)
    (translated_root / "en" / "source-hash.md").write_text("english translation", encoding="utf-8")

    paper = {
        "doi": "10.1000/example",
        "paper_title": "Preferred Title",
        "title": "Fallback Title",
        "paper_authors": ["Alice Example"],
        "publication_venue": "ACL",
        "source_path": "nested/paper.md",
        "source_hash": "source-hash",
    }
    json_a.write_text(
        json.dumps([{**paper, "prompt_template": "simple"}], ensure_ascii=False), encoding="utf-8"
    )
    json_b.write_text(
        json.dumps([{**paper, "prompt_template": "deep_read"}], ensure_ascii=False),
        encoding="utf-8",
    )

    docs = load_from_json(
        [json_a, json_b],
        md_roots=[md_root],
        md_translated_roots=[translated_root],
    )

    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, EmbedDocument)
    assert isinstance(doc.metadata, DocumentMetadata)
    assert doc.metadata.title == "Preferred Title"
    assert doc.template_records["simple"][0]["prompt_template"] == "simple"
    assert doc.template_records["deep_read"][0]["prompt_template"] == "deep_read"
    assert doc.source_md == "source markdown"
    assert doc.translations == {"en": "english translation"}
    assert doc.doc_id


def test_load_from_json_supports_dict_list_and_nested_papers(tmp_path: Path) -> None:
    list_path = tmp_path / "list.json"
    dict_path = tmp_path / "dict.json"
    nested_path = tmp_path / "nested.json"
    bad_path = tmp_path / "bad.json"

    paper = {
        "paper_title": "Preferred Title",
        "title": "Fallback Title",
        "paper_authors": ["Alice Example"],
        "publication_venue": "ACL",
        "source_path": "nested/paper.md",
        "source_hash": "source-hash",
        "prompt_template": "simple",
    }
    list_path.write_text(json.dumps([paper, "x"], ensure_ascii=False), encoding="utf-8")
    dict_path.write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")
    nested_path.write_text(
        json.dumps({"papers": [paper, "x"]}, ensure_ascii=False), encoding="utf-8"
    )
    bad_path.write_text(json.dumps("oops"), encoding="utf-8")

    assert len(load_from_json([list_path])) == 1
    assert len(load_from_json([dict_path])) == 1
    assert len(load_from_json([nested_path])) == 1
    with pytest.raises(ValueError, match="Unsupported JSON payload"):
        load_from_json([bad_path])


def test_load_from_json_applies_template_override_and_finds_later_roots(tmp_path: Path) -> None:
    json_a = tmp_path / "a.json"
    json_b = tmp_path / "b.json"
    md_root_1 = tmp_path / "md-1"
    md_root_2 = tmp_path / "md-2"
    translated_root_1 = tmp_path / "translated-1"
    translated_root_2 = tmp_path / "translated-2"

    md_file = md_root_2 / "nested" / "paper.md"
    md_file.parent.mkdir(parents=True)
    md_file.write_text("source markdown from later root", encoding="utf-8")

    translated_file = translated_root_2 / "ja" / "source-hash.md"
    translated_file.parent.mkdir(parents=True)
    translated_file.write_text("translation from later root", encoding="utf-8")

    record = {
        "doi": "10.1000/example",
        "title": "Fallback Title",
        "paper_authors": ["Alice Example"],
        "publication_venue": "ACL",
        "source_path": "nested/paper.md",
        "source_hash": "source-hash",
        "prompt_template": "ignored",
    }
    json_a.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    json_b.write_text(json.dumps({"papers": [record]}, ensure_ascii=False), encoding="utf-8")

    docs = load_from_json(
        [json_a, json_b],
        template_tag_override="override",
        md_roots=[md_root_1, md_root_2],
        md_translated_roots=[translated_root_1, translated_root_2],
    )

    assert len(docs) == 1
    doc = docs[0]
    assert doc.metadata.title == "Fallback Title"
    assert doc.metadata.source_path == "nested/paper.md"
    assert len(doc.template_records["override"]) == 2
    assert doc.template_records["override"][0]["prompt_template"] == "ignored"
    assert doc.source_md == "source markdown from later root"
    assert doc.translations == {"ja": "translation from later root"}


def test_load_from_json_shows_tqdm_progress(tmp_path: Path, monkeypatch) -> None:
    json_path = tmp_path / "papers.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "paper_title": "Paper A",
                    "paper_authors": ["Alice"],
                    "publication_venue": "ACL",
                    "prompt_template": "simple",
                },
                {
                    "paper_title": "Paper B",
                    "paper_authors": ["Bob"],
                    "publication_venue": "EMNLP",
                    "prompt_template": "simple",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tqdm_calls: list[dict[str, object]] = []
    progress_updates: list[int] = []
    exits: list[bool] = []

    class FakeProgress:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            exits.append(True)
            return False

        def update(self, value: int) -> None:
            progress_updates.append(value)

    def fake_tqdm(*args, **kwargs):  # noqa: ANN002, ANN003
        tqdm_calls.append(kwargs)
        return FakeProgress()

    monkeypatch.setattr("deepresearch_flow.paper.embed_source.tqdm", fake_tqdm)

    docs = load_from_json([json_path])

    assert len(docs) == 2
    assert any(
        call.get("desc") == "load json" and int(call.get("total", 0)) == 2 for call in tqdm_calls
    )
    assert progress_updates == [1, 1]
    assert exits == [True]


def test_load_from_snapshot_reads_source_and_translation(tmp_path: Path) -> None:
    snapshot_db = tmp_path / "snapshot.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()

    conn = sqlite3.connect(snapshot_db)
    conn.row_factory = sqlite3.Row
    try:
        init_snapshot_db(conn)
        conn.execute(
            """
            INSERT INTO paper (
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-1",
                "doi:10.1000/example",
                "doi",
                None,
                "Snapshot Title",
                "2024",
                "04",
                "2024-04-01",
                "ACL",
                "simple",
                "summary preview",
                0,
                "source-hash",
                "en",
                "provider",
                "model",
                "simple",
                "2024-04-01T00:00:00Z",
                None,
                "source-hash",
            ),
        )
        conn.execute(
            "INSERT INTO author(author_id, value, paper_count) VALUES (?, ?, ?)",
            (1, "Alice Example", 1),
        )
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES (?, ?)", ("paper-1", 1))
        conn.execute(
            "INSERT INTO tag(tag_id, value, paper_count) VALUES (?, ?, ?)", (1, "tag-a", 1)
        )
        conn.execute("INSERT INTO paper_tag(paper_id, tag_id) VALUES (?, ?)", ("paper-1", 1))
        conn.execute(
            "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
            ("paper-1", "simple"),
        )
        conn.execute(
            "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
            ("paper-1", "ja", "translation-hash"),
        )
        conn.commit()
    finally:
        conn.close()

    summary_dir = static_dir / "summary" / "paper-1"
    summary_dir.mkdir(parents=True)
    (summary_dir / "simple.json").write_text(
        json.dumps({"summary": "snapshot summary"}, ensure_ascii=False),
        encoding="utf-8",
    )
    md_dir = static_dir / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "source-hash.md").write_text("snapshot source markdown", encoding="utf-8")
    tr_dir = static_dir / "md_translate" / "ja"
    tr_dir.mkdir(parents=True)
    (tr_dir / "translation-hash.md").write_text("snapshot translation", encoding="utf-8")

    docs = load_from_snapshot(snapshot_db, static_dir)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.doc_id == "paper-1"
    assert doc.metadata.title == "Snapshot Title"
    assert doc.metadata.authors == "Alice Example"
    assert doc.metadata.tags == "tag-a"
    assert doc.template_records["simple"][0]["title"] == "Snapshot Title"
    assert doc.template_records["simple"][0]["summary"] == "snapshot summary"
    assert doc.source_md == "snapshot source markdown"
    assert doc.translations == {"ja": "snapshot translation"}


def test_load_from_snapshot_selects_only_requested_paper_ids(tmp_path: Path) -> None:
    snapshot_db = tmp_path / "snapshot.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    conn = sqlite3.connect(snapshot_db)
    try:
        init_snapshot_db(conn)
        for index, paper_id in enumerate(("paper-1", "paper-2")):
            conn.execute(
                """
                INSERT INTO paper (
                  paper_id, paper_key, paper_key_type, title, year, month,
                  publication_date, venue, preferred_summary_template,
                  summary_preview, paper_index
                ) VALUES (?, ?, 'paper_id', ?, '2024', '', '2024', '', 'simple', '', ?)
                """,
                (paper_id, paper_id, paper_id, index),
            )
        conn.commit()
    finally:
        conn.close()

    selected = load_from_snapshot(snapshot_db, static_dir, paper_ids=("paper-2",))
    empty = load_from_snapshot(snapshot_db, static_dir, paper_ids=())

    assert [document.doc_id for document in selected] == ["paper-2"]
    assert empty == []


def test_load_from_snapshot_ignores_traversal_in_legacy_summary_keys(
    tmp_path: Path,
) -> None:
    snapshot_db = tmp_path / "snapshot.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "summary").mkdir()
    (static_dir / "outside").mkdir()
    outside = tmp_path / "escape.json"
    outside.write_text(json.dumps({"summary": "must not load"}), encoding="utf-8")

    conn = sqlite3.connect(snapshot_db)
    try:
        init_snapshot_db(conn)
        conn.execute(
            """
            INSERT INTO paper (
              paper_id, paper_key, paper_key_type, doi, title, year, month,
              publication_date, venue, preferred_summary_template, summary_preview,
              paper_index, source_hash, output_language, provider, model,
              prompt_template, extracted_at, pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "../outside",
                "legacy-key",
                "title",
                None,
                "Unsafe paper",
                "2024",
                "",
                "2024",
                "ACL",
                "simple",
                "",
                0,
                None,
                "en",
                "provider",
                "model",
                "simple",
                "2024-01-01T00:00:00Z",
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
            ("../outside", "../../escape"),
        )
        conn.commit()
    finally:
        conn.close()

    docs = load_from_snapshot(snapshot_db, static_dir)

    assert len(docs) == 1
    assert docs[0].template_records == {}


def test_load_from_snapshot_keeps_documents_when_artifacts_are_missing(tmp_path: Path) -> None:
    snapshot_db = tmp_path / "snapshot.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()

    conn = sqlite3.connect(snapshot_db)
    conn.row_factory = sqlite3.Row
    try:
        init_snapshot_db(conn)
        conn.execute(
            """
            INSERT INTO paper (
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-with-files",
                "doi:10.1000/with-files",
                "doi",
                None,
                "With Files",
                "2024",
                "04",
                "2024-04-01",
                "ACL",
                "simple",
                "summary preview",
                0,
                "source-hash",
                "en",
                "provider",
                "model",
                "simple",
                "2024-04-01T00:00:00Z",
                None,
                "source-hash",
            ),
        )
        conn.execute(
            """
            INSERT INTO paper (
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-missing-files",
                "doi:10.1000/missing-files",
                "doi",
                None,
                "Missing Files",
                "2024",
                "04",
                "2024-04-01",
                "ACL",
                "simple",
                "",
                1,
                None,
                "en",
                "provider",
                "model",
                None,
                "2024-04-01T00:00:00Z",
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO author(author_id, value, paper_count) VALUES (?, ?, ?)",
            (1, "Alice Example", 1),
        )
        conn.execute(
            "INSERT INTO paper_author(paper_id, author_id) VALUES (?, ?)", ("paper-with-files", 1)
        )
        conn.execute(
            "INSERT INTO tag(tag_id, value, paper_count) VALUES (?, ?, ?)", (1, "tag-a", 1)
        )
        conn.execute(
            "INSERT INTO paper_tag(paper_id, tag_id) VALUES (?, ?)", ("paper-with-files", 1)
        )
        conn.execute(
            "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
            ("paper-with-files", "simple"),
        )
        conn.execute(
            "INSERT INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
            ("paper-with-files", "ja", "translation-hash"),
        )
        conn.commit()
    finally:
        conn.close()

    summary_dir = static_dir / "summary" / "paper-with-files"
    summary_dir.mkdir(parents=True)
    (summary_dir / "simple.json").write_text(
        json.dumps({"summary": "snapshot summary"}, ensure_ascii=False),
        encoding="utf-8",
    )
    md_dir = static_dir / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "source-hash.md").write_text("snapshot source markdown", encoding="utf-8")
    tr_dir = static_dir / "md_translate" / "ja"
    tr_dir.mkdir(parents=True)
    (tr_dir / "translation-hash.md").write_text("snapshot translation", encoding="utf-8")

    docs = load_from_snapshot(snapshot_db, static_dir)
    docs_by_id = {doc.doc_id: doc for doc in docs}

    with_files = docs_by_id["paper-with-files"]
    missing_files = docs_by_id["paper-missing-files"]

    assert with_files.metadata.title == "With Files"
    assert with_files.metadata.authors == "Alice Example"
    assert with_files.metadata.tags == "tag-a"
    assert with_files.template_records["simple"][0]["summary"] == "snapshot summary"
    assert with_files.source_md == "snapshot source markdown"
    assert with_files.translations == {"ja": "snapshot translation"}

    assert missing_files.metadata.title == "Missing Files"
    assert missing_files.template_records == {}
    assert missing_files.source_md is None
    assert missing_files.translations == {}


def test_load_from_snapshot_tolerates_unknown_year(tmp_path: Path) -> None:
    snapshot_db = tmp_path / "snapshot.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()

    conn = sqlite3.connect(snapshot_db)
    conn.row_factory = sqlite3.Row
    try:
        init_snapshot_db(conn)
        conn.execute(
            """
            INSERT INTO paper (
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-unknown-year",
                "doi:10.1000/unknown-year",
                "doi",
                None,
                "Unknown Year",
                "unknown",
                "",
                "",
                "ACL",
                "simple",
                "",
                0,
                None,
                "en",
                "provider",
                "model",
                None,
                "2024-04-01T00:00:00Z",
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    docs = load_from_snapshot(snapshot_db, static_dir)

    assert len(docs) == 1
    assert docs[0].metadata.title == "Unknown Year"
    assert docs[0].metadata.year == 0
