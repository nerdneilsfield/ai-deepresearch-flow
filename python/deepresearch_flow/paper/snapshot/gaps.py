"""Identify gaps in snapshot - papers missing specified templates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class GapIdentifyOptions:
    snapshot_db: Path
    template: str
    output_json: Path | None = None
    output_txt: Path | None = None


@dataclass
class GapCounts:
    total: int = 0
    has_template: int = 0
    missing_template: int = 0
    missing_but_has_source_md: int = 0
    missing_no_source_md: int = 0


def identify_gaps(opts: GapIdentifyOptions) -> None:
    """Identify papers missing specified template summary."""
    conn = sqlite3.connect(opts.snapshot_db)
    conn.row_factory = sqlite3.Row

    counts = GapCounts()
    missing_papers: list[dict[str, Any]] = []

    try:
        # Get total papers
        cursor = conn.execute("SELECT COUNT(*) as total FROM paper")
        counts.total = cursor.fetchone()["total"]

        # Find papers missing the template
        cursor = conn.execute(
            """
            SELECT 
                p.paper_id,
                p.title,
                p.source_md_content_hash,
                CASE WHEN ps.paper_id IS NOT NULL THEN 1 ELSE 0 END as has_template
            FROM paper p
            LEFT JOIN paper_summary ps 
                ON p.paper_id = ps.paper_id AND ps.template_tag = ?
            ORDER BY p.paper_index
            """,
            (opts.template,),
        )

        for row in cursor.fetchall():
            if row["has_template"]:
                counts.has_template += 1
            else:
                counts.missing_template += 1
                has_source_md = bool(row["source_md_content_hash"])
                
                if has_source_md:
                    counts.missing_but_has_source_md += 1
                else:
                    counts.missing_no_source_md += 1

                missing_papers.append({
                    "paper_id": row["paper_id"],
                    "title": row["title"] or "",
                    "has_source_md": has_source_md,
                    "extractable": has_source_md,
                })

    finally:
        conn.close()

    # Print summary table
    summary_table = Table(
        title=f"Gap Analysis for Template: {opts.template}",
        header_style="bold cyan",
        title_style="bold magenta"
    )
    summary_table.add_column("Metric", style="cyan", no_wrap=True)
    summary_table.add_column("Count", style="white", overflow="fold")
    summary_table.add_row("Total Papers", str(counts.total))
    summary_table.add_row(f"Has '{opts.template}'", str(counts.has_template))
    summary_table.add_row(f"Missing '{opts.template}'", str(counts.missing_template))
    summary_table.add_row("  └─ Missing but extractable (has source MD)", str(counts.missing_but_has_source_md))
    summary_table.add_row("  └─ Missing not extractable (no source MD)", str(counts.missing_no_source_md))
    Console().print(summary_table)

    # Show examples of missing papers
    if missing_papers:
        examples_table = Table(
            title=f"Missing Template Examples (showing up to 10 of {len(missing_papers)})",
            header_style="bold yellow",
            title_style="bold magenta"
        )
        examples_table.add_column("Paper ID", style="yellow", no_wrap=True)
        examples_table.add_column("Title", style="white", overflow="fold")
        examples_table.add_column("Extractable", style="green" if True else "red")
        
        for paper in missing_papers[:10]:
            title = paper["title"][:50] + "..." if len(paper["title"]) > 50 else paper["title"]
            extractable = "✓ Yes" if paper["extractable"] else "✗ No"
            examples_table.add_row(paper["paper_id"], title or "(No title)", extractable)
        Console().print(examples_table)

    # Export to JSON if requested
    if opts.output_json and missing_papers:
        output_data = {
            "template": opts.template,
            "summary": {
                "total_papers": counts.total,
                "has_template": counts.has_template,
                "missing_template": counts.missing_template,
                "missing_extractable": counts.missing_but_has_source_md,
                "missing_not_extractable": counts.missing_no_source_md,
            },
            "missing_papers": missing_papers,
            "extractable_paper_ids": [p["paper_id"] for p in missing_papers if p["extractable"]],
        }
        opts.output_json.parent.mkdir(parents=True, exist_ok=True)
        opts.output_json.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
        Console().print(f"[green]Exported detailed gap analysis to: {opts.output_json}[/green]")

    # Export to TXT (paper IDs only, for input-list) if requested
    if opts.output_txt and missing_papers:
        extractable_ids = [p["paper_id"] for p in missing_papers if p["extractable"]]
        if extractable_ids:
            opts.output_txt.parent.mkdir(parents=True, exist_ok=True)
            opts.output_txt.write_text("\n".join(extractable_ids), encoding="utf-8")
            Console().print(f"[green]Exported {len(extractable_ids)} extractable paper IDs to: {opts.output_txt}[/green]")
        else:
            Console().print("[yellow]No extractable papers found, TXT file not created[/yellow]")

    # Print usage hint
    if counts.missing_but_has_source_md > 0:
        Console().print()
        Console().print("[bold cyan]Next steps:[/bold cyan]")
        Console().print(f"1. Export the list: --txt-output missing_papers.txt")
        Console().print(f"2. Run extraction with --input-list:")
        Console().print(f"   paper extract --input ./md --prompt-template {opts.template} --input-list missing_papers.txt")
        Console().print(f"3. Merge results and rebuild snapshot")
