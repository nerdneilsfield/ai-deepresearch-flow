"""Supplement snapshot with missing templates or translations for existing papers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table

from deepresearch_flow.paper.db_ops import load_and_merge_papers, build_index


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
    templates_added: int = 0
    translations_added: int = 0
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
        output_db = opts.output_db
        output_static = opts.output_static_dir or opts.static_export_dir
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
        for paper in papers:
            paper_id = paper.get("paper_id") or paper.get("id")
            if not paper_id:
                continue
            
            stats.papers_checked += 1
            
            # Check if paper exists in DB
            existing = conn.execute(
                "SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            
            if not existing:
                # Skip non-existing papers
                continue
            
            # Supplement missing templates
            template_count = _supplement_templates(conn, output_static, paper_id, paper)
            stats.templates_added += template_count
            
            # Supplement missing translations
            translation_count, copied_count = _supplement_translations(
                conn, output_static, paper_id, paper,
                opts.md_translated_roots or []
            )
            stats.translations_added += translation_count
            stats.files_copied += copied_count
        
        conn.commit()
        _print_supplement_summary(stats, output_db, output_static, opts.in_place)
        
    finally:
        conn.close()


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
    md_translated_roots: list[Path]
) -> tuple[int, int]:
    """Add missing translations for a paper. Returns (count_added, files_copied)."""
    count = 0
    copied = 0
    
    # Get source hash to find translation files
    source_hash = paper.get("source_hash") or paper.get("source_md_content_hash")
    if not source_hash:
        return 0, 0
    
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
                    # Compute hash
                    content = trans_file.read_bytes()
                    md_hash = hashlib.sha256(content).hexdigest()
                    
                    # Add translation reference
                    conn.execute(
                        "INSERT OR REPLACE INTO paper_translation (paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                        (paper_id, lang, md_hash),
                    )
                    
                    # Copy to static dir
                    dst_dir = static_dir / "md_translate" / lang
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    dst_file = dst_dir / f"{md_hash}.md"
                    
                    if not dst_file.exists():
                        shutil.copy2(trans_file, dst_file)
                        copied += 1
                    
                    count += 1
                    break
    
    return count, copied


def _print_supplement_summary(
    stats: SupplementStats,
    output_db: Path,
    output_static: Path,
    in_place: bool
) -> None:
    """Print supplement operation summary."""
    action = "Updated" if in_place else "Created"
    
    table = Table(
        title="Snapshot Supplement Summary",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    
    table.add_row("Papers checked", str(stats.papers_checked))
    table.add_row("Templates added", str(stats.templates_added))
    table.add_row("Translations added", str(stats.translations_added))
    table.add_row("Files copied", str(stats.files_copied))
    
    Console().print(table)
    Console().print(f"[green]{action} snapshot DB: {output_db}[/green]")
    Console().print(f"[green]{action} static dir: {output_static}[/green]")
