"""Unified data source loading for paper embedding."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from deepresearch_flow.paper.snapshot.identity import (
    build_paper_key_candidates,
    choose_preferred_key,
    paper_id_for_key,
)


@dataclass(frozen=True)
class DocumentMetadata:
    title: str
    year: int
    authors: str
    venue: str
    tags: str
    source_path: str


@dataclass
class EmbedDocument:
    doc_id: str
    metadata: DocumentMetadata
    template_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    source_md: str | None = None
    translations: dict[str, str] = field(default_factory=dict)


def resolve_template_tag(record: dict[str, Any], cli_override: str | None) -> str:
    override = str(cli_override).strip() if cli_override is not None else ""
    if override:
        return override
    for key in ("template_tag", "prompt_template"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(
        "Cannot determine template tag for record. "
        "Provide --template-tag or include 'template_tag'/'prompt_template' in JSON."
    )


def _resolve_doc_id(record: dict[str, Any]) -> str:
    candidates = build_paper_key_candidates(record)
    preferred = choose_preferred_key(candidates)
    return paper_id_for_key(preferred.paper_key)


def _extract_title(record: dict[str, Any]) -> tuple[str, int]:
    paper_title = record.get("paper_title")
    if isinstance(paper_title, str) and paper_title.strip():
        return paper_title.strip(), 2
    title = record.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip(), 1
    if title is not None:
        text = str(title).strip()
        if text:
            return text, 1
    return "", 0


def _as_joined_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items)
    text = str(value).strip()
    return text


def _extract_year(record: dict[str, Any]) -> int:
    for key in ("year", "publication_date"):
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        try:
            return int(text[:4])
        except ValueError:
            continue
    return 0


def _extract_metadata(record: dict[str, Any]) -> tuple[DocumentMetadata, int]:
    title, title_rank = _extract_title(record)
    metadata = DocumentMetadata(
        title=title,
        year=_extract_year(record),
        authors=_as_joined_text(record.get("paper_authors") or record.get("_authors")),
        venue=_as_joined_text(record.get("publication_venue") or record.get("_venue")),
        tags=_as_joined_text(record.get("ai_generated_tags") or record.get("_tags")),
        source_path=_as_joined_text(record.get("source_path")),
    )
    return metadata, title_rank


def _read_json_payload(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        papers = raw.get("papers")
        if isinstance(papers, list):
            return [item for item in papers if isinstance(item, dict)]
        return [raw]
    raise ValueError(f"Unsupported JSON payload in {path}")


def _candidate_tokens(record: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        token = value.strip()
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for key in ("source_md_content_hash", "source_hash", "source_path"):
        raw = record.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        add(text)
        path = Path(text)
        add(path.name)
        add(path.stem)
    return tokens


def _file_matches(path: Path, tokens: Iterable[str]) -> bool:
    name = path.name
    stem = path.stem
    rel = str(path.as_posix())
    for token in tokens:
        if token == name or token == stem or token == rel or token in name or token in stem or token in rel:
            return True
    return False


def _resolve_markdown_file(root: Path, record: dict[str, Any]) -> Path | None:
    tokens = _candidate_tokens(record)
    if not tokens:
        return None
    if not root.exists():
        return None

    if root.is_file():
        return root if root.suffix.lower() == ".md" and _file_matches(root, tokens) else None

    source_path = str(record.get("source_path") or "").strip()
    if source_path:
        direct = root / Path(source_path)
        if direct.exists() and direct.is_file():
            return direct
        direct_name = root / Path(source_path).name
        if direct_name.exists() and direct_name.is_file():
            return direct_name
        direct_stem = root / f"{Path(source_path).stem}.md"
        if direct_stem.exists() and direct_stem.is_file():
            return direct_stem

    for token in tokens:
        direct = root / token
        if direct.exists() and direct.is_file():
            return direct
        direct_md = root / f"{token}.md"
        if direct_md.exists() and direct_md.is_file():
            return direct_md

    for md_file in sorted(root.rglob("*.md")):
        if _file_matches(md_file, tokens):
            return md_file
    return None


def _match_source_md(record: dict[str, Any], md_roots: list[Path]) -> str | None:
    for root in md_roots:
        md_file = _resolve_markdown_file(root, record)
        if md_file is not None:
            return md_file.read_text(encoding="utf-8")
    return None


def _match_translations(record: dict[str, Any], md_translated_roots: list[Path]) -> dict[str, str]:
    translations: dict[str, str] = {}
    for root in md_translated_roots:
        if not root.exists() or not root.is_dir():
            continue
        for lang_dir in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name):
            md_file = _resolve_markdown_file(lang_dir, record)
            if md_file is None:
                continue
            translations.setdefault(lang_dir.name, md_file.read_text(encoding="utf-8"))
    return translations


def _update_metadata_if_preferred(
    doc: EmbedDocument,
    metadata: DocumentMetadata,
    *,
    title_rank: int,
    title_ranks: dict[str, int],
) -> None:
    current_rank = title_ranks.get(doc.doc_id, 0)
    if title_rank > current_rank or not doc.metadata.title:
        doc.metadata = metadata
        title_ranks[doc.doc_id] = title_rank


def load_from_json(
    paths: list[Path],
    *,
    template_tag_override: str | None = None,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
) -> list[EmbedDocument]:
    docs_by_id: dict[str, EmbedDocument] = {}
    title_ranks: dict[str, int] = {}
    source_roots = md_roots or []
    translated_roots = md_translated_roots or []

    for path in paths:
        for record in _read_json_payload(path):
            tag = resolve_template_tag(record, template_tag_override)
            doc_id = _resolve_doc_id(record)
            metadata, title_rank = _extract_metadata(record)
            doc = docs_by_id.get(doc_id)
            if doc is None:
                doc = EmbedDocument(doc_id=doc_id, metadata=metadata)
                docs_by_id[doc_id] = doc
                title_ranks[doc_id] = title_rank
            else:
                _update_metadata_if_preferred(doc, metadata, title_rank=title_rank, title_ranks=title_ranks)
            if source_roots and doc.source_md is None:
                doc.source_md = _match_source_md(record, source_roots)
            if translated_roots:
                for lang, text in _match_translations(record, translated_roots).items():
                    doc.translations.setdefault(lang, text)
            doc.template_records.setdefault(tag, []).append(record)

    return list(docs_by_id.values())


def _row_text(row: sqlite3.Row, *keys: str) -> str:
    for key in keys:
        if key in row.keys():
            value = row[key]
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return ""


def load_from_snapshot(
    snapshot_db: Path,
    static_export_dir: Path,
) -> list[EmbedDocument]:
    conn = sqlite3.connect(str(snapshot_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM paper
            ORDER BY paper_index
            """
        ).fetchall()
        docs: list[EmbedDocument] = []
        for row in rows:
            paper_id = str(row["paper_id"])
            title = _row_text(row, "paper_title", "title")
            metadata = DocumentMetadata(
                title=title,
                year=int(str(row["year"])[:4]) if _row_text(row, "year") else 0,
                authors=", ".join(
                    str(author_row["value"]).strip()
                    for author_row in conn.execute(
                        """
                        SELECT a.value
                        FROM author a
                        JOIN paper_author pa ON a.author_id = pa.author_id
                        WHERE pa.paper_id = ?
                        ORDER BY a.author_id
                        """,
                        (paper_id,),
                    ).fetchall()
                    if str(author_row["value"]).strip()
                ),
                venue=_row_text(row, "venue"),
                tags=", ".join(
                    str(tag_row["value"]).strip()
                    for tag_row in conn.execute(
                        """
                        SELECT t.value
                        FROM tag t
                        JOIN paper_tag pt ON t.tag_id = pt.tag_id
                        WHERE pt.paper_id = ?
                        ORDER BY t.tag_id
                        """,
                        (paper_id,),
                    ).fetchall()
                    if str(tag_row["value"]).strip()
                ),
                source_path="",
            )
            doc = EmbedDocument(doc_id=paper_id, metadata=metadata)

            for tmpl_row in conn.execute(
                "SELECT template_tag FROM paper_summary WHERE paper_id = ? ORDER BY template_tag",
                (paper_id,),
            ).fetchall():
                tag = str(tmpl_row["template_tag"])
                summary_path = static_export_dir / "summary" / paper_id / f"{tag}.json"
                if not summary_path.exists():
                    continue
                summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(summary_data, dict):
                    summary_data["title"] = metadata.title
                    doc.template_records.setdefault(tag, []).append(summary_data)

            source_hash = _row_text(row, "source_md_content_hash")
            if source_hash:
                source_path = static_export_dir / "md" / f"{source_hash}.md"
                if source_path.exists():
                    doc.source_md = source_path.read_text(encoding="utf-8")

            for tr_row in conn.execute(
                "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ? ORDER BY lang",
                (paper_id,),
            ).fetchall():
                lang = str(tr_row["lang"]).strip()
                md_hash = str(tr_row["md_content_hash"]).strip()
                if not lang or not md_hash:
                    continue
                trans_path = static_export_dir / "md_translate" / lang / f"{md_hash}.md"
                if trans_path.exists():
                    doc.translations[lang] = trans_path.read_text(encoding="utf-8")

            docs.append(doc)
        return docs
    finally:
        conn.close()
