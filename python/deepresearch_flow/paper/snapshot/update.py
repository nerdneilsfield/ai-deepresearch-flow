"""Update snapshot by adding new papers incrementally."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.db_ops import build_index, load_and_merge_papers
from deepresearch_flow.paper.snapshot.identity import (
    build_paper_key_candidates,
    choose_preferred_key,
    paper_id_for_key,
)
from deepresearch_flow.paper.snapshot.schema import recompute_facet_counts, recompute_paper_index
from deepresearch_flow.paper.snapshot.text import insert_cjk_spaces, markdown_to_plain_text
from deepresearch_flow.paper.utils import stable_hash


@dataclass(frozen=True)
class SnapshotUpdateOptions:
    """Options for updating a snapshot with new papers."""

    snapshot_db: Path
    static_export_dir: Path
    input_paths: list[Path]
    bibtex_path: Path | None = None
    md_roots: list[Path] | None = None
    md_translated_roots: list[Path] | None = None
    pdf_roots: list[Path] | None = None
    in_place: bool = False
    output_db: Path | None = None
    output_static_dir: Path | None = None


@dataclass
class UpdateStats:
    """Statistics for update operation."""

    papers_checked: int = 0
    papers_added: int = 0
    templates_added: int = 0
    translations_added: int = 0
    files_copied: int = 0


_WHITESPACE_RE = re.compile(r"\s+")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _normalize_facet_value(value: str | None) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


def _canonical_template_tag(value: str) -> str:
    tag = (value or "").strip().lower()
    tag = re.sub(r"[^a-z0-9_-]+", "_", tag)
    tag = re.sub(r"_+", "_", tag).strip("_")
    return tag or "default"


def _normalize_authors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _extract_venue(paper: dict[str, Any]) -> str:
    bib = paper.get("bibtex")
    if isinstance(bib, dict):
        fields = bib.get("fields") or {}
        if isinstance(fields, dict):
            bib_type = str(bib.get("type") or "").lower()
            if bib_type == "article" and fields.get("journal"):
                return str(fields.get("journal") or "").strip()
            if bib_type in {"inproceedings", "conference", "proceedings"} and fields.get("booktitle"):
                return str(fields.get("booktitle") or "").strip()
            if fields.get("journal"):
                return str(fields.get("journal") or "").strip()
            if fields.get("booktitle"):
                return str(fields.get("booktitle") or "").strip()
    return str(paper.get("publication_venue") or "").strip()


def _parse_year_month(paper: dict[str, Any]) -> tuple[str, str]:
    bib = paper.get("bibtex")
    if isinstance(bib, dict):
        fields = bib.get("fields") or {}
        year = str(fields.get("year") or "").strip()
        month_raw = str(fields.get("month") or "").strip().lower()
        month = ""
        if month_raw.isdigit() and 1 <= int(month_raw) <= 12:
            month = f"{int(month_raw):02d}"
        else:
            month_map = {
                "jan": "01",
                "january": "01",
                "feb": "02",
                "february": "02",
                "mar": "03",
                "march": "03",
                "apr": "04",
                "april": "04",
                "may": "05",
                "jun": "06",
                "june": "06",
                "jul": "07",
                "july": "07",
                "aug": "08",
                "august": "08",
                "sep": "09",
                "sept": "09",
                "september": "09",
                "oct": "10",
                "october": "10",
                "nov": "11",
                "november": "11",
                "dec": "12",
                "december": "12",
            }
            month = month_map.get(month_raw, "")
        if year or month:
            return year, month

    text = str(paper.get("publication_date") or "")
    year_match = re.search(r"(19|20)\d{2}", text)
    year = year_match.group(0) if year_match else ""
    month_match = re.search(r"(19|20)\d{2}[-/](\d{1,2})", text)
    month = ""
    if month_match:
        m = int(month_match.group(2))
        if 1 <= m <= 12:
            month = f"{m:02d}"
    return year, month


def _extract_template_payloads(paper: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    templates = paper.get("templates")
    if isinstance(templates, dict):
        for tag, payload in templates.items():
            if not isinstance(tag, str) or not tag.strip():
                continue
            canonical_tag = _canonical_template_tag(tag)
            if isinstance(payload, dict):
                payloads[canonical_tag] = payload
            elif payload is not None:
                payloads[canonical_tag] = {"summary": str(payload)}

    if not payloads:
        fallback_tag = _canonical_template_tag(
            str(paper.get("prompt_template") or paper.get("template_tag") or "default")
        )
        payloads[fallback_tag] = dict(paper)

    return payloads


def _extract_template_markdown(payload: dict[str, Any]) -> str:
    for key in ("summary", "abstract"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _choose_preferred_template(paper: dict[str, Any], payloads: dict[str, dict[str, Any]]) -> str:
    preferred = _canonical_template_tag(str(paper.get("prompt_template") or paper.get("template_tag") or ""))
    if preferred and preferred in payloads:
        return preferred
    for key in ("simple", "simple_phi"):
        if key in payloads:
            return key
    return sorted(payloads.keys(), key=lambda item: item.lower())[0]


def _summary_preview(markdown: str, max_len: int = 320) -> str:
    if not markdown:
        return ""
    text = markdown_to_plain_text(markdown)
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _paper_source_hash(paper: dict[str, Any]) -> str:
    source_hash = str(paper.get("source_hash") or "").strip()
    if source_hash:
        return source_hash
    source_path = str(paper.get("source_path") or "").strip()
    if source_path:
        return stable_hash(source_path)
    return ""


def _resolve_paper_identity(
    conn: sqlite3.Connection, paper: dict[str, Any]
) -> tuple[str, str, str, str | None, list[Any]]:
    explicit_id = str(paper.get("paper_id") or paper.get("id") or "").strip()
    candidates = build_paper_key_candidates(paper)
    for cand in candidates:
        row = conn.execute(
            "SELECT paper_id FROM paper_key_alias WHERE paper_key = ?",
            (cand.paper_key,),
        ).fetchone()
        if row:
            return str(row["paper_id"]), cand.paper_key, cand.key_type, cand.meta_fingerprint, candidates

    preferred = choose_preferred_key(candidates)
    paper_id = explicit_id or paper_id_for_key(preferred.paper_key)
    return paper_id, preferred.paper_key, preferred.key_type, preferred.meta_fingerprint, candidates


def _upsert_value(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_col: str,
    value: str,
) -> int | None:
    normalized = _normalize_facet_value(value)
    if not normalized or normalized == "unknown":
        return None
    conn.execute(f"INSERT OR IGNORE INTO {table}(value) VALUES (?)", (normalized,))
    row = conn.execute(f"SELECT {id_col} FROM {table} WHERE value = ?", (normalized,)).fetchone()
    return int(row[id_col]) if row else None


def _facet_node_id(conn: sqlite3.Connection, facet_type: str, value: str | None) -> int | None:
    normalized = _normalize_facet_value(value)
    if not normalized or normalized == "unknown":
        return None
    conn.execute(
        "INSERT OR IGNORE INTO facet_node(facet_type, value) VALUES (?, ?)",
        (facet_type, normalized),
    )
    row = conn.execute(
        "SELECT node_id FROM facet_node WHERE facet_type = ? AND value = ?",
        (facet_type, normalized),
    ).fetchone()
    return int(row["node_id"]) if row else None


def _link_dim(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    table: str,
    id_col: str,
    join_table: str,
    join_col: str,
    values: list[str],
) -> list[int]:
    ids: list[int] = []
    for value in values:
        dim_id = _upsert_value(conn, table=table, id_col=id_col, value=value)
        if dim_id is None:
            continue
        conn.execute(
            f"INSERT OR IGNORE INTO {join_table}(paper_id, {join_col}) VALUES (?, ?)",
            (paper_id, dim_id),
        )
        ids.append(dim_id)
    return ids


def _copy_snapshot(src_db: Path, src_static: Path, dst_db: Path, dst_static: Path) -> None:
    """Copy snapshot DB and static files to new location."""

    dst_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dst_db)

    if dst_static != src_static:
        dst_static.mkdir(parents=True, exist_ok=True)
        for subdir in ["pdf", "md", "md_translate", "images", "summary", "manifest"]:
            src = src_static / subdir
            dst = dst_static / subdir
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


def _add_new_paper(
    conn: sqlite3.Connection,
    static_dir: Path,
    paper: dict[str, Any],
    *,
    source_hash: str,
    paper_id: str,
    paper_key: str,
    paper_key_type: str,
    candidates: list[Any],
    md_path: Path | None,
    pdf_path: Path | None,
    translated_paths: dict[str, Path],
    stats: UpdateStats,
) -> None:
    title = str(paper.get("paper_title") or "").strip()
    year, month = _parse_year_month(paper)
    publication_date = str(paper.get("publication_date") or "").strip()
    venue = _extract_venue(paper)

    output_language = str(paper.get("output_language") or "").strip()
    provider = str(paper.get("provider") or "").strip()
    model = str(paper.get("model") or "").strip()
    prompt_template = str(paper.get("prompt_template") or paper.get("template_tag") or "").strip()
    extracted_at = str(paper.get("extracted_at") or "").strip()

    source_md_hash = ""
    if md_path and md_path.exists():
        source_md_hash = _hash_file(md_path)
        dst_md = static_dir / "md" / f"{source_md_hash}.md"
        dst_md.parent.mkdir(parents=True, exist_ok=True)
        if not dst_md.exists():
            shutil.copy2(md_path, dst_md)
            stats.files_copied += 1

    pdf_hash = ""
    if pdf_path and pdf_path.exists():
        pdf_hash = _hash_file(pdf_path)
        dst_pdf = static_dir / "pdf" / f"{pdf_hash}.pdf"
        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        if not dst_pdf.exists():
            shutil.copy2(pdf_path, dst_pdf)
            stats.files_copied += 1

    template_payloads = _extract_template_payloads(paper)
    preferred_summary_template = _choose_preferred_template(paper, template_payloads)
    preferred_markdown = _extract_template_markdown(template_payloads.get(preferred_summary_template, {}))

    conn.execute(
        """
        INSERT INTO paper (
            paper_id, paper_key, paper_key_type, title, year, month,
            publication_date, venue, preferred_summary_template, summary_preview,
            source_hash, output_language, provider, model, prompt_template,
            extracted_at, pdf_content_hash, source_md_content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            paper_key,
            paper_key_type,
            title,
            year,
            month,
            publication_date,
            venue,
            preferred_summary_template,
            _summary_preview(preferred_markdown),
            source_hash,
            output_language,
            provider,
            model,
            prompt_template,
            extracted_at,
            pdf_hash,
            source_md_hash,
        ),
    )
    stats.papers_added += 1

    for cand in candidates:
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_key_alias(paper_key, paper_id, paper_key_type, meta_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            (
                cand.paper_key,
                paper_id,
                cand.key_type,
                cand.meta_fingerprint if cand.key_type == "meta" else None,
            ),
        )

    summary_dir = static_dir / "summary" / paper_id
    summary_dir.mkdir(parents=True, exist_ok=True)
    ordered_template_tags = sorted(template_payloads.keys(), key=lambda item: item.lower())
    for template_tag in ordered_template_tags:
        payload = template_payloads[template_tag]
        conn.execute(
            "INSERT OR IGNORE INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
            (paper_id, template_tag),
        )
        (summary_dir / f"{template_tag}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    stats.templates_added += len(ordered_template_tags)

    preferred_payload = template_payloads.get(preferred_summary_template) or paper
    (static_dir / "summary" / f"{paper_id}.json").parent.mkdir(parents=True, exist_ok=True)
    (static_dir / "summary" / f"{paper_id}.json").write_text(
        json.dumps(preferred_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    translation_hashes: dict[str, str] = {}
    for lang, path in sorted(translated_paths.items()):
        lang_norm = str(lang or "").strip().lower()
        if not lang_norm or not path.exists():
            continue
        trans_hash = _hash_file(path)
        conn.execute(
            "INSERT OR REPLACE INTO paper_translation (paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
            (paper_id, lang_norm, trans_hash),
        )
        dst = static_dir / "md_translate" / lang_norm / f"{trans_hash}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(path, dst)
            stats.files_copied += 1
        translation_hashes[lang_norm] = trans_hash
    stats.translations_added += len(translation_hashes)

    authors = _normalize_authors(paper.get("paper_authors"))
    keywords = _normalize_str_list(paper.get("keywords"))
    institutions = _normalize_str_list(paper.get("paper_institutions"))
    tags = _normalize_str_list(paper.get("ai_generated_tags"))

    _link_dim(
        conn,
        paper_id=paper_id,
        table="author",
        id_col="author_id",
        join_table="paper_author",
        join_col="author_id",
        values=authors,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="keyword",
        id_col="keyword_id",
        join_table="paper_keyword",
        join_col="keyword_id",
        values=keywords,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="institution",
        id_col="institution_id",
        join_table="paper_institution",
        join_col="institution_id",
        values=institutions,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="tag",
        id_col="tag_id",
        join_table="paper_tag",
        join_col="tag_id",
        values=tags,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="venue",
        id_col="venue_id",
        join_table="paper_venue",
        join_col="venue_id",
        values=[venue] if venue else [],
    )

    graph_nodes: set[int] = set()

    def add_graph_nodes(facet_type: str, values: list[str] | str | None) -> None:
        if values is None:
            return
        iterable = values if isinstance(values, list) else [values]
        for item in iterable:
            node_id = _facet_node_id(conn, facet_type, str(item) if item is not None else None)
            if node_id is not None:
                graph_nodes.add(node_id)

    add_graph_nodes("author", authors)
    add_graph_nodes("keyword", keywords)
    add_graph_nodes("institution", institutions)
    add_graph_nodes("tag", tags)
    add_graph_nodes("venue", venue)
    add_graph_nodes("year", year)
    add_graph_nodes("month", month)
    add_graph_nodes("summary_template", ordered_template_tags)
    add_graph_nodes("output_language", output_language)
    add_graph_nodes("provider", provider)
    add_graph_nodes("model", model)
    add_graph_nodes("prompt_template", prompt_template)
    add_graph_nodes("translation_lang", list(translation_hashes.keys()))

    for node_id in graph_nodes:
        conn.execute(
            "INSERT OR IGNORE INTO paper_facet(paper_id, node_id) VALUES (?, ?)",
            (paper_id, node_id),
        )

    node_list = sorted(graph_nodes)
    if len(node_list) > 1:
        edge_rows: list[tuple[int, int]] = []
        for idx, left in enumerate(node_list):
            for right in node_list[idx + 1 :]:
                edge_rows.append((left, right))
        conn.executemany(
            """
            INSERT INTO facet_edge(node_id_a, node_id_b, paper_count)
            VALUES (?, ?, 1)
            ON CONFLICT(node_id_a, node_id_b)
            DO UPDATE SET paper_count = paper_count + 1
            """,
            edge_rows,
        )

    summary_text = markdown_to_plain_text(
        " ".join(_extract_template_markdown(template_payloads[tag]) for tag in ordered_template_tags)
    )
    source_text = ""
    if source_md_hash:
        src_md_file = static_dir / "md" / f"{source_md_hash}.md"
        if src_md_file.exists():
            source_text = markdown_to_plain_text(_safe_read_text(src_md_file))

    translated_parts: list[str] = []
    for lang, md_hash in translation_hashes.items():
        tr_file = static_dir / "md_translate" / lang / f"{md_hash}.md"
        if tr_file.exists():
            translated_parts.append(markdown_to_plain_text(_safe_read_text(tr_file)))
    translated_text = " ".join(part for part in translated_parts if part)

    metadata_text = " ".join(
        part
        for part in [
            title,
            " ".join(authors),
            venue,
            " ".join(keywords),
            " ".join(institutions),
            year,
        ]
        if part
    )

    conn.execute(
        """
        INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            insert_cjk_spaces(title),
            insert_cjk_spaces(summary_text),
            insert_cjk_spaces(source_text),
            insert_cjk_spaces(translated_text),
            insert_cjk_spaces(metadata_text),
        ),
    )
    conn.execute(
        "INSERT INTO paper_fts_trigram(paper_id, title, venue) VALUES (?, ?, ?)",
        (paper_id, title.lower(), venue.lower()),
    )


def update_snapshot(opts: SnapshotUpdateOptions) -> None:
    """Add new papers to existing snapshot."""

    console = Console()
    stats = UpdateStats()

    if not opts.snapshot_db.exists():
        raise FileNotFoundError(f"Snapshot DB not found: {opts.snapshot_db}")
    if not opts.static_export_dir.exists():
        raise FileNotFoundError(f"Static export dir not found: {opts.static_export_dir}")

    if opts.in_place:
        output_db = opts.snapshot_db
        output_static = opts.static_export_dir
        backup_db = opts.snapshot_db.with_suffix(".db.backup")
        shutil.copy2(opts.snapshot_db, backup_db)
        console.print(f"[yellow]Backup created: {backup_db}[/yellow]")
    elif opts.output_db:
        output_db = opts.output_db
        output_static = opts.output_static_dir or opts.static_export_dir
        _copy_snapshot(opts.snapshot_db, opts.static_export_dir, output_db, output_static)
    else:
        raise ValueError("Must specify either --in-place or --output-db")

    if not opts.input_paths:
        console.print("[yellow]No inputs provided, nothing to update[/yellow]")
        return

    console.print("[cyan]Loading input papers...[/cyan]")
    papers = load_and_merge_papers(
        opts.input_paths,
        opts.bibtex_path,
        cache_dir=None,
        use_cache=False,
        pdf_roots=opts.pdf_roots or [],
    )
    papers = [
        paper
        for paper in papers
        if not paper.get("_is_pdf_only")
        and not paper.get("_is_md_only")
        and not paper.get("_is_translated_only")
    ]
    if not papers:
        console.print("[yellow]No papers found in inputs[/yellow]")
        return

    paper_index = build_index(
        papers,
        md_roots=opts.md_roots or [],
        md_translated_roots=opts.md_translated_roots or [],
        pdf_roots=opts.pdf_roots or [],
    )

    console.print(f"[cyan]Processing {len(papers)} papers...[/cyan]")

    conn = sqlite3.connect(str(output_db))
    conn.row_factory = sqlite3.Row

    try:
        for paper in papers:
            stats.papers_checked += 1

            source_hash = _paper_source_hash(paper)
            paper_id, paper_key, paper_key_type, _, candidates = _resolve_paper_identity(conn, paper)

            existing = conn.execute(
                "SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if existing:
                continue

            md_path = paper_index.md_path_by_hash.get(source_hash) if source_hash else None
            pdf_path = paper_index.pdf_path_by_hash.get(source_hash) if source_hash else None
            translated_paths = paper_index.translated_md_by_hash.get(source_hash, {}) if source_hash else {}

            _add_new_paper(
                conn,
                output_static,
                paper,
                source_hash=source_hash,
                paper_id=paper_id,
                paper_key=paper_key,
                paper_key_type=paper_key_type,
                candidates=candidates,
                md_path=md_path,
                pdf_path=pdf_path,
                translated_paths=translated_paths,
                stats=stats,
            )

        recompute_paper_index(conn)
        recompute_facet_counts(conn)

        conn.commit()
        _print_update_summary(stats, output_db, output_static, opts.in_place)
    finally:
        conn.close()


def _print_update_summary(
    stats: UpdateStats,
    output_db: Path,
    output_static: Path,
    in_place: bool,
) -> None:
    """Print update operation summary."""

    action = "Updated" if in_place else "Created"

    table = Table(
        title="Snapshot Update Summary",
        header_style="bold cyan",
        title_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")

    table.add_row("Papers checked", str(stats.papers_checked))
    table.add_row("Papers added", str(stats.papers_added))
    table.add_row("Templates added", str(stats.templates_added))
    table.add_row("Translations added", str(stats.translations_added))
    table.add_row("Files copied", str(stats.files_copied))

    Console().print(table)
    Console().print(f"[green]{action} snapshot DB: {output_db}[/green]")
    Console().print(f"[green]{action} static dir: {output_static}[/green]")
