"""Supplement snapshot with additional papers or templates (incremental update)."""

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

from deepresearch_flow.paper.snapshot.builder import (
    SnapshotBuildOptions,
    build_snapshot,
    load_and_merge_papers,
)


@dataclass(frozen=True)
class SnapshotSupplementOptions:
    """Options for supplementing an existing snapshot."""
    existing_snapshot_db: Path
    static_export_dir: Path
    supplement_json: Path  # JSON file with papers to add/update
    output_db: Path
    output_static_dir: Path | None = None  # If None, update in-place


def supplement_snapshot(opts: SnapshotSupplementOptions) -> None:
    """
    Supplement an existing snapshot with additional papers or templates.
    
    This performs an incremental update:
    1. Copy existing snapshot DB
    2. Load existing static files
    3. Add/update papers from supplement JSON
    4. Write updated snapshot
    """
    console = Console()
    
    # Check existing snapshot
    if not opts.existing_snapshot_db.exists():
        raise FileNotFoundError(f"Existing snapshot not found: {opts.existing_snapshot_db}")
    
    # Load supplement papers
    supplement_papers = _load_supplement_papers(opts.supplement_json)
    if not supplement_papers:
        console.print("[yellow]No papers to supplement[/yellow]")
        return
    
    console.print(f"[cyan]Supplementing with {len(supplement_papers)} papers...[/cyan]")
    
    # Setup output paths
    output_db = opts.output_db
    output_static = opts.output_static_dir or opts.static_export_dir
    
    # Copy existing snapshot
    _copy_existing_snapshot(opts.existing_snapshot_db, opts.static_export_dir, 
                           output_db, output_static)
    
    # Update database with supplement papers
    stats = _update_snapshot_db(output_db, output_static, supplement_papers)
    
    # Print summary
    _print_supplement_summary(stats, output_db, output_static)


def _load_supplement_papers(json_path: Path) -> list[dict[str, Any]]:
    """Load papers from supplement JSON file."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "papers" in data:
        return data["papers"]
    return [data] if data else []


def _copy_existing_snapshot(existing_db: Path, existing_static: Path,
                           output_db: Path, output_static: Path) -> None:
    """Copy existing snapshot DB and static files to output location."""
    # Copy database
    output_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(existing_db, output_db)
    
    # Copy static files if output is different from existing
    if output_static != existing_static:
        output_static.mkdir(parents=True, exist_ok=True)
        for subdir in ["pdf", "md", "md_translate", "images", "summary", "manifest"]:
            src = existing_static / subdir
            dst = output_static / subdir
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


def _update_snapshot_db(db_path: Path, static_dir: Path, 
                       papers: list[dict[str, Any]]) -> dict[str, int]:
    """Update snapshot database with supplement papers."""
    stats = {"added": 0, "updated": 0, "summaries_added": 0}
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    try:
        for paper in papers:
            paper_id = paper.get("paper_id") or paper.get("id")
            if not paper_id:
                continue
            
            # Check if paper exists
            existing = conn.execute(
                "SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            
            if existing:
                # Update existing paper - mainly add new templates
                stats["updated"] += 1
            else:
                # Insert new paper
                _insert_paper(conn, paper)
                stats["added"] += 1
            
            # Add/update summaries
            template_count = _update_summaries(conn, static_dir, paper_id, paper)
            stats["summaries_added"] += template_count
            
        conn.commit()
    finally:
        conn.close()
    
    return stats


def _insert_paper(conn: sqlite3.Connection, paper: dict[str, Any]) -> None:
    """Insert a new paper into the database."""
    # This is a simplified version - full implementation would need all fields
    conn.execute(
        """
        INSERT OR IGNORE INTO paper (
            paper_id, paper_key, title, year, venue, 
            source_hash, pdf_content_hash, source_md_content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper.get("paper_id") or paper.get("id"),
            paper.get("paper_key", ""),
            paper.get("title", ""),
            paper.get("year", ""),
            paper.get("venue", ""),
            paper.get("source_hash", ""),
            paper.get("pdf_content_hash", ""),
            paper.get("source_md_content_hash", ""),
        ),
    )


def _update_summaries(conn: sqlite3.Connection, static_dir: Path,
                     paper_id: str, paper: dict[str, Any]) -> int:
    """Update summaries for a paper. Returns number of summaries added."""
    count = 0
    
    # Get template from paper
    template = paper.get("prompt_template") or paper.get("template_tag") or "default"
    
    # Check if this template already exists
    existing = conn.execute(
        "SELECT 1 FROM paper_summary WHERE paper_id = ? AND template_tag = ?",
        (paper_id, template),
    ).fetchone()
    
    if not existing:
        # Add summary reference
        conn.execute(
            "INSERT INTO paper_summary (paper_id, template_tag) VALUES (?, ?)",
            (paper_id, template),
        )
        count += 1
        
        # Write summary JSON to static dir
        summary_dir = static_dir / "summary" / paper_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_file = summary_dir / f"{template}.json"
        summary_file.write_text(json.dumps(paper, ensure_ascii=False, indent=2))
    
    return count


def _print_supplement_summary(stats: dict[str, int], output_db: Path, 
                             output_static: Path) -> None:
    """Print supplement operation summary."""
    table = Table(
        title="Snapshot Supplement Summary",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    
    table.add_row("Papers Added", str(stats["added"]))
    table.add_row("Papers Updated", str(stats["updated"]))
    table.add_row("Summaries Added", str(stats["summaries_added"]))
    
    Console().print(table)
    Console().print(f"[green]Updated snapshot DB: {output_db}[/green]")
    Console().print(f"[green]Updated static dir: {output_static}[/green]")
