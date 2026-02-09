"""Update snapshot by adding new papers incrementally."""

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

from deepresearch_flow.paper.db_ops import load_and_merge_papers


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


def update_snapshot(opts: SnapshotUpdateOptions) -> None:
    """Add new papers to existing snapshot."""
    console = Console()
    stats = UpdateStats()
    
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
    
    if not papers:
        console.print("[yellow]No papers found in inputs[/yellow]")
        return
    
    console.print(f"[cyan]Processing {len(papers)} papers...[/cyan]")
    
    # Process each paper
    conn = sqlite3.connect(str(output_db))
    conn.row_factory = sqlite3.Row
    
    try:
        for paper in papers:
            paper_id = paper.get("paper_id") or paper.get("id")
            if not paper_id:
                continue
            
            stats.papers_checked += 1
            
            # Check if paper already exists
            existing = conn.execute(
                "SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            
            if existing:
                # Skip existing papers
                continue
            
            # Add new paper
            _add_new_paper(conn, output_static, paper, opts, stats)
        
        # Recompute facet counts and paper index
        _recompute_metadata(conn)
        
        conn.commit()
        _print_update_summary(stats, output_db, output_static, opts.in_place)
        
    finally:
        conn.close()


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
    opts: SnapshotUpdateOptions,
    stats: UpdateStats
) -> None:
    """Add a completely new paper to the snapshot."""
    paper_id = paper.get("paper_id") or paper.get("id")
    
    # Insert paper record
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
            paper.get("paper_key", ""),
            paper.get("paper_key_type", ""),
            paper.get("title", ""),
            paper.get("year", ""),
            paper.get("month", ""),
            paper.get("publication_date", ""),
            paper.get("venue", ""),
            paper.get("preferred_summary_template", ""),
            paper.get("summary_preview", ""),
            paper.get("source_hash", ""),
            paper.get("output_language", "en"),
            paper.get("provider", ""),
            paper.get("model", ""),
            paper.get("prompt_template", ""),
            paper.get("extracted_at", ""),
            paper.get("pdf_content_hash", ""),
            paper.get("source_md_content_hash", ""),
        ),
    )
    
    stats.papers_added += 1
    
    # Add authors
    authors = paper.get("authors", [])
    if isinstance(authors, list):
        for author in authors:
            name = author.get("name") if isinstance(author, dict) else str(author)
            if name:
                conn.execute("INSERT OR IGNORE INTO author (name) VALUES (?)", (name,))
                conn.execute(
                    "INSERT INTO paper_author (paper_id, author_name) VALUES (?, ?)",
                    (paper_id, name),
                )
    
    # Add keywords
    keywords = paper.get("keywords", [])
    if isinstance(keywords, list):
        for keyword in keywords:
            if isinstance(keyword, str) and keyword:
                conn.execute("INSERT OR IGNORE INTO keyword (value) VALUES (?)", (keyword,))
                conn.execute(
                    "INSERT INTO paper_keyword (paper_id, keyword_value) VALUES (?, ?)",
                    (paper_id, keyword),
                )
    
    # Add institutions
    institutions = paper.get("institutions", [])
    if isinstance(institutions, list):
        for inst in institutions:
            if isinstance(inst, str) and inst:
                conn.execute("INSERT OR IGNORE INTO institution (name) VALUES (?)", (inst,))
                conn.execute(
                    "INSERT INTO paper_institution (paper_id, institution_name) VALUES (?, ?)",
                    (paper_id, inst),
                )
    
    # Add tags
    tags = paper.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag:
                conn.execute("INSERT OR IGNORE INTO tag (value) VALUES (?)", (tag,))
                conn.execute(
                    "INSERT INTO paper_tag (paper_id, tag_value) VALUES (?, ?)",
                    (paper_id, tag),
                )
    
    # Add templates (summaries)
    template_tag = paper.get("prompt_template") or paper.get("template_tag") or "default"
    conn.execute(
        "INSERT INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
        (paper_id, template_tag),
    )
    stats.templates_added += 1
    
    # Write summary JSON
    summary_dir = static_dir / "summary" / paper_id
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_file = summary_dir / f"{template_tag}.json"
    summary_file.write_text(json.dumps(paper, ensure_ascii=False, indent=2))
    
    # Copy source markdown if available
    md_hash = paper.get("source_md_content_hash")
    if md_hash and opts.md_roots:
        for md_root in opts.md_roots:
            src_md = md_root / f"{md_hash}.md"
            if src_md.exists():
                dst_md = static_dir / "md" / f"{md_hash}.md"
                dst_md.parent.mkdir(parents=True, exist_ok=True)
                if not dst_md.exists():
                    shutil.copy2(src_md, dst_md)
                    stats.files_copied += 1
                break
    
    # Copy PDF if available
    pdf_hash = paper.get("pdf_content_hash")
    if pdf_hash and opts.pdf_roots:
        for pdf_root in opts.pdf_roots:
            # Try to find PDF with matching hash
            for pdf_file in pdf_root.rglob("*.pdf"):
                if pdf_file.is_file():
                    content = pdf_file.read_bytes()
                    file_hash = hashlib.sha256(content).hexdigest()
                    if file_hash == pdf_hash:
                        dst_pdf = static_dir / "pdf" / f"{pdf_hash}.pdf"
                        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
                        if not dst_pdf.exists():
                            shutil.copy2(pdf_file, dst_pdf)
                            stats.files_copied += 1
                        break
    
    # Add translations
    if opts.md_translated_roots:
        for root in opts.md_translated_roots:
            if not root.exists():
                continue
            for lang_dir in root.iterdir():
                if not lang_dir.is_dir():
                    continue
                lang = lang_dir.name
                
                # Look for translation files for this paper
                if md_hash:
                    possible_files = [
                        lang_dir / f"{md_hash}.{lang}.md",
                        lang_dir / f"{md_hash}.md",
                        lang_dir / f"{paper_id}.{lang}.md",
                    ]
                    
                    for trans_file in possible_files:
                        if trans_file.exists():
                            content = trans_file.read_bytes()
                            trans_hash = hashlib.sha256(content).hexdigest()
                            
                            conn.execute(
                                "INSERT INTO paper_translation (paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                                (paper_id, lang, trans_hash),
                            )
                            
                            dst_trans = static_dir / "md_translate" / lang / f"{trans_hash}.md"
                            dst_trans.parent.mkdir(parents=True, exist_ok=True)
                            if not dst_trans.exists():
                                shutil.copy2(trans_file, dst_trans)
                                stats.files_copied += 1
                            
                            stats.translations_added += 1
                            break


