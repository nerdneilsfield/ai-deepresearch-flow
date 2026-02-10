"""Supplement snapshot with missing templates or translations for existing papers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
from typing import Any

from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.db_ops import load_and_merge_papers
from deepresearch_flow.paper.snapshot.identity import build_paper_key_candidates
from deepresearch_flow.paper.snapshot.image_utils import rewrite_markdown_images, _hash_bytes
from deepresearch_flow.paper.utils import stable_hash


@dataclass(frozen=True)
class SnapshotSupplementOptions:
    """Options for supplementing an existing snapshot."""
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
class SupplementStats:
    """Statistics for supplement operation."""
    papers_checked: int = 0
    papers_supplemented: int = 0
    templates_added: int = 0
    summaries_generated: int = 0
    translations_added: int = 0
    translations_processed: int = 0
    images_extracted: int = 0
    files_copied: int = 0


def supplement_snapshot(opts: SnapshotSupplementOptions) -> None:
    """Supplement existing snapshot with missing templates/translations."""
    console = Console()
    stats = SupplementStats()
    
    # Validate inputs
    if not opts.snapshot_db.exists():
        raise FileNotFoundError(f"Snapshot DB not found: {opts.snapshot_db}")
    if not opts.static_export_dir.exists():
        raise FileNotFoundError(f"Static export dir not found: {opts.static_export_dir}")
    
    # Determine output paths
    if opts.in_place:
        output_db = opts.snapshot_db
        output_static = opts.static_export_dir
        # Backup original
        backup_db = opts.snapshot_db.with_suffix('.db.backup')
        shutil.copy2(opts.snapshot_db, backup_db)
        console.print(f"[yellow]Backup created: {backup_db}[/yellow]")
    elif opts.output_db:
        if not opts.output_static_dir:
            raise ValueError("Must specify --output-static-dir when using --output-db")
        output_db = opts.output_db
        output_static = opts.output_static_dir
        # Copy to new location
        _copy_snapshot(opts.snapshot_db, opts.static_export_dir, output_db, output_static)
    else:
        raise ValueError("Must specify either --in-place or --output-db")
    
    # Load and merge input papers
    if not opts.input_paths:
        console.print("[yellow]No inputs provided, nothing to supplement[/yellow]")
        return
    
    console.print("[cyan]Loading input papers...[/cyan]")
    papers = load_and_merge_papers(
        opts.input_paths,
        opts.bibtex_path,
        cache_dir=None,
        use_cache=False,
        pdf_roots=opts.pdf_roots or [],
    )
    
    if not papers:
        console.print("[yellow]No papers found in inputs[/yellow]")
        return
    
    console.print(f"[cyan]Checking {len(papers)} papers for missing content...[/cyan]")
    
    # Process each paper
    conn = sqlite3.connect(str(output_db))
    conn.row_factory = sqlite3.Row

    try:
        written_images: set[str] = set()
        for paper in papers:
            stats.papers_checked += 1
            paper_ids = _resolve_existing_paper_ids(conn, paper)
            if not paper_ids:
                continue

            for paper_id in paper_ids:
                # Supplement missing templates
                template_count = _supplement_templates(conn, output_static, paper_id, paper)
                stats.templates_added += template_count

                # Supplement missing translations
                translation_count, copied_count, images_count = _supplement_translations(
                    conn, output_static, paper_id, paper,
                    opts.md_translated_roots or [],
                    written_images,
                )
                stats.translations_added += translation_count
                stats.translations_processed += translation_count
                stats.files_copied += copied_count
                stats.images_extracted += images_count
                if template_count > 0 or translation_count > 0:
                    stats.papers_supplemented += 1
        
        conn.commit()
        _print_supplement_summary(stats, output_db, output_static, opts.in_place)
        
    finally:
        conn.close()


def _resolve_existing_paper_ids(conn: sqlite3.Connection, paper: dict[str, Any]) -> list[str]:
    """Resolve one or more paper IDs for supplement inputs that may not include paper_id."""
    def _rows_to_ids(rows: list[sqlite3.Row]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for row in rows:
            paper_id = str(row["paper_id"])
            if paper_id not in seen:
                seen.add(paper_id)
                out.append(paper_id)
        return out

    explicit = paper.get("paper_id") or paper.get("id")
    if isinstance(explicit, str) and explicit.strip():
        paper_id = explicit.strip()
        row = conn.execute("SELECT 1 FROM paper WHERE paper_id = ? LIMIT 1", (paper_id,)).fetchone()
        if row:
            return [paper_id]

    source_hash = paper.get("source_hash")
    if isinstance(source_hash, str) and source_hash.strip():
        normalized = source_hash.strip()
        rows = conn.execute(
            "SELECT paper_id FROM paper WHERE source_hash = ? ORDER BY paper_index",
            (normalized,),
        ).fetchall()
        if rows:
            return _rows_to_ids(rows)
        rows = conn.execute(
            "SELECT paper_id FROM paper WHERE source_md_content_hash = ? ORDER BY paper_index",
            (normalized,),
        ).fetchall()
        if rows:
            return _rows_to_ids(rows)

    source_path = paper.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        path_hash = stable_hash(source_path.strip())
        rows = conn.execute(
            "SELECT paper_id FROM paper WHERE source_hash = ? ORDER BY paper_index",
            (path_hash,),
        ).fetchall()
        if rows:
            return _rows_to_ids(rows)
        path_obj = Path(source_path.strip())
        if path_obj.exists() and path_obj.is_file():
            try:
                md_text = path_obj.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                md_text = path_obj.read_text(encoding="latin-1")
            content_hash = hashlib.sha256(md_text.encode("utf-8", errors="ignore")).hexdigest()
            rows = conn.execute(
                "SELECT paper_id FROM paper WHERE source_md_content_hash = ? ORDER BY paper_index",
                (content_hash,),
            ).fetchall()
            if rows:
                return _rows_to_ids(rows)

    try:
        candidates = build_paper_key_candidates(paper)
    except Exception:
        candidates = []
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        rows = conn.execute(
            "SELECT paper_id FROM paper_key_alias WHERE paper_key = ?",
            (candidate.paper_key,),
        ).fetchall()
        for row in rows:
            paper_id = str(row["paper_id"])
            if paper_id in seen:
                continue
            seen.add(paper_id)
            out.append(paper_id)
    return out


def _copy_snapshot(src_db: Path, src_static: Path, dst_db: Path, dst_static: Path) -> None:
    """Copy snapshot DB and static files to new location."""
    # Copy DB
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dst_db)
    
    # Copy static if different
    if dst_static != src_static:
        dst_static.mkdir(parents=True, exist_ok=True)
        for subdir in ["pdf", "md", "md_translate", "images", "summary", "manifest"]:
            src = src_static / subdir
            dst = dst_static / subdir
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


def _supplement_templates(
    conn: sqlite3.Connection,
    static_dir: Path,
    paper_id: str,
    paper: dict[str, Any]
) -> int:
    """Add missing templates for a paper. Returns count added."""
    count = 0
    
    # Get template tag from paper
    template_tag = paper.get("prompt_template") or paper.get("template_tag") or "default"
    
    # Check if template already exists
    existing = conn.execute(
        "SELECT 1 FROM paper_summary WHERE paper_id = ? AND template_tag = ?",
        (paper_id, template_tag),
    ).fetchone()
    
    if not existing:
        # Add template reference
        conn.execute(
            "INSERT INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
            (paper_id, template_tag),
        )
        
        # Write summary JSON
        summary_dir = static_dir / "summary" / paper_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_file = summary_dir / f"{template_tag}.json"
        summary_file.write_text(json.dumps(paper, ensure_ascii=False, indent=2))
        
        count += 1
    
    return count


def _supplement_translations(
    conn: sqlite3.Connection,
    static_dir: Path,
    paper_id: str,
    paper: dict[str, Any],
    md_translated_roots: list[Path],
    written_images: set[str],
) -> tuple[int, int, int]:
    """Add missing translations for a paper. Returns (count_added, files_copied, images_extracted)."""
    count = 0
    copied = 0
    images_count = 0

    # Get source hash to find translation files
    source_hash = paper.get("source_hash") or paper.get("source_md_content_hash")
    if not source_hash:
        return 0, 0, 0
    
    # Scan translated directories for this paper
    for root in md_translated_roots:
        if not root.exists():
            continue
        
        for lang_dir in root.iterdir():
            if not lang_dir.is_dir():
                continue
            
            lang = lang_dir.name
            
            # Check if translation already exists in DB
            existing = conn.execute(
                "SELECT 1 FROM paper_translation WHERE paper_id = ? AND lang = ?",
                (paper_id, lang),
            ).fetchone()
            
            if existing:
                continue
            
            # Look for translation file
            # Try different naming patterns
            possible_names = [
                f"{source_hash}.{lang}.md",
                f"{source_hash}.md",
                f"{paper_id}.{lang}.md",
            ]
            
            for trans_file in lang_dir.iterdir():
                if trans_file.suffix != ".md":
                    continue
                
                # Check if this file belongs to our paper
                if any(name in trans_file.name for name in possible_names):
                    # Process translated markdown: extract images and rewrite paths
                    try:
                        trans_content = trans_file.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        trans_content = trans_file.read_text(encoding="latin-1")

                    images_dir = static_dir / "images"
                    processed_trans, trans_images = rewrite_markdown_images(
                        trans_content,
                        source_path=trans_file,
                        images_output_dir=images_dir,
                        written=written_images,
                    )
                    images_count += len([img for img in trans_images if img.get("status") == "available"])

                    # Compute hash of processed content
                    md_hash = _hash_bytes(processed_trans.encode("utf-8"))

                    # Add translation reference
                    conn.execute(
                        "INSERT OR REPLACE INTO paper_translation (paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                        (paper_id, lang, md_hash),
                    )

                    # Save processed content to static dir
                    dst_dir = static_dir / "md_translate" / lang
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    dst_file = dst_dir / f"{md_hash}.md"

                    if not dst_file.exists():
                        dst_file.write_text(processed_trans, encoding="utf-8")
                        copied += 1

                    count += 1
                    break

    return count, copied, images_count


def _print_supplement_summary(
    stats: SupplementStats,
    output_db: Path,
    output_static: Path,
    in_place: bool
) -> None:
    """Print supplement operation summary."""
    from rich.panel import Panel

    console = Console()
    action = "Updated" if in_place else "Created"

    console.print()
    console.print(f"[bold cyan]Snapshot Supplement Summary[/bold cyan]", style="bold")
    console.print()

    # Papers table
    papers_table = Table(title="Papers", header_style="bold magenta", box=None)
    papers_table.add_column("Metric", style="cyan")
    papers_table.add_column("Count", style="green", justify="right")
    papers_table.add_row("Total checked", str(stats.papers_checked))
    papers_table.add_row("Supplemented", str(stats.papers_supplemented))
    console.print(papers_table)
    console.print()

    # Content Processing table
    processing_table = Table(title="Content Processing", header_style="bold magenta", box=None)
    processing_table.add_column("Type", style="cyan")
    processing_table.add_column("Count", style="green", justify="right")
    processing_table.add_column("Details", style="yellow")
    processing_table.add_row("Translations processed", str(stats.translations_processed), "Images extracted & paths rewritten")
    processing_table.add_row("Images extracted", str(stats.images_extracted), "From translations")
    console.print(processing_table)
    console.print()

    # Metadata table
    metadata_table = Table(title="Metadata Added", header_style="bold magenta", box=None)
    metadata_table.add_column("Metric", style="cyan")
    metadata_table.add_column("Count", style="green", justify="right")
    metadata_table.add_row("Templates added", str(stats.templates_added))
    if stats.summaries_generated > 0:
        metadata_table.add_row("Summaries generated", str(stats.summaries_generated))
    metadata_table.add_row("Translations added", str(stats.translations_added))
    console.print(metadata_table)
    console.print()

    # Files table
    files_table = Table(title="Files", header_style="bold magenta", box=None)
    files_table.add_column("Metric", style="cyan")
    files_table.add_column("Count", style="green", justify="right")
    files_table.add_row("Total files copied", str(stats.files_copied))
    console.print(files_table)
    console.print()

    # Success panel
    console.print(Panel(
        f"[bold green]{action} Successfully![/bold green]\n\n"
        f"Database: [yellow]{output_db}[/yellow]\n"
        f"Static: [yellow]{output_static}[/yellow]",
        border_style="green",
        padding=(1, 2)
    ))
