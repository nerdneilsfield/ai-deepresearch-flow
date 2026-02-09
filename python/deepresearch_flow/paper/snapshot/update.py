"""Update existing snapshot incrementally without rebuilding."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class SnapshotUpdateOptions:
    """Options for updating an existing snapshot."""
    snapshot_db: Path
    static_export_dir: Path
    mode: str = "all"  # "all", "translations", "summaries", "metadata"
    dry_run: bool = False


@dataclass
class UpdateStats:
    """Statistics for update operation."""
    translations_added: int = 0
    translations_updated: int = 0
    summaries_added: int = 0
    summaries_updated: int = 0
    papers_updated: int = 0
    files_processed: int = 0


def update_snapshot(opts: SnapshotUpdateOptions) -> None:
    """
    Update existing snapshot incrementally.
    
    Scans the static export directory for changes and updates the database:
    - New translations in md_translate/
    - New summaries in summary/
    - Updated metadata from JSON files
    """
    console = Console()
    stats = UpdateStats()
    
    if not opts.snapshot_db.exists():
        raise FileNotFoundError(f"Snapshot DB not found: {opts.snapshot_db}")
    if not opts.static_export_dir.exists():
        raise FileNotFoundError(f"Static export dir not found: {opts.static_export_dir}")
    
    conn = sqlite3.connect(str(opts.snapshot_db))
    conn.row_factory = sqlite3.Row
    
    try:
        if opts.mode in ("all", "translations"):
            _update_translations(conn, opts.static_export_dir, stats, opts.dry_run, console)
        
        if opts.mode in ("all", "summaries"):
            _update_summaries(conn, opts.static_export_dir, stats, opts.dry_run, console)
        
        if opts.mode in ("all", "metadata"):
            _update_metadata(conn, opts.static_export_dir, stats, opts.dry_run, console)
        
        if not opts.dry_run:
            conn.commit()
            console.print("[green]Snapshot updated successfully![/green]")
        else:
            console.print("[yellow]Dry run completed. No changes made.[/yellow]")
        
        _print_update_summary(stats, opts.dry_run)
        
    finally:
        conn.close()


def _update_translations(conn: sqlite3.Connection, static_dir: Path, 
                        stats: UpdateStats, dry_run: bool, console: Console) -> None:
    """Update translations from md_translate/ directory."""
    md_translate_dir = static_dir / "md_translate"
    if not md_translate_dir.exists():
        return
    
    # Scan all language subdirectories
    for lang_dir in md_translate_dir.iterdir():
        if not lang_dir.is_dir():
            continue
        
        lang = lang_dir.name
        for md_file in lang_dir.glob("*.md"):
            stats.files_processed += 1
            
            # Extract paper_id from filename (remove .md extension)
            paper_id = md_file.stem
            
            # Compute hash of translation file
            content = md_file.read_bytes()
            md_hash = hashlib.sha256(content).hexdigest()
            
            # Check if translation already exists
            existing = conn.execute(
                """SELECT md_content_hash FROM paper_translation 
                   WHERE paper_id = ? AND lang = ?""",
                (paper_id, lang),
            ).fetchone()
            
            if existing:
                if existing["md_content_hash"] != md_hash:
                    if not dry_run:
                        conn.execute(
                            """UPDATE paper_translation 
                               SET md_content_hash = ? 
                               WHERE paper_id = ? AND lang = ?""",
                            (md_hash, paper_id, lang),
                        )
                    stats.translations_updated += 1
            else:
                if not dry_run:
                    conn.execute(
                        """INSERT INTO paper_translation (paper_id, lang, md_content_hash) 
                           VALUES (?, ?, ?)""",
                        (paper_id, lang, md_hash),
                    )
                stats.translations_added += 1


def _update_summaries(conn: sqlite3.Connection, static_dir: Path,
                     stats: UpdateStats, dry_run: bool, console: Console) -> None:
    """Update summaries from summary/ directory."""
    summary_dir = static_dir / "summary"
    if not summary_dir.exists():
        return
    
    # Scan paper subdirectories
    for paper_dir in summary_dir.iterdir():
        if not paper_dir.is_dir():
            continue
        
        paper_id = paper_dir.name
        
        # Check all template JSON files
        for json_file in paper_dir.glob("*.json"):
            stats.files_processed += 1
            
            template_tag = json_file.stem
            
            # Check if summary already exists
            existing = conn.execute(
                "SELECT 1 FROM paper_summary WHERE paper_id = ? AND template_tag = ?",
                (paper_id, template_tag),
            ).fetchone()
            
            if existing:
                stats.summaries_updated += 1
            else:
                if not dry_run:
                    conn.execute(
                        "INSERT INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
                        (paper_id, template_tag),
                    )
                stats.summaries_added += 1
        
        # Also check for fallback summary (direct json file in summary/)
        fallback_file = summary_dir / f"{paper_id}.json"
        if fallback_file.exists():
            # Use a default template tag for fallback
            template_tag = "default"
            
            existing = conn.execute(
                "SELECT 1 FROM paper_summary WHERE paper_id = ? AND template_tag = ?",
                (paper_id, template_tag),
            ).fetchone()
            
            if not existing:
                if not dry_run:
                    conn.execute(
                        "INSERT INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
                        (paper_id, template_tag),
                    )
                stats.summaries_added += 1


def _update_metadata(conn: sqlite3.Connection, static_dir: Path,
                    stats: UpdateStats, dry_run: bool, console: Console) -> None:
    """Update paper metadata from summary JSON files."""
    summary_dir = static_dir / "summary"
    if not summary_dir.exists():
        return
    
    for paper_dir in summary_dir.iterdir():
        if not paper_dir.is_dir():
            # Check for fallback file
            if paper_dir.suffix == ".json":
                json_file = paper_dir
                paper_id = paper_dir.stem
            else:
                continue
        else:
            # Use preferred template or first available
            paper_id = paper_dir.name
            json_files = list(paper_dir.glob("*.json"))
            if not json_files:
                continue
            json_file = json_files[0]  # Use first template
        
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            continue
        
        if not isinstance(data, dict):
            continue
        
        # Update paper metadata
        title = data.get("title") or data.get("paper_title")
        year = data.get("year")
        venue = data.get("venue")
        
        if title or year or venue:
            if not dry_run:
                # Build update query dynamically
                updates = []
                params = []
                if title:
                    updates.append("title = ?")
                    params.append(title)
                if year:
                    updates.append("year = ?")
                    params.append(year)
                if venue:
                    updates.append("venue = ?")
                    params.append(venue)
                
                if updates:
                    params.append(paper_id)
                    conn.execute(
                        f"UPDATE paper SET {', '.join(updates)} WHERE paper_id = ?",
                        params,
                    )
            stats.papers_updated += 1


def _print_update_summary(stats: UpdateStats, dry_run: bool) -> None:
    """Print update summary table."""
    action = "Would update" if dry_run else "Updated"
    
    table = Table(
        title=f"Snapshot Update Summary {'(Dry Run)' if dry_run else ''}",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    
    table.add_row(f"{action} translations (added)", str(stats.translations_added))
    table.add_row(f"{action} translations (updated)", str(stats.translations_updated))
    table.add_row(f"{action} summaries (added)", str(stats.summaries_added))
    table.add_row(f"{action} summaries (updated)", str(stats.summaries_updated))
    table.add_row(f"{action} paper metadata", str(stats.papers_updated))
    table.add_row("Files processed", str(stats.files_processed))
    
    Console().print(table)


import hashlib  # Import added at top