def _recompute_metadata(conn: sqlite3.Connection) -> None:
    """Recompute facet counts and paper index after adding papers."""
    # Update paper_index
    conn.execute("""
        UPDATE paper SET paper_index = (
            SELECT ROW_NUMBER() OVER (ORDER BY paper_id) - 1
            FROM (SELECT paper_id FROM paper) AS sub
            WHERE sub.paper_id = paper.paper_id
        )
    """)
    
    # Recompute year counts
    conn.execute("DELETE FROM year_count")
    conn.execute("""
        INSERT INTO year_count (year, paper_count)
        SELECT year, COUNT(*) FROM paper WHERE year IS NOT NULL AND year != ''
        GROUP BY year
    """)
    
    # Recompute month counts
    conn.execute("DELETE FROM month_count")
    conn.execute("""
        INSERT INTO month_count (year_month, paper_count)
        SELECT year || '-' || month, COUNT(*) FROM paper
        WHERE year IS NOT NULL AND year != '' AND month IS NOT NULL AND month != ''
        GROUP BY year, month
    """)


def _print_update_summary(
    stats: UpdateStats,
    output_db: Path,
    output_static: Path,
    in_place: bool
) -> None:
    """Print update operation summary."""
    action = "Updated" if in_place else "Created"
    
    table = Table(
        title="Snapshot Update Summary",
        header_style="bold cyan",
        title_style="bold magenta"
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
