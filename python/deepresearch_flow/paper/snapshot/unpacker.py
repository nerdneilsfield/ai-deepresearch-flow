"""Unpack snapshot to recover original files with readable names.

This is the reverse operation of builder.build_snapshot().
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class SnapshotUnpackBaseOptions:
    snapshot_db: Path
    static_export_dir: Path
    pdf_roots: list[Path]


@dataclass(frozen=True)
class SnapshotUnpackMdOptions(SnapshotUnpackBaseOptions):
    md_output_dir: Path
    md_translated_output_dir: Path


@dataclass(frozen=True)
class SnapshotUnpackInfoOptions(SnapshotUnpackBaseOptions):
    template: str
    output_json: Path


@dataclass
class UnpackCounts:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    missing_pdf: int = 0
    translated_succeeded: int = 0
    translated_failed: int = 0


def _sanitize_filename(title: str) -> str:
    """Convert title to safe filename."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", title)
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    sanitized = sanitized.strip()
    if not sanitized:
        sanitized = "untitled"
    return sanitized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_pdf_hash_index(pdf_roots: Iterable[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in pdf_roots:
        if root.is_file() and root.suffix.lower() == ".pdf":
            pdf_hash = _hash_file(root)
            index.setdefault(pdf_hash, root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.pdf"):
            if not path.is_file():
                continue
            pdf_hash = _hash_file(path)
            index.setdefault(pdf_hash, path)
    return index


def _unique_base_name(base: str, paper_id: str, used: set[str]) -> str:
    candidate = base
    if candidate in used:
        candidate = f"{base}_{paper_id}"
    counter = 1
    while candidate in used:
        candidate = f"{base}_{paper_id}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _open_snapshot_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _print_summary(title: str, counts: UnpackCounts) -> None:
    table = Table(title=title, header_style="bold cyan", title_style="bold magenta")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold")
    table.add_row("Total", str(counts.total))
    table.add_row("Succeeded", str(counts.succeeded))
    table.add_row("Failed", str(counts.failed))
    table.add_row("Missing PDF", str(counts.missing_pdf))
    if counts.translated_succeeded or counts.translated_failed:
        table.add_row("Translated succeeded", str(counts.translated_succeeded))
        table.add_row("Translated failed", str(counts.translated_failed))
    Console().print(table)


def unpack_md(opts: SnapshotUnpackMdOptions) -> None:
    """Unpack source/translated markdown and align filenames to PDFs."""
    opts.md_output_dir.mkdir(parents=True, exist_ok=True)
    opts.md_translated_output_dir.mkdir(parents=True, exist_ok=True)

    pdf_index = _build_pdf_hash_index(opts.pdf_roots)
    used_names: set[str] = set()
    counts = UnpackCounts()

    conn = _open_snapshot_db(opts.snapshot_db)
    try:
        cursor = conn.execute(
            """
            SELECT
                paper_id,
                title,
                source_hash,
                pdf_content_hash,
                source_md_content_hash
            FROM paper
            ORDER BY paper_index, title
            """
        )
        for row in cursor.fetchall():
            counts.total += 1
            paper_id = str(row["paper_id"])
            title = str(row["title"] or "")
            pdf_hash = row["pdf_content_hash"]
            md_hash = row["source_md_content_hash"]

            base = ""
            if pdf_hash and pdf_hash in pdf_index:
                base = pdf_index[pdf_hash].stem
            else:
                counts.missing_pdf += 1
                base = _sanitize_filename(title)
            base = _unique_base_name(base, paper_id, used_names)

            if md_hash:
                src_md = opts.static_export_dir / "md" / f"{md_hash}.md"
                if src_md.exists():
                    dst_md = opts.md_output_dir / f"{base}.md"
                    try:
                        dst_md.write_text(src_md.read_text(encoding="utf-8"), encoding="utf-8")
                        counts.succeeded += 1
                    except OSError:
                        counts.failed += 1
                else:
                    counts.failed += 1
            else:
                counts.failed += 1


            for tr_row in conn.execute(
                "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                (paper_id,),
            ):
                lang = str(tr_row["lang"] or "").lower()
                tr_hash = tr_row["md_content_hash"]
                if not lang or not tr_hash:
                    counts.translated_failed += 1
                    continue
                src_tr = opts.static_export_dir / "md_translate" / lang / f"{tr_hash}.md"
                if not src_tr.exists():
                    counts.translated_failed += 1
                    continue
                dst_tr = opts.md_translated_output_dir / f"{base}.{lang}.md"
                try:
                    dst_tr.write_text(src_tr.read_text(encoding="utf-8"), encoding="utf-8")
                    counts.translated_succeeded += 1
                except OSError:
                    counts.translated_failed += 1
    finally:
        conn.close()

    _print_summary("snapshot unpack md summary", counts)


def unpack_info(opts: SnapshotUnpackInfoOptions) -> None:
    """Unpack aggregated paper_infos.json from snapshot summaries."""
    pdf_index = _build_pdf_hash_index(opts.pdf_roots)
    counts = UnpackCounts()
    items: list[dict[str, Any]] = []

    conn = _open_snapshot_db(opts.snapshot_db)
    try:
        cursor = conn.execute(
            """
            SELECT
                paper_id,
                title,
                source_hash,
                pdf_content_hash
            FROM paper
            ORDER BY paper_index, title
            """
        )
        for row in cursor.fetchall():
            counts.total += 1
            paper_id = str(row["paper_id"])
            pdf_hash = row["pdf_content_hash"]
            if not (pdf_hash and pdf_hash in pdf_index):
                counts.missing_pdf += 1

            summary_path = opts.static_export_dir / "summary" / paper_id / f"{opts.template}.json"
            fallback_path = opts.static_export_dir / "summary" / f"{paper_id}.json"
            target_path = summary_path if summary_path.exists() else fallback_path
            used_fallback = target_path == fallback_path
            if not target_path.exists():
                counts.failed += 1
                continue
            try:
                payload = json.loads(target_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                counts.failed += 1
                continue
            if not isinstance(payload, dict):
                counts.failed += 1
                continue

            base = ""
            if pdf_hash and pdf_hash in pdf_index:
                base = pdf_index[pdf_hash].stem
            else:
                base = _sanitize_filename(str(row["title"] or ""))
            source_path = f"{base}.md" if base else ""

            payload["paper_id"] = paper_id
            payload["paper_title"] = str(row["title"] or "")
            payload["source_path"] = source_path
            payload["source_hash"] = str(row["source_hash"] or "")

            if used_fallback:
                counts.failed += 1
            else:
                counts.succeeded += 1
            items.append(payload)
    finally:
        conn.close()

    opts.output_json.parent.mkdir(parents=True, exist_ok=True)
    opts.output_json.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary("snapshot unpack info summary", counts)
