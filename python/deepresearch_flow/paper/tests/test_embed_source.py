from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deepresearch_flow.paper.embed_source import (
    DocumentMetadata,
    EmbedDocument,
    _match_source_md,
    _match_translations,
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


def test_match_helpers_use_source_path_and_hash(tmp_path: Path) -> None:
    md_root = tmp_path / "md"
    translated_root = tmp_path / "translated"

    source_file = md_root / "nested" / "paper.md"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("source markdown", encoding="utf-8")

    en_file = translated_root / "en" / "source-hash.md"
    fr_file = translated_root / "fr" / "source-hash.md"
    en_file.parent.mkdir(parents=True)
    fr_file.parent.mkdir(parents=True)
    en_file.write_text("english translation", encoding="utf-8")
    fr_file.write_text("french translation", encoding="utf-8")

    record = {
        "source_path": "nested/paper.md",
        "source_hash": "source-hash",
    }

    assert _match_source_md(record, [md_root]) == "source markdown"
    assert _match_translations(record, [translated_root]) == {
        "en": "english translation",
        "fr": "french translation",
    }


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
    json_a.write_text(json.dumps([{**paper, "prompt_template": "simple"}], ensure_ascii=False), encoding="utf-8")
    json_b.write_text(json.dumps([{**paper, "prompt_template": "deep_read"}], ensure_ascii=False), encoding="utf-8")

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
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (?, ?, ?)", (1, "Alice Example", 1))
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES (?, ?)", ("paper-1", 1))
        conn.execute("INSERT INTO tag(tag_id, value, paper_count) VALUES (?, ?, ?)", (1, "tag-a", 1))
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
