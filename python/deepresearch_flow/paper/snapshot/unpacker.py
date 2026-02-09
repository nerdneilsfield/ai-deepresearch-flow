"""Unpack snapshot to recover original files with readable names.

This is the reverse operation of builder.build_snapshot().
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from rich.console import Console
from rich.table import Table
from tqdm import tqdm


# Image embedding helpers
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def _mime_from_path(path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if path.suffix.lower() == ".png":
        return "image/png"
    if path.suffix.lower() == ".gif":
        return "image/gif"
    if path.suffix.lower() == ".webp":
        return "image/webp"
    if path.suffix.lower() == ".svg":
        return "image/svg+xml"
    return None


def _data_url_from_bytes(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _split_link_target(raw_link: str) -> tuple[str, str, str, str]:
    link = raw_link.strip()
    if link.startswith("<"):
        end = link.find(">")
        if end != -1:
            return link[1:end], link[end + 1 :], "<", ">"
    parts = link.split(maxsplit=1)
    if not parts:
        return "", "", "", ""
    target = parts[0]
    suffix = link[len(target) :]
    return target, suffix, "", ""


def _embed_images_in_markdown(content: str, md_file_path: Path, images_root: Path) -> str:
    """Embed local images as base64 data URLs in markdown content.
    
    Args:
        content: Markdown file content
        md_file_path: Path to the markdown file (for resolving relative paths)
        images_root: Root directory where images are stored
    """
    output: list[str] = []
    last_idx = 0
    
    for match in IMAGE_PATTERN.finditer(content):
        output.append(content[last_idx:match.start()])
        alt_text = match.group(1)
        raw_link = match.group(2)
        
        # Parse link (handle optional angle bracket and title suffix)
        target, suffix, wrap_prefix, wrap_suffix = _split_link_target(raw_link)
        
        # Skip if already data URL or http URL
        if target.startswith("data:") or target.startswith("http://") or target.startswith("https://"):
            output.append(match.group(0))
            last_idx = match.end()
            continue
        
        # Resolve image path - images are in images_root
        # Target could be like "images/xxx.png" or just "xxx.png"
        img_path = images_root / Path(target).name
        
        if not img_path.exists():
            # Try full path
            img_path = images_root / target
            if not img_path.exists():
                output.append(match.group(0))
                last_idx = match.end()
                continue
        
        # Check if it's an image
        mime = _mime_from_path(img_path)
        if not mime or not mime.startswith("image/"):
            output.append(match.group(0))
            last_idx = match.end()
            continue
        
        # Read and embed
        try:
            data = img_path.read_bytes()
            data_url = _data_url_from_bytes(mime, data)
            new_link = f"{wrap_prefix}{data_url}{wrap_suffix}{suffix}"
            output.append(f"![{alt_text}]({new_link})")
        except Exception:
            output.append(match.group(0))
        
        last_idx = match.end()
    
    output.append(content[last_idx:])
    return "".join(output)


@dataclass(frozen=True)
class SnapshotUnpackBaseOptions:
    snapshot_db: Path
    static_export_dir: Path
    pdf_roots: list[Path]


@dataclass(frozen=True)
class SnapshotUnpackMdOptions(SnapshotUnpackBaseOptions):
    md_output_dir: Path
    md_translated_output_dir: Path
    dry_run: bool = False
    log_json: Path | None = None


@dataclass(frozen=True)
class SnapshotUnpackInfoOptions(SnapshotUnpackBaseOptions):
    template: str
    output_json: Path
    log_json: Path | None = None


@dataclass
class UnpackCounts:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    missing_pdf: int = 0
    translated_succeeded: int = 0
    translated_failed: int = 0
    # Detailed failure reason statistics
    failed_no_md_hash: int = 0  # No source Markdown hash
    failed_md_file_missing: int = 0  # Source Markdown file does not exist
    failed_md_write_error: int = 0  # Write error (OSError)
    failed_tr_no_hash: int = 0  # No translation hash or language
    failed_tr_file_missing: int = 0  # Translation file does not exist
    failed_tr_write_error: int = 0  # Translation write error
    # Failed paper details: list of (paper_id, title, reason)
    failed_details: list[tuple[str, str, str]] = field(default_factory=list)


def _sanitize_filename(title: str) -> str:
    """Convert title to safe filename."""
    sanitized = re.sub(r'[<>:"/\|?*]', "_", title)
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


def _print_summary(title: str, counts: UnpackCounts, is_info: bool = False) -> None:
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

    # Show detailed failure reasons
    if counts.failed > 0:
        if is_info:
            # Failure reasons for unpack info
            fail_table = Table(
                title="Failed Reasons",
                header_style="bold red",
                title_style="bold magenta"
            )
            fail_table.add_column("Reason", style="cyan", no_wrap=True)
            fail_table.add_column("Count", style="white", overflow="fold")
            fail_table.add_row("Template not found (using fallback)", str(counts.failed_no_md_hash))
            fail_table.add_row("Summary file missing", str(counts.failed_md_file_missing))
            fail_table.add_row("JSON decode / invalid payload", str(counts.failed_md_write_error))
            Console().print(fail_table)
        else:
            # Failure reasons for unpack md
            fail_table = Table(
                title="Source Markdown Failed Reasons",
                header_style="bold red",
                title_style="bold magenta"
            )
            fail_table.add_column("Reason", style="cyan", no_wrap=True)
            fail_table.add_column("Count", style="white", overflow="fold")
            fail_table.add_row("No source Markdown hash", str(counts.failed_no_md_hash))
            fail_table.add_row("Source Markdown file missing", str(counts.failed_md_file_missing))
            fail_table.add_row("Write error (OSError)", str(counts.failed_md_write_error))
            Console().print(fail_table)

    # Show detailed failure reasons for translations (only for unpack md)
    if not is_info and counts.translated_failed > 0:
        tr_fail_table = Table(
            title="Translated Markdown Failed Reasons",
            header_style="bold red",
            title_style="bold magenta"
        )
        tr_fail_table.add_column("Reason", style="cyan", no_wrap=True)
        tr_fail_table.add_column("Count", style="white", overflow="fold")
        tr_fail_table.add_row("No translation hash or lang", str(counts.failed_tr_no_hash))
        tr_fail_table.add_row("Translation file missing", str(counts.failed_tr_file_missing))
        tr_fail_table.add_row("Write error (OSError)", str(counts.failed_tr_write_error))
        Console().print(tr_fail_table)

    # Show examples of failed papers with title and reason
    if counts.failed_details:
        examples_table = Table(
            title=f"Failed Paper Examples (showing up to 10 of {len(counts.failed_details)})",
            header_style="bold yellow",
            title_style="bold magenta"
        )
        examples_table.add_column("Paper ID", style="yellow", no_wrap=True, overflow="fold")
        examples_table.add_column("Title", style="white", overflow="fold")
        examples_table.add_column("Reason", style="red", overflow="fold")
        for pid, title, reason in counts.failed_details[:10]:
            # Truncate title if too long
            display_title = title[:60] + "..." if len(title) > 60 else title
            examples_table.add_row(pid, display_title or "(No title)", reason)
        Console().print(examples_table)


def unpack_md(opts: SnapshotUnpackMdOptions) -> None:
    """Unpack source/translated markdown and align filenames to PDFs."""
    if not opts.dry_run:
        opts.md_output_dir.mkdir(parents=True, exist_ok=True)
        opts.md_translated_output_dir.mkdir(parents=True, exist_ok=True)

    pdf_index = _build_pdf_hash_index(opts.pdf_roots)
    used_names: set[str] = set()
    counts = UnpackCounts()

    conn = _open_snapshot_db(opts.snapshot_db)
    try:
        # First, get total count for progress bar
        cursor = conn.execute("SELECT COUNT(*) as total FROM paper")
        total_papers = cursor.fetchone()["total"]

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

        with tqdm(total=total_papers, desc="Unpacking", unit="paper") as pbar:
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
                        if not opts.dry_run:
                            try:
                                content = src_md.read_text(encoding="utf-8")
                                # Embed images as base64
                                images_root = opts.static_export_dir / "images"
                                if images_root.exists():
                                    content = _embed_images_in_markdown(content, src_md, images_root)
                                dst_md.write_text(content, encoding="utf-8")
                            except OSError as e:
                                counts.failed += 1
                                counts.failed_md_write_error += 1
                                counts.failed_details.append((paper_id, title, f"Write error: {e}"))
                                pbar.update(1)
                                continue
                        counts.succeeded += 1
                    else:
                        counts.failed += 1
                        counts.failed_md_file_missing += 1
                        counts.failed_details.append((paper_id, title, "Source Markdown file missing"))
                else:
                    counts.failed += 1
                    counts.failed_no_md_hash += 1
                    counts.failed_details.append((paper_id, title, "No source Markdown hash"))
                    pbar.update(1)
                    continue  # Skip translation if no source MD

                # Process translations
                for tr_row in conn.execute(
                    "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
                    (paper_id,),
                ):
                    lang = str(tr_row["lang"] or "").lower()
                    tr_hash = tr_row["md_content_hash"]
                    if not lang or not tr_hash:
                        counts.translated_failed += 1
                        counts.failed_tr_no_hash += 1
                        continue
                    src_tr = opts.static_export_dir / "md_translate" / lang / f"{tr_hash}.md"
                    if not src_tr.exists():
                        counts.translated_failed += 1
                        counts.failed_tr_file_missing += 1
                        continue
                    dst_tr = opts.md_translated_output_dir / f"{base}.{lang}.md"
                    if not opts.dry_run:
                        try:
                            content = src_tr.read_text(encoding="utf-8")
                            # Embed images as base64
                            images_root = opts.static_export_dir / "images"
                            if images_root.exists():
                                content = _embed_images_in_markdown(content, src_tr, images_root)
                            dst_tr.write_text(content, encoding="utf-8")
                        except OSError:
                            counts.translated_failed += 1
                            counts.failed_tr_write_error += 1
                            continue
                    counts.translated_succeeded += 1

                pbar.update(1)
    finally:
        conn.close()

    _print_summary("snapshot unpack md summary", counts)
    
    # Export error log if requested
    if opts.log_json and counts.failed_details:
        _export_error_log(opts.log_json, counts, "unpack_md")


def _export_error_log(log_path: Path, counts: UnpackCounts, command: str) -> None:
    """Export detailed error log to JSON file."""
    log_data = {
        "command": command,
        "summary": {
            "total": counts.total,
            "succeeded": counts.succeeded,
            "failed": counts.failed,
            "missing_pdf": counts.missing_pdf,
        },
        "failure_reasons": {
            "no_source_md_hash": counts.failed_no_md_hash,
            "source_md_file_missing": counts.failed_md_file_missing,
            "write_error": counts.failed_md_write_error,
            "translation_no_hash": counts.failed_tr_no_hash,
            "translation_file_missing": counts.failed_tr_file_missing,
            "translation_write_error": counts.failed_tr_write_error,
        },
        "failed_papers": [
            {
                "paper_id": pid,
                "title": title,
                "reason": reason,
            }
            for pid, title, reason in counts.failed_details
        ],
    }
    
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    Console().print(f"[green]Error log exported to: {log_path}[/green]")


def unpack_info(opts: SnapshotUnpackInfoOptions) -> None:
    """Unpack aggregated paper_infos.json from snapshot summaries."""
    pdf_index = _build_pdf_hash_index(opts.pdf_roots)
    counts = UnpackCounts()
    items: list[dict[str, Any]] = []

    conn = _open_snapshot_db(opts.snapshot_db)
    try:
        # First, get total count for progress bar
        cursor = conn.execute("SELECT COUNT(*) as total FROM paper")
        total_papers = cursor.fetchone()["total"]

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
        
        with tqdm(total=total_papers, desc="Unpacking info", unit="paper") as pbar:
            for row in cursor.fetchall():
                counts.total += 1
                paper_id = str(row["paper_id"])
                title = str(row["title"] or "")
                pdf_hash = row["pdf_content_hash"]
                if not (pdf_hash and pdf_hash in pdf_index):
                    counts.missing_pdf += 1

                summary_path = opts.static_export_dir / "summary" / paper_id / f"{opts.template}.json"
                fallback_path = opts.static_export_dir / "summary" / f"{paper_id}.json"
                target_path = summary_path if summary_path.exists() else fallback_path
                used_fallback = target_path == fallback_path
                
                if not target_path.exists():
                    counts.failed += 1
                    counts.failed_no_md_hash += 1  # Using this field for "Summary file missing"
                    counts.failed_details.append((paper_id, title, f"Summary not found for template '{opts.template}'"))
                    pbar.update(1)
                    continue
                    
                try:
                    payload = json.loads(target_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    counts.failed += 1
                    counts.failed_md_file_missing += 1  # Using this field for "JSON decode error"
                    counts.failed_details.append((paper_id, title, f"JSON decode error: {e}"))
                    pbar.update(1)
                    continue
                    
                if not isinstance(payload, dict):
                    counts.failed += 1
                    counts.failed_md_write_error += 1  # Using this field for "Invalid payload type"
                    counts.failed_details.append((paper_id, title, "Invalid payload type (not a dict)"))
                    pbar.update(1)
                    continue

                base = ""
                if pdf_hash and pdf_hash in pdf_index:
                    base = pdf_index[pdf_hash].stem
                else:
                    base = _sanitize_filename(str(row["title"] or ""))
                source_path = f"{base}.md" if base else ""

                payload["paper_id"] = paper_id
                payload["paper_title"] = title
                payload["source_path"] = source_path
                payload["source_hash"] = str(row["source_hash"] or "")

                if used_fallback:
                    counts.failed += 1
                    counts.failed_no_md_hash += 1  # Using this field for "Template not found, using fallback"
                    counts.failed_details.append((paper_id, title, f"Using fallback summary (template '{opts.template}' not found)"))
                else:
                    counts.succeeded += 1
                items.append(payload)
                pbar.update(1)
    finally:
        conn.close()

    opts.output_json.parent.mkdir(parents=True, exist_ok=True)
    opts.output_json.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary("snapshot unpack info summary", counts, is_info=True)
    
    # Export error log if requested
    if opts.log_json and counts.failed_details:
        _export_error_log(opts.log_json, counts, "unpack_info")
