"""Commands to identify and export missing snapshot artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class ShowMissingOptions:
    snapshot_db: Path
    static_export_dir: Path | None = None


@dataclass(frozen=True)
class ExportMissingOptions:
    snapshot_db: Path
    missing_type: str  # "source_md", "pdf", "template", "translation"
    template: str | None = None  # for "template" type
    lang: str | None = None  # for "translation" type
    output_json: Path | None = None
    output_txt: Path | None = None
    output_paths: Path | None = None  # Export file paths for extraction


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def show_missing(opts: ShowMissingOptions) -> None:
    """Show comprehensive missing artifacts report with multiple tables."""
    conn = _open_db(opts.snapshot_db)
    
    try:
        # Table 1: Basic content missing (Source MD, PDF)
        _show_basic_missing(conn)
        
        # Table 2: Template coverage
        _show_template_coverage(conn)
        
        # Table 3: Translation coverage
        _show_translation_coverage(conn)
        
        # Table 4: Static file existence (if static_export_dir provided)
        if opts.static_export_dir:
            _show_static_missing(conn, opts.static_export_dir)
            
    finally:
        conn.close()


def _show_basic_missing(conn: sqlite3.Connection) -> None:
    """Show missing source markdown and PDF."""
    cursor = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN source_md_content_hash IS NULL OR source_md_content_hash = '' THEN 1 ELSE 0 END) as no_source_md,
            SUM(CASE WHEN pdf_content_hash IS NULL OR pdf_content_hash = '' THEN 1 ELSE 0 END) as no_pdf
        FROM paper
    """)
    row = cursor.fetchone()
    
    table = Table(
        title="Basic Content Missing",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Content Type", style="cyan")
    table.add_column("Missing Count", style="red", justify="right")
    table.add_column("Total Papers", style="white", justify="right")
    table.add_column("Percentage", style="yellow", justify="right")
    
    total = row["total"]
    no_source_md = row["no_source_md"]
    no_pdf = row["no_pdf"]
    
    table.add_row(
        "Source Markdown",
        str(no_source_md),
        str(total),
        f"{no_source_md/total*100:.1f}%"
    )
    table.add_row(
        "PDF Content",
        str(no_pdf),
        str(total),
        f"{no_pdf/total*100:.1f}%"
    )
    
    Console().print(table)
    Console().print()


def _show_template_coverage(conn: sqlite3.Connection) -> None:
    """Show template coverage statistics."""
    # Get all templates
    cursor = conn.execute("SELECT DISTINCT template_tag FROM paper_summary ORDER BY template_tag")
    templates = [row[0] for row in cursor.fetchall()]
    
    if not templates:
        Console().print("[yellow]No templates found in database[/yellow]")
        return
    
    # Get counts for each template
    cursor = conn.execute("SELECT COUNT(*) as total FROM paper")
    total = cursor.fetchone()["total"]
    
    table = Table(
        title="Template Coverage",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Template", style="cyan")
    table.add_column("Has Template", style="green", justify="right")
    table.add_column("Missing", style="red", justify="right")
    table.add_column("Total", style="white", justify="right")
    table.add_column("Coverage", style="yellow", justify="right")
    
    for template in templates:
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_summary WHERE template_tag = ?",
            (template,)
        )
        has_count = cursor.fetchone()["cnt"]
        missing = total - has_count
        coverage = has_count / total * 100
        
        table.add_row(
            template,
            str(has_count),
            str(missing),
            str(total),
            f"{coverage:.1f}%"
        )
    
    Console().print(table)
    Console().print()


def _show_translation_coverage(conn: sqlite3.Connection) -> None:
    """Show translation coverage statistics."""
    cursor = conn.execute("SELECT DISTINCT lang FROM paper_translation ORDER BY lang")
    languages = [row[0] for row in cursor.fetchall()]
    
    if not languages:
        Console().print("[yellow]No translations found in database[/yellow]")
        return
    
    cursor = conn.execute("SELECT COUNT(*) as total FROM paper")
    total = cursor.fetchone()["total"]
    
    table = Table(
        title="Translation Coverage",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("Language", style="cyan")
    table.add_column("Has Translation", style="green", justify="right")
    table.add_column("Missing", style="red", justify="right")
    table.add_column("Total", style="white", justify="right")
    table.add_column("Coverage", style="yellow", justify="right")
    
    for lang in languages:
        cursor = conn.execute(
            "SELECT COUNT(DISTINCT paper_id) as cnt FROM paper_translation WHERE lang = ?",
            (lang,)
        )
        has_count = cursor.fetchone()["cnt"]
        missing = total - has_count
        coverage = has_count / total * 100
        
        table.add_row(
            lang,
            str(has_count),
            str(missing),
            str(total),
            f"{coverage:.1f}%"
        )
    
    Console().print(table)
    Console().print()


def _show_static_missing(conn: sqlite3.Connection, static_dir: Path) -> None:
    """Show missing static files."""
    cursor = conn.execute("SELECT paper_id, source_md_content_hash FROM paper")
    
    missing_md = 0
    missing_pdf = 0
    total = 0
    
    for row in cursor.fetchall():
        total += 1
        paper_id = row["paper_id"]
        md_hash = row["source_md_content_hash"]
        
        if md_hash:
            md_file = static_dir / "md" / f"{md_hash}.md"
            if not md_file.exists():
                missing_md += 1
    
    table = Table(
        title="Static File Existence",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    table.add_column("File Type", style="cyan")
    table.add_column("Missing Files", style="red", justify="right")
    table.add_column("Checked", style="white", justify="right")
    
    table.add_row("Markdown files", str(missing_md), str(total))
    
    Console().print(table)


def export_missing(opts: ExportMissingOptions) -> None:
    """Export list of papers missing specified artifact."""
    conn = _open_db(opts.snapshot_db)
    
    try:
        missing_papers = []
        
        if opts.missing_type == "source_md":
            missing_papers = _get_missing_source_md(conn)
            title = "Source Markdown"
        elif opts.missing_type == "pdf":
            missing_papers = _get_missing_pdf(conn)
            title = "PDF"
        elif opts.missing_type == "template":
            if not opts.template:
                raise ValueError("--template is required when exporting missing templates")
            missing_papers = _get_missing_template(conn, opts.template)
            title = f"Template '{opts.template}'"
        elif opts.missing_type == "translation":
            if not opts.lang:
                raise ValueError("--lang is required when exporting missing translations")
            missing_papers = _get_missing_translation(conn, opts.lang)
            title = f"Translation '{opts.lang}'"
        else:
            raise ValueError(f"Unknown missing_type: {opts.missing_type}")
        
        # Print summary
        Console().print(f"[bold cyan]Exporting {len(missing_papers)} papers missing {title}[/bold cyan]")
        
        # Export to JSON
        if opts.output_json:
            opts.output_json.parent.mkdir(parents=True, exist_ok=True)
            opts.output_json.write_text(
                json.dumps(missing_papers, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            Console().print(f"[green]Exported JSON to: {opts.output_json}[/green]")
        
        # Export to TXT (paper IDs only)
        if opts.output_txt:
            opts.output_txt.parent.mkdir(parents=True, exist_ok=True)
            opts.output_txt.write_text(
                "\n".join([p["paper_id"] for p in missing_papers]),
                encoding="utf-8"
            )
            Console().print(f"[green]Exported TXT to: {opts.output_txt}[/green]")
        
        # Export file paths (for use with --input-list)
        if opts.output_paths and missing_papers:
            # Build file paths from source_md_content_hash
            paths = []
            for p in missing_papers:
                md_hash = p.get("source_md_content_hash")
                if md_hash:
                    # Path relative to md root: {hash}.md or {hash}/{filename}.md
                    # Try common patterns
                    paths.append(f"{md_hash}.md")
            
            if paths:
                opts.output_paths.parent.mkdir(parents=True, exist_ok=True)
                opts.output_paths.write_text("\n".join(paths), encoding="utf-8")
                Console().print(f"[green]Exported file paths to: {opts.output_paths}[/green]")
                Console().print(f"[cyan]Use with: paper extract --input ./md --input-list {opts.output_paths}[/cyan]")
        
        # Show first 10 examples
        if missing_papers:
            table = Table(
                title=f"Missing {title} Examples (showing up to 10)",
                header_style="bold yellow",
                title_style="bold magenta"
            )
            table.add_column("Paper ID", style="yellow", no_wrap=True)
            table.add_column("Title", style="white", overflow="fold")
            
            for p in missing_papers[:10]:
                t = p["title"][:60] + "..." if len(p["title"]) > 60 else p["title"]
                table.add_row(p["paper_id"], t or "(No title)")
            
            Console().print(table)
            
    finally:
        conn.close()


def _get_missing_source_md(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get papers missing source markdown."""
    cursor = conn.execute("""
        SELECT paper_id, title, source_hash
        FROM paper
        WHERE source_md_content_hash IS NULL OR source_md_content_hash = ''
        ORDER BY paper_index
    """)
    return [
        {
            "paper_id": row["paper_id"],
            "title": row["title"] or "",
            "source_hash": row["source_hash"] or "",
        }
        for row in cursor.fetchall()
    ]


def _get_missing_pdf(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get papers missing PDF."""
    cursor = conn.execute("""
        SELECT paper_id, title, source_hash
        FROM paper
        WHERE pdf_content_hash IS NULL OR pdf_content_hash = ''
        ORDER BY paper_index
    """)
    return [
        {
            "paper_id": row["paper_id"],
            "title": row["title"] or "",
            "source_hash": row["source_hash"] or "",
        }
        for row in cursor.fetchall()
    ]


def _get_missing_template(conn: sqlite3.Connection, template: str) -> list[dict[str, Any]]:
    """Get papers missing specified template."""
    cursor = conn.execute("""
        SELECT p.paper_id, p.title, p.source_hash, p.source_md_content_hash
        FROM paper p
        WHERE p.paper_id NOT IN (
            SELECT paper_id FROM paper_summary WHERE template_tag = ?
        )
        ORDER BY p.paper_index
    """, (template,))
    return [
        {
            "paper_id": row["paper_id"],
            "title": row["title"] or "",
            "source_hash": row["source_hash"] or "",
            "has_source_md": bool(row["source_md_content_hash"]),
            "source_md_content_hash": row["source_md_content_hash"] or "",
        }
        for row in cursor.fetchall()
    ]


def _get_missing_translation(conn: sqlite3.Connection, lang: str) -> list[dict[str, Any]]:
    """Get papers missing specified translation."""
    cursor = conn.execute("""
        SELECT p.paper_id, p.title, p.source_hash
        FROM paper p
        WHERE p.paper_id NOT IN (
            SELECT paper_id FROM paper_translation WHERE lang = ?
        )
        ORDER BY p.paper_index
    """, (lang,))
    return [
        {
            "paper_id": row["paper_id"],
            "title": row["title"] or "",
            "source_hash": row["source_hash"] or "",
        }
        for row in cursor.fetchall()
    ]
