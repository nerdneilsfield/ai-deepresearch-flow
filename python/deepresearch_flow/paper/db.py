"""Database management commands for paper extraction outputs."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable
import difflib

from tqdm import tqdm

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deepresearch_flow.paper.config import load_config
from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.routing import (
    RoutePool,
    parse_model_selector,
    provider_window_error_as_click,
    resolve_model_capability,
)
from deepresearch_flow.paper.schema import SchemaError, load_schema
from deepresearch_flow.paper.template_registry import (
    get_stage_definitions,
    list_template_names,
    load_schema_for_template,
)
from deepresearch_flow.paper.render import resolve_render_template, render_papers

try:
    from pybtex.database import BibliographyData, parse_file
    from pybtex.database.output.bibtex import Writer

    PYBTEX_AVAILABLE = True
except ImportError:
    PYBTEX_AVAILABLE = False


def load_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("papers"), list):
        return data["papers"]
    if isinstance(data, list):
        return data
    raise click.ClickException("Input JSON must be a list or {template_tag, papers}")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _slice_by_index_range[T](items: list[T], start_idx: int, end_idx: int) -> list[T]:
    slice_end = end_idx if end_idx != -1 else None
    return items[start_idx:slice_end]


def _load_api_push_retry_report(path: Path) -> tuple[str, list[Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in retry report {path}: {exc}") from exc

    if not isinstance(data, list):
        raise click.ClickException("Retry report must be a JSON list")
    if not data:
        return "empty", []
    if all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in data):
        return "static", [str(entry["path"]) for entry in data]
    if all(isinstance(entry, dict) and isinstance(entry.get("request"), dict) for entry in data):
        return "semantic", [entry["request"] for entry in data]
    raise click.ClickException(
        "Unsupported retry report format. Expected static entries with 'path' or semantic entries with 'request'."
    )


def _semantic_group_key(chunk: dict[str, Any]) -> tuple[str, str]:
    return str(chunk.get("doc_id") or ""), str(chunk.get("template_tag") or "")


def _sort_semantic_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        chunks,
        key=lambda chunk: (
            str(chunk.get("doc_id") or ""),
            str(chunk.get("template_tag") or ""),
            str(chunk.get("chunk_type") or ""),
            int(chunk.get("chunk_index") or 0),
            str(chunk.get("id") or ""),
        ),
    )


def _expand_semantic_chunk_window(
    chunks: list[dict[str, Any]],
    *,
    start_chunk_idx: int,
    end_chunk_idx: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str]]]:
    ordered_chunks = _sort_semantic_chunks(chunks)
    selected_window = _slice_by_index_range(ordered_chunks, start_chunk_idx, end_chunk_idx)
    selected_groups = {_semantic_group_key(chunk) for chunk in selected_window}
    expanded_chunks = [
        chunk for chunk in ordered_chunks if _semantic_group_key(chunk) in selected_groups
    ]
    return ordered_chunks, expanded_chunks, selected_groups


def load_json_payload(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {path}: {exc}") from exc

    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        papers = data.get("papers")
        if isinstance(papers, list):
            return papers, data
        raise click.ClickException(f"JSON object missing 'papers' list: {path}")

    raise click.ClickException(f"Unsupported JSON structure in {path}")


def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list) or isinstance(value, dict):
        return len(value) == 0
    return False


def export_compare_csv(results: list[Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import csv

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Side",
                "Source Hash",
                "Title",
                "Match Status",
                "Match Type",
                "Match Score",
                "Source Path",
                "Other Source Hash",
                "Other Title",
                "Other Source Path",
                "Lang",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.side,
                    result.source_hash,
                    result.title,
                    result.match_status,
                    result.match_type or "",
                    f"{result.match_score:.4f}",
                    result.source_path or "",
                    result.other_source_hash or "",
                    result.other_title or "",
                    result.other_source_path or "",
                    result.lang or "",
                ]
            )


def export_only_in_b_paths(results: list[Any], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for result in results:
        if result.side != "B" or result.match_status != "only_in_B":
            continue
        if result.source_path:
            lines.append(result.source_path)

    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def normalize_authors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def parse_publication_year(paper: dict[str, Any]) -> int | None:
    if "bibtex" in paper and isinstance(paper["bibtex"], dict):
        year_str = paper["bibtex"].get("fields", {}).get("year")
        if year_str and str(year_str).isdigit():
            return int(year_str)
    date_str = paper.get("publication_date") or paper.get("paper_publication_date")
    if not date_str:
        return None
    match = re.search(r"(19|20)\d{2}", str(date_str))
    return int(match.group(0)) if match else None


MONTH_NAMES = [f"{idx:02d}" for idx in range(1, 13)]
MONTH_LOOKUP = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "sept": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


def normalize_month(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 1 <= value <= 12:
            return f"{value:02d}"
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return normalize_month(int(raw))
    if raw in MONTH_LOOKUP:
        return MONTH_LOOKUP[raw]
    return None


def parse_year_month(date_str: str | None) -> tuple[str | None, str | None]:
    if not date_str:
        return None, None
    text = str(date_str).strip()
    year_match = re.search(r"(19|20)\d{2}", text)
    year = year_match.group(0) if year_match else None

    numeric_match = re.search(r"(19|20)\d{2}[-/](\d{1,2})", text)
    if numeric_match:
        month = normalize_month(int(numeric_match.group(2)))
        return year, month

    month_word = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|november|december)",
        text.lower(),
    )
    if month_word:
        return year, normalize_month(month_word.group(0))

    return year, None


def resolve_relative_path(path: Path, roots: Iterable[Path]) -> Path:
    resolved = path.resolve()
    roots_by_depth = sorted(roots, key=lambda r: len(str(r.resolve())), reverse=True)
    for root in roots_by_depth:
        root_resolved = root.resolve()
        try:
            return resolved.relative_to(root_resolved)
        except ValueError:
            continue
    return Path(path.name)


def clean_journal_name(name: str | None) -> str:
    if not name:
        return "Unknown"
    value = re.sub(r"\([^)]*\)", "", name)
    value = re.sub(r"vol\.\s*\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"volume\s*\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"no\.\s*\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"number\s*\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"pp\.\s*\d+[-–]\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"pages\s*\d+[-–]\d+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(19|20)\d{2}\b", "", value)
    value = re.sub(r"[,:.;]+\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.replace("{", "").replace("}", "")
    return value if value else "Unknown"


def clean_conference_name(name: str | None) -> str:
    if not name:
        return "Unknown"
    value = re.sub(r"\b(19|20)\d{2}\b", "", name)
    value = re.sub(r"\b\d+(st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"proceedings\s+of\s+the\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"proceedings\s+of\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[,:.;]+\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.replace("{", "").replace("}", "")
    return value if value else "Unknown"


def classify_venue(name: str | None) -> str:
    if not name:
        return "unknown"
    lowered = name.lower()
    if any(keyword in lowered for keyword in ["journal", "transactions", "letters", "review"]):
        return "journal"
    if any(
        keyword in lowered
        for keyword in ["conference", "proceedings", "symposium", "workshop", "meeting"]
    ):
        return "conference"
    return "other"


def format_distribution(count: int, max_count: int, width: int = 20) -> str:
    if max_count <= 0:
        return ""
    filled = max(1, int(round(width * (count / max_count)))) if count else 0
    return "#" * filled


def similar_title(a: str, b: str, threshold: float = 0.9) -> bool:
    if not a or not b:
        return False
    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio >= threshold


async def generate_tags_for_paper(
    client: httpx.AsyncClient,
    provider,
    model: str,
    api_key: str | None,
    paper: dict[str, Any],
    max_retries: int,
    backoff_base: float,
    backoff_max: float,
    route_pool: RoutePool | None = None,
) -> list[str]:
    system_prompt = (
        "You are a scientific paper tagging assistant. "
        "Return ONLY a JSON array of up to 5 tags. "
        "Each tag should be 1-3 words, lowercase, and use underscores."
    )
    payload = {
        "title": paper.get("paper_title"),
        "authors": normalize_authors(paper.get("paper_authors")),
        "abstract": paper.get("abstract") or paper.get("summary") or "",
        "keywords": paper.get("keywords") or [],
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            current_provider = provider
            current_model = model
            current_api_key = api_key
            if route_pool is not None:
                route = await route_pool.get()
                current_provider = route.provider
                current_model = route.model.model_name
                current_api_key = route.key.value
            response_text = await call_provider(
                current_provider,
                current_model,
                messages,
                schema={},
                api_key=current_api_key,
                timeout=60.0,
                structured_mode="none",
                client=client,
            )
            tags = parse_tag_list(response_text)
            if isinstance(tags, list):
                return [str(tag) for tag in tags][:5]
            raise ProviderError("Tag response is not a list", error_type="validation_error")
        except ProviderError as exc:
            if route_pool is not None:
                quota_hit = await route_pool.mark_quota_exceeded(route, str(exc), exc.status_code)
                if quota_hit:
                    continue
                if exc.retryable:
                    await route_pool.mark_error(route)
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base, attempt, backoff_max))
                continue
            raise
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base, attempt, backoff_max))
                continue
            raise ProviderError(str(exc), error_type="parse_error") from exc

    raise ProviderError("Max retries exceeded")


def parse_tag_list(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\\[[\\s\\S]*\\]", text)
        if not match:
            raise ProviderError("No JSON array found", error_type="parse_error")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ProviderError("Tag response is not a list", error_type="validation_error")
    return [str(item) for item in parsed]


def load_api_pipeline_config(
    config_path: str | Path,
    admin_token: str | None,
) -> Any | None:
    """Load enabled pipeline settings for API startup, or return ``None``.

    This small public seam keeps deployment startup policy testable without
    coupling tests to Starlette construction.  Disabled/missing sections keep
    legacy API startup unchanged.
    """

    from deepresearch_flow.pipeline.config import load_pipeline_config

    path = Path(config_path)
    if not path.is_file():
        return None
    parsed = load_pipeline_config(path)
    if not parsed.enabled:
        return None
    if not str(admin_token or "").strip():
        raise ValueError("PAPER_DB_ADMIN_TOKEN is required when [pipeline].enabled is true")
    return parsed


def register_db_commands(db_group: click.Group) -> None:
    @db_group.group("snapshot")
    def snapshot_group() -> None:
        """Build production snapshot artifacts (SQLite + static export)."""

    @snapshot_group.command("build")
    @click.option(
        "-i", "--input", "input_paths", multiple=True, required=True, help="Input JSON file path"
    )
    @click.option(
        "-c", "--config", "config_path", default="config.toml", help="Path to config.toml"
    )
    @click.option("-b", "--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        default=(),
        help="Optional markdown root directory (repeatable) for source viewing",
    )
    @click.option(
        "--md-translated-root",
        "md_translated_roots",
        multiple=True,
        default=(),
        help="Optional markdown root directory (repeatable) for translated viewing",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        default=(),
        help="Optional PDF root directory (repeatable) for PDF discovery",
    )
    @click.option(
        "--output-db",
        "output_db",
        default="paper_snapshot.db",
        show_default=True,
        help="Output DB path",
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default="paper-static",
        show_default=True,
        help="Output directory for hashed static assets",
    )
    @click.option(
        "--previous-snapshot-db",
        "previous_snapshot_db",
        default=None,
        help="Optional previous snapshot DB path for identity continuity",
    )
    @click.option(
        "--output-embed-db",
        "output_embed_db",
        default=None,
        help="Build LanceDB vector index alongside snapshot",
    )
    @click.option("-v", "--verbose", is_flag=True, help="Show detailed snapshot build logs")
    def snapshot_build(
        input_paths: tuple[str, ...],
        config_path: str,
        bibtex_path: str | None,
        md_roots: tuple[str, ...],
        md_translated_roots: tuple[str, ...],
        pdf_roots: tuple[str, ...],
        output_db: str,
        static_export_dir: str,
        previous_snapshot_db: str | None,
        output_embed_db: str | None,
        verbose: bool,
    ) -> None:
        """Build a production snapshot (SQLite + static export)."""
        from deepresearch_flow.paper.snapshot.builder import SnapshotBuildOptions, build_snapshot

        opts = SnapshotBuildOptions(
            input_paths=[Path(path) for path in input_paths],
            bibtex_path=Path(bibtex_path) if bibtex_path else None,
            md_roots=[Path(root) for root in md_roots],
            md_translated_roots=[Path(root) for root in md_translated_roots],
            pdf_roots=[Path(root) for root in pdf_roots],
            output_db=Path(output_db),
            static_export_dir=Path(static_export_dir),
            previous_snapshot_db=Path(previous_snapshot_db) if previous_snapshot_db else None,
            verbose=verbose,
        )
        build_snapshot(opts)
        click.echo(f"Wrote snapshot DB: {opts.output_db}")
        click.echo(f"Wrote static export: {opts.static_export_dir}")
        if output_embed_db:
            from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

            click.echo(f"Building vector index at {output_embed_db}...")
            config = load_config(config_path)
            asyncio.run(
                run_embed_pipeline(
                    config=config,
                    snapshot_db=opts.output_db,
                    static_export_dir=opts.static_export_dir,
                    vector_dir=Path(output_embed_db),
                )
            )

    @snapshot_group.group("unpack")
    def snapshot_unpack_group() -> None:
        """Unpack snapshot artifacts."""

    @snapshot_unpack_group.command("md")
    @click.option("--snapshot-db", "snapshot_db", required=True, help="Path to snapshot database")
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        required=True,
        help="Path to static export directory",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        required=True,
        help="PDF root directories for name alignment (repeatable)",
    )
    @click.option(
        "--md-output-dir", "md_output_dir", required=True, help="Output directory for Markdown"
    )
    @click.option(
        "--md-translated-output-dir",
        "md_translated_output_dir",
        required=True,
        help="Output directory for translated Markdown",
    )
    @click.option("--dry-run", is_flag=True, help="Show what would be done without writing files")
    @click.option("--log", "log_json", default=None, help="Path to export error log JSON")
    def snapshot_unpack_md(
        snapshot_db: str,
        static_export_dir: str,
        pdf_roots: tuple[str, ...],
        md_output_dir: str,
        md_translated_output_dir: str,
        dry_run: bool,
        log_json: str | None,
    ) -> None:
        """Unpack source/translated markdown and align filenames to PDFs."""
        from deepresearch_flow.paper.snapshot.unpacker import SnapshotUnpackMdOptions, unpack_md

        opts = SnapshotUnpackMdOptions(
            snapshot_db=Path(snapshot_db),
            static_export_dir=Path(static_export_dir),
            pdf_roots=[Path(path) for path in pdf_roots],
            md_output_dir=Path(md_output_dir),
            md_translated_output_dir=Path(md_translated_output_dir),
            dry_run=dry_run,
            log_json=Path(log_json) if log_json else None,
        )
        unpack_md(opts)

    @snapshot_unpack_group.command("info")
    @click.option("--snapshot-db", "snapshot_db", required=True, help="Path to snapshot database")
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        required=True,
        help="Path to static export directory",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        required=True,
        help="PDF root directories for name alignment (repeatable)",
    )
    @click.option("--template", "template", required=True, help="Summary template tag")
    @click.option(
        "--strict-template",
        "strict_template",
        is_flag=True,
        help="Only export papers that have the requested template summary (no fallback)",
    )
    @click.option("--output-json", "output_json", required=True, help="Output JSON file path")
    @click.option("--log", "log_json", default=None, help="Path to export error log JSON")
    def snapshot_unpack_info(
        snapshot_db: str,
        static_export_dir: str,
        pdf_roots: tuple[str, ...],
        template: str,
        strict_template: bool,
        output_json: str,
        log_json: str | None,
    ) -> None:
        """Unpack aggregated paper_infos.json from snapshot summaries."""
        from deepresearch_flow.paper.snapshot.unpacker import SnapshotUnpackInfoOptions, unpack_info

        opts = SnapshotUnpackInfoOptions(
            snapshot_db=Path(snapshot_db),
            static_export_dir=Path(static_export_dir),
            pdf_roots=[Path(path) for path in pdf_roots],
            template=template,
            strict_template=strict_template,
            output_json=Path(output_json),
            log_json=Path(log_json) if log_json else None,
        )
        unpack_info(opts)

    @snapshot_group.command("show-missing")
    @click.option("--snapshot-db", "snapshot_db", required=True, help="Path to snapshot database")
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default=None,
        help="Path to static export directory (optional, for checking file existence)",
    )
    def snapshot_show_missing(
        snapshot_db: str,
        static_export_dir: str | None,
    ) -> None:
        """Show comprehensive report of missing artifacts."""
        from deepresearch_flow.paper.snapshot.missing import ShowMissingOptions, show_missing

        opts = ShowMissingOptions(
            snapshot_db=Path(snapshot_db),
            static_export_dir=Path(static_export_dir) if static_export_dir else None,
        )
        show_missing(opts)

    @snapshot_group.command("export-missing")
    @click.option("--snapshot-db", "snapshot_db", required=True, help="Path to snapshot database")
    @click.option(
        "--type",
        "missing_type",
        type=click.Choice(["source_md", "pdf", "template", "translation"]),
        required=True,
        help="Type of missing artifact to export",
    )
    @click.option(
        "--template", "template", default=None, help="Template name (required for --type=template)"
    )
    @click.option(
        "--lang", "lang", default=None, help="Language code (required for --type=translation)"
    )
    @click.option("--output", "output_json", default=None, help="Output JSON file path")
    @click.option(
        "--txt-output", "output_txt", default=None, help="Output TXT file path (paper IDs only)"
    )
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        default=[],
        help="Markdown root directories for resolving source paths (repeatable)",
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default=None,
        help="Static export directory for resolving md/{source_md_content_hash}.md paths",
    )
    @click.option(
        "--output-paths",
        "output_paths",
        default=None,
        help="Output markdown paths for extraction (use with --input-list)",
    )
    def snapshot_export_missing(
        snapshot_db: str,
        missing_type: str,
        template: str | None,
        lang: str | None,
        output_json: str | None,
        output_txt: str | None,
        md_roots: tuple[str, ...],
        static_export_dir: str | None,
        output_paths: str | None,
    ) -> None:
        """Export list of papers missing specified artifacts."""
        from deepresearch_flow.paper.snapshot.missing import ExportMissingOptions, export_missing

        opts = ExportMissingOptions(
            snapshot_db=Path(snapshot_db),
            missing_type=missing_type,
            template=template,
            lang=lang,
            output_json=Path(output_json) if output_json else None,
            output_txt=Path(output_txt) if output_txt else None,
            output_paths=Path(output_paths) if output_paths else None,
            md_roots=[Path(root) for root in md_roots] if md_roots else None,
            static_export_dir=Path(static_export_dir) if static_export_dir else None,
        )
        export_missing(opts)

    @snapshot_group.command("update")
    @click.option(
        "--snapshot-db",
        "snapshot_db",
        required=True,
        help="Path to existing snapshot database",
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        required=True,
        help="Path to static export directory",
    )
    @click.option(
        "-i",
        "--input",
        "input_paths",
        multiple=True,
        required=True,
        help="Input JSON file path (repeatable)",
    )
    @click.option("-b", "--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        default=[],
        help="Markdown root directories (repeatable)",
    )
    @click.option(
        "--md-translated-root",
        "md_translated_roots",
        multiple=True,
        default=[],
        help="Translated markdown directories (repeatable)",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        default=[],
        help="PDF root directories (repeatable)",
    )
    @click.option(
        "--in-place", is_flag=True, help="Modify existing snapshot in place (creates backup)"
    )
    @click.option(
        "--output-db", "output_db", default=None, help="Output database path (if not --in-place)"
    )
    @click.option(
        "--output-static-dir",
        "output_static_dir",
        default=None,
        help="Output static directory (if not --in-place)",
    )
    def snapshot_update(
        snapshot_db: str,
        static_export_dir: str,
        input_paths: tuple[str, ...],
        bibtex_path: str | None,
        md_roots: tuple[str, ...],
        md_translated_roots: tuple[str, ...],
        pdf_roots: tuple[str, ...],
        in_place: bool,
        output_db: str | None,
        output_static_dir: str | None,
    ) -> None:
        """Add new papers to existing snapshot."""
        from deepresearch_flow.paper.snapshot.update import SnapshotUpdateOptions, update_snapshot

        if not in_place and not output_db:
            raise click.ClickException("Must specify either --in-place or --output-db")

        opts = SnapshotUpdateOptions(
            snapshot_db=Path(snapshot_db),
            static_export_dir=Path(static_export_dir),
            input_paths=[Path(p) for p in input_paths],
            bibtex_path=Path(bibtex_path) if bibtex_path else None,
            md_roots=[Path(r) for r in md_roots] if md_roots else None,
            md_translated_roots=[Path(r) for r in md_translated_roots]
            if md_translated_roots
            else None,
            pdf_roots=[Path(r) for r in pdf_roots] if pdf_roots else None,
            in_place=in_place,
            output_db=Path(output_db) if output_db else None,
            output_static_dir=Path(output_static_dir) if output_static_dir else None,
        )
        update_snapshot(opts)

    @snapshot_group.command("supplement")
    @click.option(
        "--snapshot-db",
        "snapshot_db",
        required=True,
        help="Path to existing snapshot database",
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        required=True,
        help="Path to static export directory",
    )
    @click.option(
        "-i",
        "--input",
        "input_paths",
        multiple=True,
        required=True,
        help="Input JSON file path (repeatable)",
    )
    @click.option("-b", "--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        default=[],
        help="Markdown root directories (repeatable)",
    )
    @click.option(
        "--md-translated-root",
        "md_translated_roots",
        multiple=True,
        default=[],
        help="Translated markdown directories (repeatable)",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        default=[],
        help="PDF root directories (repeatable)",
    )
    @click.option(
        "--in-place", is_flag=True, help="Modify existing snapshot in place (creates backup)"
    )
    @click.option(
        "--output-db", "output_db", default=None, help="Output database path (if not --in-place)"
    )
    @click.option(
        "--output-static-dir",
        "output_static_dir",
        default=None,
        help="Output static directory (if not --in-place)",
    )
    def snapshot_supplement(
        snapshot_db: str,
        static_export_dir: str,
        input_paths: tuple[str, ...],
        bibtex_path: str | None,
        md_roots: tuple[str, ...],
        md_translated_roots: tuple[str, ...],
        pdf_roots: tuple[str, ...],
        in_place: bool,
        output_db: str | None,
        output_static_dir: str | None,
    ) -> None:
        """Supplement existing snapshot with missing templates/translations for existing papers."""
        from deepresearch_flow.paper.snapshot.supplement import (
            SnapshotSupplementOptions,
            supplement_snapshot,
        )

        if not in_place and not output_db:
            raise click.ClickException("Must specify either --in-place or --output-db")

        opts = SnapshotSupplementOptions(
            snapshot_db=Path(snapshot_db),
            static_export_dir=Path(static_export_dir),
            input_paths=[Path(p) for p in input_paths],
            bibtex_path=Path(bibtex_path) if bibtex_path else None,
            md_roots=[Path(r) for r in md_roots] if md_roots else None,
            md_translated_roots=[Path(r) for r in md_translated_roots]
            if md_translated_roots
            else None,
            pdf_roots=[Path(r) for r in pdf_roots] if pdf_roots else None,
            in_place=in_place,
            output_db=Path(output_db) if output_db else None,
            output_static_dir=Path(output_static_dir) if output_static_dir else None,
        )
        supplement_snapshot(opts)

    @snapshot_group.command("migrate")
    @click.option(
        "--snapshot-db", "snapshot_db", required=True, help="Path to existing snapshot database"
    )
    @click.option("--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default=None,
        help="Optional static export directory to update",
    )
    @click.option(
        "--in-place", is_flag=True, help="Modify database in place (creates timestamped backup)"
    )
    @click.option(
        "--output-db", "output_db", default=None, help="Output database path (if not --in-place)"
    )
    @click.option(
        "--backup/--no-backup",
        default=True,
        help="Create backup when using --in-place (default: yes)",
    )
    def snapshot_migrate(
        snapshot_db: str,
        bibtex_path: str | None,
        static_export_dir: str | None,
        in_place: bool,
        output_db: str | None,
        backup: bool,
    ) -> None:
        """Migrate legacy snapshot database schema and enrich with DOI/BibTeX data."""
        import sqlite3
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from deepresearch_flow.paper.snapshot.migrate import (
            create_timestamped_backup,
            enrich_db_with_bibtex,
            migrate_schema,
            update_static_export_index,
        )

        console = Console()

        if not in_place and not output_db:
            raise click.ClickException("Must specify either --in-place or --output-db")

        if in_place and output_db:
            raise click.ClickException("Cannot use both --in-place and --output-db")

        snapshot_path = Path(snapshot_db)
        if not snapshot_path.exists():
            raise click.ClickException(f"Snapshot database not found: {snapshot_path}")

        console.print(f"\n[bold cyan]Snapshot Migration[/bold cyan]", style="bold")
        console.print(f"Source: [yellow]{snapshot_path}[/yellow]")

        # Determine output path
        if in_place:
            # Create backup first
            if backup:
                backup_path = create_timestamped_backup(snapshot_path)
                console.print(f"Backup: [green]{backup_path.name}[/green]")
            else:
                if not click.confirm(
                    "Are you sure? This will modify the database without backup.",
                    default=False,
                ):
                    console.print("[red]Aborted.[/red]")
                    return
            target_path = snapshot_path
            console.print(f"Mode: [magenta]In-place migration[/magenta]")
        else:
            target_path = Path(output_db)
            if target_path.exists():
                if not click.confirm(
                    f"Output database {target_path} exists. Overwrite?", default=False
                ):
                    console.print("[red]Aborted.[/red]")
                    return
            # Copy to output location
            shutil.copy2(snapshot_path, target_path)
            console.print(f"Mode: [magenta]Copy to new database[/magenta]")
            console.print(f"Output: [yellow]{target_path}[/yellow]")

        console.print()

        # Open database and migrate
        conn = sqlite3.connect(str(target_path))
        conn.row_factory = sqlite3.Row

        try:
            # Migrate schema
            console.print("[bold]1. Schema Migration[/bold]")
            schema_changed, schema_rows = migrate_schema(conn)

            schema_table = Table(show_header=True, header_style="bold magenta", box=None)
            schema_table.add_column("Component", style="cyan")
            schema_table.add_column("Action", style="yellow")
            schema_table.add_column("Status", justify="center")
            for component, action, status in schema_rows:
                schema_table.add_row(component, action, status)
            console.print(schema_table)
            console.print()

            # Enrich with BibTeX if provided
            matched = 0
            doi_count = 0
            total = 0
            if bibtex_path:
                bibtex_file = Path(bibtex_path)
                if not bibtex_file.exists():
                    raise click.ClickException(f"BibTeX file not found: {bibtex_file}")

                console.print(
                    f"[bold]2. BibTeX Enrichment[/bold] from [yellow]{bibtex_file.name}[/yellow]"
                )
                matched, doi_count, total = enrich_db_with_bibtex(conn, bibtex_file)

                bibtex_table = Table(show_header=True, header_style="bold magenta", box=None)
                bibtex_table.add_column("Metric", style="cyan")
                bibtex_table.add_column("Count", justify="right", style="green")
                bibtex_table.add_column("Percentage", justify="right")
                bibtex_table.add_row("Total papers", str(total), "")
                bibtex_table.add_row(
                    "BibTeX matched",
                    str(matched),
                    f"{matched / total * 100:.1f}%" if total else "0%",
                )
                bibtex_table.add_row(
                    "DOI extracted",
                    str(doi_count),
                    f"{doi_count / total * 100:.1f}%" if total else "0%",
                )
                console.print(bibtex_table)
                console.print()
            else:
                console.print(
                    "[yellow]2. BibTeX Enrichment[/yellow]: Skipped (no BibTeX file provided)"
                )
                console.print()

            # Commit changes
            conn.commit()

            # Update static export if provided
            if static_export_dir:
                static_path = Path(static_export_dir)
                if static_path.exists():
                    console.print(f"[bold]3. Static Export Update[/bold]")
                    updated_count = update_static_export_index(static_path, conn)
                    if updated_count > 0:
                        console.print(
                            f"  [green]✓[/green] Updated {updated_count} papers in paper_index.json"
                        )
                    else:
                        console.print(
                            f"  [yellow]○[/yellow] paper_index.json not found or no updates needed"
                        )
                    console.print()

            console.print(
                Panel(
                    f"[bold green]Migration Complete![/bold green]\n\n"
                    f"Database: [yellow]{target_path}[/yellow]",
                    border_style="green",
                    padding=(1, 2),
                )
            )

        except Exception as e:
            conn.rollback()
            raise click.ClickException(f"Migration failed: {e}")
        finally:
            conn.close()

    @db_group.group("api")
    def api_group() -> None:
        """Read-only JSON API server backed by a snapshot DB."""

    @api_group.command("serve")
    @click.option("--snapshot-db", "snapshot_db", required=True, help="Path to paper_snapshot.db")
    @click.option(
        "--static-base-url",
        "static_base_url",
        default=None,
        help="Static asset base URL (e.g. https://static.example.com)",
    )
    @click.option(
        "--cors-origin",
        "cors_origins",
        multiple=True,
        default=(),
        help="Allowed CORS origin (repeatable; default is '*')",
    )
    @click.option(
        "--max-query-length", "max_query_length", type=int, default=500, show_default=True
    )
    @click.option("--max-page-size", "max_page_size", type=int, default=100, show_default=True)
    @click.option(
        "--max-pagination-offset",
        "max_pagination_offset",
        type=int,
        default=10000,
        show_default=True,
    )
    @click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
    @click.option("--port", default=8001, type=int, show_default=True, help="Bind port")
    @click.option(
        "--embed-db", "embed_db", default=None, help="LanceDB directory for semantic search"
    )
    @click.option(
        "--search-access-token",
        "search_access_token_cli",
        default=None,
        envvar="SEARCH_ACCESS_TOKEN",
        help="Token to gate advanced search",
    )
    @click.option(
        "--search-auth-mode",
        "search_auth_mode",
        type=click.Choice(["static", "github-oauth", "both"]),
        default="static",
        show_default=True,
        envvar="SEARCH_AUTH_MODE",
        help="Advanced search auth mode: static bearer, GitHub OAuth, or both.",
    )
    @click.option(
        "-c", "--config", "config_path", default="config.toml", help="Path to config.toml"
    )
    @click.option(
        "--admin-token",
        "admin_token",
        default=None,
        envvar="PAPER_DB_ADMIN_TOKEN",
        help="Bearer token for /api/v1/admin endpoints (or set PAPER_DB_ADMIN_TOKEN)",
    )
    @click.option(
        "--mcp-access-token",
        "mcp_access_token",
        default=None,
        envvar="MCP_ACCESS_TOKEN",
        help="Bearer token for /mcp and /mcp-sse endpoints (or set MCP_ACCESS_TOKEN)",
    )
    @click.option(
        "--mcp-auth-mode",
        "mcp_auth_mode",
        type=click.Choice(["static", "github-oauth"]),
        default="static",
        show_default=True,
        envvar="MCP_AUTH_MODE",
        help="MCP auth mode: static bearer or GitHub OAuth for /mcp.",
    )
    @click.option(
        "--mcp-public-base-url",
        "mcp_public_base_url",
        default=None,
        envvar="MCP_PUBLIC_BASE_URL",
        help="Public origin shared by MCP and search GitHub OAuth, without a path.",
    )
    @click.option(
        "--github-oauth-client-id",
        "github_oauth_client_id",
        default=None,
        envvar="GITHUB_OAUTH_CLIENT_ID",
        help="GitHub OAuth App client ID shared by MCP and search OAuth.",
    )
    @click.option(
        "--github-oauth-client-secret",
        "github_oauth_client_secret",
        default=None,
        envvar="GITHUB_OAUTH_CLIENT_SECRET",
        help="GitHub OAuth App client secret shared by MCP and search OAuth.",
    )
    @click.option(
        "--mcp-github-allowed-user-id",
        "mcp_github_allowed_user_ids",
        multiple=True,
        envvar="MCP_GITHUB_ALLOWED_USER_IDS",
        help="Allowed numeric GitHub user ID for MCP/search OAuth; repeat or use comma-separated env.",
    )
    @click.option(
        "--mcp-oauth-client-cache",
        "mcp_oauth_client_cache_path",
        default=None,
        envvar="MCP_OAUTH_CLIENT_CACHE",
        type=click.Path(path_type=str),
        help="JSON file path for persisted MCP OAuth dynamic client registrations.",
    )
    def api_serve(
        snapshot_db: str,
        static_base_url: str | None,
        cors_origins: tuple[str, ...],
        max_query_length: int,
        max_page_size: int,
        max_pagination_offset: int,
        host: str,
        port: int,
        embed_db: str | None,
        search_access_token_cli: str | None,
        search_auth_mode: str,
        config_path: str,
        admin_token: str | None,
        mcp_access_token: str | None,
        mcp_auth_mode: str,
        mcp_public_base_url: str | None,
        github_oauth_client_id: str | None,
        github_oauth_client_secret: str | None,
        mcp_github_allowed_user_ids: tuple[str, ...],
        mcp_oauth_client_cache_path: str | None,
    ) -> None:
        """Serve the snapshot-backed JSON API."""
        import os
        import uvicorn

        from deepresearch_flow.paper.snapshot.api import ApiLimits, create_app
        static_base_url_value = (
            static_base_url
            or os.getenv("PAPER_DB_STATIC_BASE")
            or os.getenv("PAPER_DB_STATIC_BASE_URL")
            or ""
        )
        api_base_url = os.getenv("PAPER_DB_API_BASE") or ""
        if api_base_url and host == "127.0.0.1" and port == 8001:
            from urllib.parse import urlparse

            parsed = urlparse(api_base_url)
            if not parsed.scheme:
                parsed = urlparse(f"http://{api_base_url}")
            if parsed.hostname:
                host = parsed.hostname
            if parsed.port:
                port = parsed.port
        cors_allowed = list(cors_origins) if cors_origins else ["*"]
        limits = ApiLimits(
            max_query_length=max_query_length,
            max_page_size=max_page_size,
            max_pagination_offset=max_pagination_offset,
        )
        advanced_ctx = None
        paper_config = load_config(config_path)
        try:
            pipeline_config = load_api_pipeline_config(config_path, admin_token)
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        startup_embed_db = None
        if embed_db:
            startup_embed_db = Path(embed_db)
        elif paper_config.search is not None and paper_config.search.vector_dir:
            startup_embed_db = Path(paper_config.search.vector_dir)
        if startup_embed_db is not None:
            from deepresearch_flow.paper.vector_store import ensure_admin_scalar_indices, open_store

            timeout_env = str(os.getenv("SEMANTIC_INDEX_BUILD_TIMEOUT") or "").strip()
            wait_timeout = timedelta(minutes=30)
            if timeout_env:
                try:
                    timeout_seconds = float(timeout_env)
                except ValueError as exc:
                    raise click.ClickException(
                        "SEMANTIC_INDEX_BUILD_TIMEOUT must be a positive number of seconds."
                    ) from exc
                if timeout_seconds <= 0:
                    raise click.ClickException(
                        "SEMANTIC_INDEX_BUILD_TIMEOUT must be a positive number of seconds."
                    )
                wait_timeout = timedelta(seconds=timeout_seconds)
            startup_lance_db = open_store(startup_embed_db)
            try:
                ensure_result = ensure_admin_scalar_indices(
                    startup_lance_db,
                    vector_dir=startup_embed_db,
                    wait_timeout=wait_timeout,
                )
            except TimeoutError as exc:
                raise click.ClickException(
                    "SEMANTIC_INDEX_BUILD_TIMEOUT while building semantic scalar indices for "
                    f"{startup_embed_db}. Increase SEMANTIC_INDEX_BUILD_TIMEOUT "
                    "(seconds) and retry."
                ) from exc
            if ensure_result.table_exists and ensure_result.created_names:
                click.echo(
                    f"[INFO] Ensured semantic scalar indices for {startup_embed_db}: "
                    + ", ".join(ensure_result.created_names),
                    err=True,
                )
        admin_embed_db = None
        admin_embed_dimensions = None
        if admin_token:
            if embed_db:
                admin_embed_db = Path(embed_db)
            elif paper_config.search is not None and paper_config.search.vector_dir:
                admin_embed_db = Path(paper_config.search.vector_dir)
            if admin_embed_db is not None and paper_config.embedding is not None:
                admin_embed_dimensions = paper_config.embedding.dimensions
        if paper_config.search is not None and paper_config.search.advanced_enabled:
            lance_dir = embed_db or paper_config.search.vector_dir
            if not lance_dir:
                raise click.ClickException(
                    "Advanced search requires LanceDB path via --embed-db or config.search.vector_dir"
                )
            if (
                embed_db
                and paper_config.search.vector_dir
                and str(Path(embed_db).resolve())
                != str(Path(paper_config.search.vector_dir).resolve())
            ):
                click.echo(
                    f"[WARN] --embed-db ({embed_db}) differs from config.search.vector_dir ({paper_config.search.vector_dir}); using CLI",
                    err=True,
                )

            token = search_access_token_cli or paper_config.search.access_token
            if search_auth_mode in {"static", "both"} and not token:
                raise click.ClickException(
                    "Advanced search requires a token via --search-access-token, "
                    "SEARCH_ACCESS_TOKEN, or config.search.access_token"
                )

            import lancedb

            from deepresearch_flow.paper.snapshot.advanced import AdvancedSearchContext
            from deepresearch_flow.paper.vector_store import validate_index_meta

            lance_path = Path(lance_dir)
            lance_db = lancedb.connect(str(lance_path))
            embedding_provider, embedding_model = paper_config.embedding.resolve_active()
            try:
                validate_index_meta(
                    lance_path,
                    model=embedding_model.model_name,
                    canonical_model=embedding_model.canonical_name,
                    dimensions=embedding_model.dimensions,
                    normalized=paper_config.embedding.normalized,
                    provider=embedding_provider.name,
                )
            except Exception as exc:
                raise click.ClickException(f"Advanced search INDEX_MISMATCH: {exc}") from exc

            embedding_pool = RoutePool.from_embedding_provider(paper_config.embedding)
            rerank_pool = None
            if paper_config.rerank is not None and paper_config.rerank.enabled:
                rerank_pool = RoutePool.from_rerank_provider(paper_config.rerank)

            advanced_ctx = AdvancedSearchContext(
                embed_db_path=lance_path,
                lance_db=lance_db,
                paper_config=paper_config,
                embedding_route_pool=embedding_pool,
                rerank_route_pool=rerank_pool,
                search_access_token=token,
                search_config=paper_config.search,
                auth_mode=search_auth_mode,
            )
        allowed_github_user_ids = [
            item.strip()
            for raw in mcp_github_allowed_user_ids
            for item in str(raw).split(",")
            if item.strip()
        ]
        app = create_app(
            snapshot_db=Path(snapshot_db),
            static_base_url=static_base_url_value,
            cors_allowed_origins=cors_allowed,
            limits=limits,
            mcp_access_token=mcp_access_token,
            mcp_auth_mode=mcp_auth_mode,
            mcp_public_base_url=mcp_public_base_url,
            github_oauth_client_id=github_oauth_client_id,
            github_oauth_client_secret=github_oauth_client_secret,
            mcp_github_allowed_user_ids=allowed_github_user_ids,
            mcp_oauth_client_cache_path=(
                Path(mcp_oauth_client_cache_path) if mcp_oauth_client_cache_path else None
            ),
            search_auth_mode=search_auth_mode,
            admin_token=admin_token,
            admin_embed_db=admin_embed_db,
            admin_embed_dimensions=admin_embed_dimensions,
            pipeline_config=pipeline_config,
            advanced_config=advanced_ctx,
        )
        click.echo(f"Serving API on http://{host}:{port} (Ctrl+C to stop)")
        uvicorn.run(app, host=host, port=port, log_level="info")


    @api_group.command("push")
    @click.option(
        "--snapshot-db", "snapshot_db", required=True, help="Path to local paper_snapshot.db"
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default=None,
        help="Path to local static export dir (for summary JSONs + remote storage push)",
    )
    @click.option(
        "--config",
        "config_path",
        default="remote.toml",
        show_default=True,
        help="Path to remote config TOML file",
    )
    @click.option(
        "--dry-run", is_flag=True, default=False, help="Extract and show stats without pushing"
    )
    @click.option(
        "--overwrite",
        is_flag=True,
        default=False,
        help="Replace matching remote papers and existing static files",
    )
    @click.option(
        "--retry-failed",
        "retry_failed_path",
        default=None,
        type=click.Path(exists=True),
        help="Path to push-static-errors.json to retry only failed static files",
    )
    @click.option(
        "--only-storage",
        is_flag=True,
        default=False,
        help="Push only static storage; skip admin API",
    )
    @click.option(
        "--only-api",
        is_flag=True,
        default=False,
        help="Push only admin API; skip static storage and semantic chunks",
    )
    @click.option(
        "--embed-db",
        "embed_db",
        default=None,
        type=click.Path(path_type=str),
        help="Path to local semantic LanceDB directory for optional vector sync",
    )
    @click.option(
        "--storage-concurrency",
        "storage_concurrency",
        type=int,
        default=4,
        show_default=True,
        help="Concurrent workers for static storage push",
    )
    @click.option(
        "--start-idx",
        "start_idx",
        type=int,
        default=0,
        show_default=True,
        help="0-based start index for papers",
    )
    @click.option(
        "--end-idx",
        "end_idx",
        type=int,
        default=-1,
        show_default=True,
        help="0-based exclusive end index for papers (-1 = all)",
    )
    def api_push(
        snapshot_db: str,
        static_export_dir: str | None,
        config_path: str,
        dry_run: bool,
        overwrite: bool,
        retry_failed_path: str | None,
        only_storage: bool,
        only_api: bool,
        storage_concurrency: int,
        embed_db: str | None,
        start_idx: int,
        end_idx: int,
    ) -> None:
        """Push papers from a local snapshot DB to a remote admin API."""
        from rich.console import Console
        from rich.table import Table

        from deepresearch_flow.paper.snapshot.push import (
            PushStats,
            extract_papers_from_db,
            load_remote_config,
            push_papers,
        )

        console = Console()

        config_file = Path(config_path)
        if not config_file.exists():
            raise click.ClickException(f"Config file not found: {config_file}")

        db_path = Path(snapshot_db)
        if not db_path.exists():
            raise click.ClickException(f"Snapshot DB not found: {db_path}")

        static_dir = Path(static_export_dir) if static_export_dir else None
        if static_dir and not static_dir.exists():
            raise click.ClickException(f"Static export dir not found: {static_dir}")

        if only_api and only_storage:
            raise click.ClickException("--only-api and --only-storage are mutually exclusive")
        if only_storage and dry_run:
            raise click.ClickException("--dry-run cannot be used with --only-storage")
        if embed_db and only_storage:
            raise click.ClickException("--embed-db cannot be combined with --only-storage")
        if start_idx < 0:
            raise click.ClickException("--start-idx must be >= 0")
        if end_idx < -1:
            raise click.ClickException("--end-idx must be -1 or >= 0")
        if only_storage and (start_idx != 0 or end_idx != -1):
            raise click.ClickException("--start-idx/--end-idx cannot be used with --only-storage")
        if embed_db and dry_run:
            embed_db = None
        if storage_concurrency < 1:
            raise click.ClickException("--storage-concurrency must be at least 1")
        if only_api and retry_failed_path:
            raise click.ClickException("--retry-failed cannot be used with --only-api")
        if retry_failed_path and not static_export_dir and not embed_db:
            raise click.ClickException("--retry-failed requires --static-export-dir or --embed-db")
        if only_storage and not static_dir:
            raise click.ClickException("--only-storage requires --static-export-dir")

        config = load_remote_config(config_file)

        retry_mode = "none"
        retry_static_files: list[str] | None = None
        retry_semantic_requests: list[dict[str, Any]] | None = None
        if retry_failed_path:
            retry_mode, retry_payload = _load_api_push_retry_report(Path(retry_failed_path))
            if retry_mode == "static":
                retry_static_files = [str(item) for item in retry_payload]
            elif retry_mode == "semantic":
                retry_semantic_requests = [item for item in retry_payload if isinstance(item, dict)]
            elif retry_mode == "empty":
                console.print("[yellow]Retry report is empty; nothing to push.[/yellow]")
                return

        if retry_mode == "static" and not config.storage:
            raise click.ClickException(
                "--retry-failed static retry requires [remote.storage] in config"
            )
        if retry_mode == "static" and not static_dir:
            raise click.ClickException("Static retry report requires --static-export-dir")
        if retry_mode == "semantic" and not embed_db:
            raise click.ClickException("Semantic retry report requires --embed-db")
        if only_storage and not config.storage:
            raise click.ClickException("--only-storage requires [remote.storage] in config")

        console.print(f"[cyan]Remote:[/cyan] {config.api_base_url}")
        console.print(f"[cyan]Batch size:[/cyan] {config.batch_size}")

        should_push_api = not only_storage and retry_mode == "none"
        should_push_storage = (
            not only_api and static_dir and config.storage and retry_mode in {"none", "static"}
        )
        should_push_semantic = embed_db is not None and not only_api and retry_mode != "static"

        selected_doc_ids: set[str] | None = None
        if start_idx != 0 or end_idx != -1:
            indexed_papers = extract_papers_from_db(db_path, static_export_dir=static_dir)
            indexed_papers = _slice_by_index_range(indexed_papers, start_idx, end_idx)
            selected_doc_ids = {
                str(paper["paper_id"])
                for paper in indexed_papers
                if isinstance(paper, dict) and paper.get("paper_id") is not None
            }

        if should_push_api:
            console.print("[cyan]Extracting papers from local DB...[/cyan]")
            papers = extract_papers_from_db(db_path, static_export_dir=static_dir)
            papers = _slice_by_index_range(papers, start_idx, end_idx)
            console.print(f"[green]Found {len(papers)} papers[/green]")

            if not papers:
                console.print("[yellow]Nothing to push[/yellow]")
                return

            templates_count = sum(1 for p in papers if p.get("templates"))
            console.print(
                f"[cyan]Papers with summary payloads:[/cyan] {templates_count}/{len(papers)}"
            )

            if dry_run:
                console.print("[yellow]Dry run — not pushing[/yellow]")
                table = Table(title="Papers to push")
                table.add_column("paper_id", style="dim")
                table.add_column("title")
                table.add_column("templates", justify="right")
                for p in papers[:20]:
                    t_count = len(p.get("templates", {}))
                    table.add_row(
                        p["paper_id"][:16] + "…", p.get("paper_title", "")[:60], str(t_count)
                    )
                if len(papers) > 20:
                    table.add_row("…", f"and {len(papers) - 20} more", "")
                console.print(table)
                return

            console.print("[cyan]Pushing to remote...[/cyan]")

            def on_batch(batch_idx: int, batch_size: int, data: dict) -> None:
                added = data.get("added", 0)
                updated = data.get("updated", 0)
                skipped = data.get("skipped", 0)
                errors = len(data.get("errors", []))
                message = (
                    f"  Batch {batch_idx + 1}: "
                    f"[green]+{added}[/green] added, "
                    f"[yellow]{skipped}[/yellow] skipped"
                )
                if updated:
                    message += f", [cyan]~{updated}[/cyan] overwritten"
                if errors:
                    message += f", [red]{errors}[/red] errors"
                console.print(message)

            try:
                stats = push_papers(papers, config, on_batch=on_batch, overwrite=overwrite)
            except Exception as exc:
                raise click.ClickException(f"Push failed: {exc}")

            console.print()
            result_table = Table(title="Push Results")
            result_table.add_column("Metric")
            result_table.add_column("Value", justify="right")
            result_table.add_row("Total papers", str(stats.total))
            result_table.add_row("Added", f"[green]{stats.added}[/green]")
            result_table.add_row("Overwritten", f"[cyan]{stats.updated}[/cyan]")
            result_table.add_row("Skipped (duplicates)", f"[yellow]{stats.skipped}[/yellow]")
            result_table.add_row(
                "Errors", f"[red]{len(stats.errors)}[/red]" if stats.errors else "0"
            )
            result_table.add_row("Batches sent", str(stats.batches_sent))
            console.print(result_table)

            if stats.errors:
                console.print("[red]Errors:[/red]")
                for err in stats.errors[:10]:
                    console.print(f"  index={err.get('index')}: {err.get('error')}")
                if len(stats.errors) > 10:
                    console.print(f"  ... and {len(stats.errors) - 10} more")
                raise click.ClickException(f"{len(stats.errors)} paper(s) failed to insert")

        # --- Static file push via remote storage ---
        if should_push_storage:
            from deepresearch_flow.paper.snapshot.push_static import (
                discover_static_files,
                push_static_files,
                write_error_report,
            )
            from deepresearch_flow.storage.base import StorageAuthError
            from deepresearch_flow.storage.factory import create_storage

            if retry_mode == "static":
                only_files = retry_static_files
                console.print(f"[cyan]Retrying {len(only_files)} failed static files...[/cyan]")
            else:
                only_files = None
                console.print("[cyan]Pushing static files...[/cyan]")

            console.print(f"[cyan]Storage:[/cyan] {config.storage.type} → {config.storage.url}")
            console.print(f"[cyan]Storage concurrency:[/cyan] {storage_concurrency}")
            static_files = discover_static_files(static_dir, only_files=only_files)
            progress = (
                tqdm(total=len(static_files), desc="Static push", unit="file")
                if static_files
                else None
            )
            progress_counts = {"uploaded": 0, "overwritten": 0, "skipped": 0, "failed": 0}

            def on_static_file_result(rel_path: str, kind: str, error: str = "") -> None:
                progress_counts[kind] += 1
                if progress is not None:
                    progress.update(1)
                    progress.set_postfix(progress_counts, refresh=False)
                if kind in {"uploaded", "overwritten", "skipped"}:
                    return
                if kind == "failed":
                    console.print(f"  [red]× failed[/red] {rel_path}: {error}")

            try:
                with create_storage(config.storage) as storage:
                    static_stats = push_static_files(
                        static_dir,
                        storage,
                        only_files=only_files,
                        on_file_result=on_static_file_result,
                        concurrency=storage_concurrency,
                        overwrite=overwrite,
                    )
            except StorageAuthError as exc:
                raise click.ClickException(str(exc)) from exc
            finally:
                if progress is not None:
                    progress.close()

            static_table = Table(title="Static Files Push")
            static_table.add_column("Directory")
            static_table.add_column("Uploaded", justify="right")
            static_table.add_column("Overwritten", justify="right")
            static_table.add_column("Skipped", justify="right")
            static_table.add_column("Failed", justify="right")

            for dirname in sorted(static_stats.per_directory):
                d = static_stats.per_directory[dirname]
                static_table.add_row(
                    dirname,
                    f"[green]{d['uploaded']}[/green]",
                    f"[cyan]{d['overwritten']}[/cyan]",
                    f"[dim]{d['skipped']}[/dim]",
                    f"[red]{d['failed']}[/red]" if d["failed"] else "0",
                )
            static_table.add_section()
            static_table.add_row(
                "[bold]Total[/bold]",
                f"[bold green]{static_stats.uploaded}[/bold green]",
                f"[bold cyan]{static_stats.overwritten}[/bold cyan]",
                f"[bold dim]{static_stats.skipped}[/bold dim]",
                f"[bold red]{static_stats.failed}[/bold red]"
                if static_stats.failed
                else "[bold]0[/bold]",
            )
            console.print(static_table)

            if static_stats.failed_files:
                error_path = Path("push-static-errors.json")
                write_error_report(static_stats.failed_files, error_path)
                console.print(f"\n[red]Failed files:[/red]")
                for entry in static_stats.failed_files[:10]:
                    console.print(f"  {entry['path']}: {entry['error']}")
                if len(static_stats.failed_files) > 10:
                    console.print(f"  ... and {len(static_stats.failed_files) - 10} more")
                console.print(f"\nFull error list saved to [bold]{error_path}[/bold]")
                console.print("Retry with: --retry-failed push-static-errors.json")
                raise click.ClickException(
                    f"{len(static_stats.failed_files)} static file(s) failed to upload"
                )

        if should_push_semantic:
            from deepresearch_flow.paper.snapshot.push_semantic import (
                SemanticPushError,
                group_chunks_for_push,
                push_semantic_chunks,
                write_error_report as write_semantic_error_report,
            )
            from deepresearch_flow.paper.vector_store import (
                load_index_meta,
                open_store,
                read_all_chunks,
            )

            embed_path = Path(embed_db)
            if not embed_path.exists():
                raise click.ClickException(f"Embed DB not found: {embed_path}")

            index_meta = load_index_meta(embed_path)
            if retry_semantic_requests is not None:
                semantic_requests = retry_semantic_requests
                if selected_doc_ids is not None:
                    semantic_requests = [
                        request
                        for request in semantic_requests
                        if str(request.get("group", {}).get("doc_id") or "") in selected_doc_ids
                    ]
                all_chunks: list[dict[str, Any]] = []
                semantic_chunk_total = sum(
                    len(list(request.get("chunks") or [])) for request in semantic_requests
                )
            else:
                embed_store = open_store(embed_path)
                all_chunks = read_all_chunks(embed_store)
                if selected_doc_ids is not None:
                    all_chunks = [
                        chunk
                        for chunk in all_chunks
                        if str(chunk.get("doc_id") or "") in selected_doc_ids
                    ]
                semantic_cfg = config.semantic
                semantic_requests = group_chunks_for_push(
                    all_chunks,
                    max_rows=semantic_cfg.max_rows,
                    max_payload_bytes=semantic_cfg.max_payload_bytes,
                )
                semantic_chunk_total = len(all_chunks)
            if semantic_requests:
                semantic_cfg = config.semantic
                console.print(f"[cyan]Pushing {semantic_chunk_total} semantic chunks...[/cyan]")
                console.print(
                    "[cyan]Semantic config:[/cyan] "
                    f"max_rows={semantic_cfg.max_rows} "
                    f"max_payload_bytes={semantic_cfg.max_payload_bytes} "
                    f"timeout={semantic_cfg.timeout} "
                    f"retries={semantic_cfg.retries} "
                    f"retry_backoff_seconds={semantic_cfg.retry_backoff_seconds}"
                )
                console.print(f"[cyan]Semantic requests:[/cyan] {len(semantic_requests)}")
                progress = tqdm(total=semantic_chunk_total, desc="Semantic push", unit="chunk")
                progress_counts = {
                    "requests": 0,
                    "retries": 0,
                    "ins": 0,
                    "upd": 0,
                    "skip": 0,
                    "del": 0,
                }

                def on_semantic_batch(
                    batch_idx: int, chunk_count: int, data: dict[str, Any]
                ) -> None:
                    progress_counts["requests"] = batch_idx + 1
                    progress_counts["ins"] += int(data.get("inserted", 0))
                    progress_counts["upd"] += int(data.get("updated", 0))
                    progress_counts["skip"] += int(data.get("skipped", 0))
                    progress_counts["del"] += int(data.get("deleted", 0))
                    progress.update(chunk_count)
                    progress.set_postfix(progress_counts, refresh=False)

                def on_semantic_retry(
                    batch_idx: int, attempt: int, failure: dict[str, Any]
                ) -> None:
                    progress_counts["retries"] += 1
                    progress.set_postfix(progress_counts, refresh=False)
                    console.print(
                        "  [yellow]semantic retry[/yellow] "
                        f"{attempt}/{semantic_cfg.retries} "
                        f"batch={batch_idx + 1}/{len(semantic_requests)} "
                        f"doc={failure.get('doc_id') or '?'} "
                        f"tag={failure.get('template_tag') or '_shared'} "
                        f"chunks={failure.get('chunk_count', 0)} "
                        f"payload_bytes={failure.get('payload_bytes', 0)} "
                        f"error={failure.get('error')}"
                    )

                try:
                    semantic_stats = push_semantic_chunks(
                        all_chunks,
                        index_meta,
                        config,
                        requests=semantic_requests,
                        on_batch=on_semantic_batch,
                        on_retry=on_semantic_retry,
                    )
                except SemanticPushError as exc:
                    error_path = Path("push-semantic-errors.json")
                    write_semantic_error_report(exc.stats.errors, error_path)
                    failure = exc.failure
                    console.print("\n[red]Semantic batch failed:[/red]")
                    console.print(
                        f"  batch={int(failure.get('batch_index', 0)) + 1}/{failure.get('total_batches', 0)} "
                        f"doc={failure.get('doc_id') or '?'} "
                        f"tag={failure.get('template_tag') or '_shared'}"
                    )
                    console.print(
                        f"  chunks={failure.get('chunk_count', 0)} "
                        f"payload_bytes={failure.get('payload_bytes', 0)} "
                        f"attempts={failure.get('attempts', 0)}"
                    )
                    console.print(f"  error={failure.get('error')}")
                    console.print(f"\nFull error list saved to [bold]{error_path}[/bold]")
                    raise click.ClickException(f"Semantic push failed: {exc}") from exc
                except Exception as exc:
                    raise click.ClickException(f"Semantic push failed: {exc}") from exc
                finally:
                    progress.close()
                console.print(
                    f"[green]Semantic:[/green] requests={semantic_stats.batches_sent} "
                    f"ins={semantic_stats.inserted} upd={semantic_stats.updated} "
                    f"skip={semantic_stats.skipped} del={semantic_stats.deleted}"
                )
            else:
                console.print("[yellow]No semantic chunks to push.[/yellow]")

    @api_group.command("push-semantic")
    @click.option(
        "--embed-db",
        "embed_db",
        required=True,
        type=click.Path(path_type=str),
        help="Path to local semantic LanceDB directory",
    )
    @click.option(
        "--config",
        "config_path",
        default="remote.toml",
        show_default=True,
        help="Path to remote config TOML file",
    )
    @click.option(
        "--retry-failed",
        "retry_failed_path",
        default=None,
        type=click.Path(exists=True),
        help="Path to push-semantic-errors.json to retry only failed semantic requests",
    )
    @click.option(
        "--start-chunk-idx",
        "start_chunk_idx",
        type=int,
        default=0,
        show_default=True,
        help="0-based start chunk index",
    )
    @click.option(
        "--end-chunk-idx",
        "end_chunk_idx",
        type=int,
        default=-1,
        show_default=True,
        help="0-based exclusive end chunk index (-1 = all)",
    )
    def api_push_semantic(
        embed_db: str,
        config_path: str,
        retry_failed_path: str | None,
        start_chunk_idx: int,
        end_chunk_idx: int,
    ) -> None:
        """Push only semantic rows from a local embed DB to the remote admin API."""
        from deepresearch_flow.paper.snapshot.push import load_remote_config
        from deepresearch_flow.paper.snapshot.push_semantic import (
            SemanticPushError,
            group_chunks_for_push,
            push_semantic_chunks,
            write_error_report as write_semantic_error_report,
        )
        from deepresearch_flow.paper.vector_store import (
            load_index_meta,
            open_store,
            read_all_chunks,
        )

        console = Console()

        config_file = Path(config_path)
        if not config_file.exists():
            raise click.ClickException(f"Config file not found: {config_file}")

        if start_chunk_idx < 0:
            raise click.ClickException("--start-chunk-idx must be >= 0")
        if end_chunk_idx < -1:
            raise click.ClickException("--end-chunk-idx must be -1 or >= 0")
        if retry_failed_path and (start_chunk_idx != 0 or end_chunk_idx != -1):
            raise click.ClickException(
                "--retry-failed cannot be combined with --start-chunk-idx/--end-chunk-idx"
            )

        config = load_remote_config(config_file)
        console.print(f"[cyan]Remote:[/cyan] {config.api_base_url}")

        retry_semantic_requests: list[dict[str, Any]] | None = None
        if retry_failed_path:
            retry_mode, retry_payload = _load_api_push_retry_report(Path(retry_failed_path))
            if retry_mode == "empty":
                console.print("[yellow]Retry report is empty; nothing to push.[/yellow]")
                return
            if retry_mode != "semantic":
                raise click.ClickException("push-semantic only accepts semantic retry reports")
            retry_semantic_requests = [item for item in retry_payload if isinstance(item, dict)]

        embed_path = Path(embed_db)
        if not embed_path.exists():
            raise click.ClickException(f"Embed DB not found: {embed_path}")

        index_meta = load_index_meta(embed_path)
        semantic_cfg = config.semantic

        if retry_semantic_requests is not None:
            semantic_requests = retry_semantic_requests
            semantic_chunk_total = sum(
                len(list(request.get("chunks") or [])) for request in semantic_requests
            )
            selected_groups = {
                (
                    str(request.get("group", {}).get("doc_id") or ""),
                    str(request.get("group", {}).get("template_tag") or ""),
                )
                for request in semantic_requests
            }
            all_chunks: list[dict[str, Any]] = []
            console.print(f"[cyan]Retrying semantic requests:[/cyan] {len(semantic_requests)}")
        else:
            embed_store = open_store(embed_path)
            local_chunks = read_all_chunks(embed_store)
            ordered_chunks, all_chunks, selected_groups = _expand_semantic_chunk_window(
                local_chunks,
                start_chunk_idx=start_chunk_idx,
                end_chunk_idx=end_chunk_idx,
            )
            semantic_chunk_total = len(all_chunks)
            console.print(f"[cyan]Local semantic chunks:[/cyan] {len(ordered_chunks)}")
            slice_end = end_chunk_idx if end_chunk_idx != -1 else len(ordered_chunks)
            console.print(f"[cyan]Chunk window:[/cyan] [{start_chunk_idx}, {slice_end})")
            console.print(f"[cyan]Selected groups:[/cyan] {len(selected_groups)}")
            console.print(f"[cyan]Expanded chunks:[/cyan] {semantic_chunk_total}")
            if not all_chunks:
                console.print("[yellow]Nothing to push.[/yellow]")
                return
            semantic_requests = group_chunks_for_push(
                all_chunks,
                max_rows=semantic_cfg.max_rows,
                max_payload_bytes=semantic_cfg.max_payload_bytes,
            )

        if not semantic_requests:
            console.print("[yellow]Nothing to push.[/yellow]")
            return

        console.print(
            "[cyan]Semantic config:[/cyan] "
            f"max_rows={semantic_cfg.max_rows} "
            f"max_payload_bytes={semantic_cfg.max_payload_bytes} "
            f"timeout={semantic_cfg.timeout} "
            f"retries={semantic_cfg.retries} "
            f"retry_backoff_seconds={semantic_cfg.retry_backoff_seconds}"
        )
        console.print(f"[cyan]Semantic requests:[/cyan] {len(semantic_requests)}")
        progress = tqdm(total=semantic_chunk_total, desc="Semantic push", unit="chunk")
        progress_counts = {"requests": 0, "retries": 0, "ins": 0, "upd": 0, "skip": 0, "del": 0}

        def on_semantic_batch(batch_idx: int, chunk_count: int, data: dict[str, Any]) -> None:
            progress_counts["requests"] = batch_idx + 1
            progress_counts["ins"] += int(data.get("inserted", 0))
            progress_counts["upd"] += int(data.get("updated", 0))
            progress_counts["skip"] += int(data.get("skipped", 0))
            progress_counts["del"] += int(data.get("deleted", 0))
            progress.update(chunk_count)
            progress.set_postfix(progress_counts, refresh=False)

        def on_semantic_retry(batch_idx: int, attempt: int, failure: dict[str, Any]) -> None:
            progress_counts["retries"] += 1
            progress.set_postfix(progress_counts, refresh=False)
            console.print(
                "  [yellow]semantic retry[/yellow] "
                f"{attempt}/{semantic_cfg.retries} "
                f"batch={batch_idx + 1}/{len(semantic_requests)} "
                f"doc={failure.get('doc_id') or '?'} "
                f"tag={failure.get('template_tag') or '_shared'} "
                f"chunks={failure.get('chunk_count', 0)} "
                f"payload_bytes={failure.get('payload_bytes', 0)} "
                f"error={failure.get('error')}"
            )

        try:
            semantic_stats = push_semantic_chunks(
                all_chunks,
                index_meta,
                config,
                requests=semantic_requests,
                on_batch=on_semantic_batch,
                on_retry=on_semantic_retry,
            )
        except SemanticPushError as exc:
            error_path = Path("push-semantic-errors.json")
            write_semantic_error_report(exc.stats.errors, error_path)
            failure = exc.failure
            console.print("\n[red]Semantic batch failed:[/red]")
            console.print(
                f"  batch={int(failure.get('batch_index', 0)) + 1}/{failure.get('total_batches', 0)} "
                f"doc={failure.get('doc_id') or '?'} "
                f"tag={failure.get('template_tag') or '_shared'}"
            )
            console.print(
                f"  chunks={failure.get('chunk_count', 0)} "
                f"payload_bytes={failure.get('payload_bytes', 0)} "
                f"attempts={failure.get('attempts', 0)}"
            )
            console.print(f"  error={failure.get('error')}")
            console.print(f"\nFull error list saved to [bold]{error_path}[/bold]")
            raise click.ClickException(f"Semantic push failed: {exc}") from exc
        except Exception as exc:
            raise click.ClickException(f"Semantic push failed: {exc}") from exc
        finally:
            progress.close()

        console.print(
            f"[green]Semantic:[/green] requests={semantic_stats.batches_sent} "
            f"ins={semantic_stats.inserted} upd={semantic_stats.updated} "
            f"skip={semantic_stats.skipped} del={semantic_stats.deleted}"
        )

    @db_group.command("append-bibtex")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-b", "--bibtex", "bibtex_path", required=True, help="Input BibTeX file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    def append_bibtex(input_path: str, bibtex_path: str, output_path: str) -> None:
        if not PYBTEX_AVAILABLE:
            raise click.ClickException("pybtex is required for append-bibtex")

        papers = load_json(Path(input_path))
        bib_data = parse_file(bibtex_path)
        bib_entries = []
        for key, entry in bib_data.entries.items():
            bib_entries.append(
                {
                    "key": key,
                    "type": entry.type,
                    "fields": dict(entry.fields),
                    "persons": {
                        role: [str(p) for p in persons] for role, persons in entry.persons.items()
                    },
                }
            )

        appended = []
        for paper in papers:
            title = paper.get("paper_title") or ""
            matched = False
            for bib in bib_entries:
                bib_title = bib.get("fields", {}).get("title", "")
                if similar_title(title, bib_title):
                    paper["bibtex"] = bib
                    matched = True
                    break
            if matched:
                appended.append(paper)
        write_json(Path(output_path), appended)
        click.echo(f"Appended bibtex for {len(appended)} papers")

    @db_group.command("sort-papers")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option("--order", type=click.Choice(["asc", "desc"]), default="desc")
    def sort_papers(input_path: str, output_path: str, order: str) -> None:
        papers = load_json(Path(input_path))
        reverse = order == "desc"
        papers.sort(key=lambda p: parse_publication_year(p) or 0, reverse=reverse)
        write_json(Path(output_path), papers)
        click.echo(f"Sorted {len(papers)} papers")

    @db_group.command("split-by-tag")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-d", "--output-dir", "output_dir", required=True, help="Output directory")
    def split_by_tag(input_path: str, output_dir: str) -> None:
        papers = load_json(Path(input_path))
        tag_map: dict[str, list[dict[str, Any]]] = {}
        for paper in papers:
            tags = paper.get("ai_generated_tags") or []
            for tag in tags:
                tag_map.setdefault(tag, []).append(paper)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for tag, items in tag_map.items():
            write_json(out_dir / f"{tag}.json", items)
        write_json(out_dir / "index.json", {"tags": sorted(tag_map.keys())})
        click.echo(f"Split into {len(tag_map)} tag files")

    @db_group.command("split-database")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-d", "--output-dir", "output_dir", required=True, help="Output directory")
    @click.option(
        "-c",
        "--criteria",
        type=click.Choice(["year", "alphabetical", "count"]),
        default="count",
    )
    @click.option("-n", "--count", "chunk_count", default=100, help="Chunk size for count criteria")
    def split_database(input_path: str, output_dir: str, criteria: str, chunk_count: int) -> None:
        papers = load_json(Path(input_path))
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if criteria == "year":
            by_year: dict[str, list[dict[str, Any]]] = {}
            for paper in papers:
                year = parse_publication_year(paper)
                key = str(year) if year else "unknown"
                by_year.setdefault(key, []).append(paper)
            for year, items in by_year.items():
                write_json(out_dir / f"year_{year}.json", items)
            click.echo(f"Split into {len(by_year)} year files")
            return

        if criteria == "alphabetical":
            by_letter: dict[str, list[dict[str, Any]]] = {}
            for paper in papers:
                title = (paper.get("paper_title") or "").strip()
                letter = title[:1].upper() if title else "#"
                by_letter.setdefault(letter, []).append(paper)
            for letter, items in by_letter.items():
                write_json(out_dir / f"{letter}.json", items)
            click.echo(f"Split into {len(by_letter)} letter files")
            return

        chunks = [papers[i : i + chunk_count] for i in range(0, len(papers), chunk_count)]
        for idx, chunk in enumerate(chunks, start=1):
            write_json(out_dir / f"chunk_{idx}.json", chunk)
        click.echo(f"Split into {len(chunks)} chunks")

    @db_group.command("statistics")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option(
        "--top-n", "top_n", default=20, type=int, show_default=True, help="Top N rows to show"
    )
    def statistics(input_path: str, top_n: int) -> None:
        papers = load_json(Path(input_path))
        console = Console()
        console.print(Panel(f"Statistics for {input_path}", title="Paper Statistics"))

        year_counts: dict[str, int] = {}
        month_counts: dict[str, int] = {}
        author_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        keyword_counts: dict[str, int] = {}
        journal_counts: dict[str, int] = {}
        conference_counts: dict[str, int] = {}
        other_venue_counts: dict[str, int] = {}

        def normalize_keywords(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, list):
                items = value
            elif isinstance(value, str):
                items = re.split(r"[;,]", value)
            else:
                items = [value]
            normalized: list[str] = []
            for item in items:
                token = str(item).strip().lower()
                if token:
                    normalized.append(token)
            return normalized

        for paper in papers:
            bibtex_fields = {}
            bibtex_type = None
            if isinstance(paper.get("bibtex"), dict):
                bibtex_fields = paper.get("bibtex", {}).get("fields", {}) or {}
                bibtex_type = (paper.get("bibtex", {}).get("type") or "").lower()

            year_value = None
            if bibtex_fields.get("year"):
                year_value = str(bibtex_fields.get("year"))
            if not year_value:
                year_value, _ = parse_year_month(str(paper.get("publication_date") or ""))
            year_key = year_value or "Unknown"
            year_counts[year_key] = year_counts.get(year_key, 0) + 1

            month_value = normalize_month(bibtex_fields.get("month"))
            if not month_value:
                _, month_value = parse_year_month(str(paper.get("publication_date") or ""))
            month_key = month_value or "Unknown"
            month_counts[month_key] = month_counts.get(month_key, 0) + 1

            for author in normalize_authors(paper.get("paper_authors")):
                author_counts[author] = author_counts.get(author, 0) + 1
            for tag in paper.get("ai_generated_tags") or []:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            for keyword in normalize_keywords(paper.get("keywords")):
                keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

            venue = None
            if bibtex_type in {"article"}:
                venue = bibtex_fields.get("journal")
                journal_counts[clean_journal_name(venue)] = (
                    journal_counts.get(
                        clean_journal_name(venue),
                        0,
                    )
                    + 1
                )
            elif bibtex_type in {"inproceedings", "conference", "proceedings"}:
                venue = bibtex_fields.get("booktitle")
                conference_counts[clean_conference_name(venue)] = (
                    conference_counts.get(
                        clean_conference_name(venue),
                        0,
                    )
                    + 1
                )
            else:
                extracted_venue = paper.get("publication_venue")
                venue_kind = classify_venue(extracted_venue)
                if venue_kind == "journal":
                    journal_counts[clean_journal_name(extracted_venue)] = (
                        journal_counts.get(
                            clean_journal_name(extracted_venue),
                            0,
                        )
                        + 1
                    )
                elif venue_kind == "conference":
                    conference_counts[clean_conference_name(extracted_venue)] = (
                        conference_counts.get(
                            clean_conference_name(extracted_venue),
                            0,
                        )
                        + 1
                    )
                elif extracted_venue:
                    other_venue_counts[clean_conference_name(extracted_venue)] = (
                        other_venue_counts.get(
                            clean_conference_name(extracted_venue),
                            0,
                        )
                        + 1
                    )

        total = len(papers)
        console.print(f"Total papers: {total}")

        year_table = Table(title="Publication Year Statistics")
        year_table.add_column("Year", style="cyan")
        year_table.add_column("Count", style="green", justify="right")
        year_table.add_column("Percentage", style="yellow", justify="right")
        year_table.add_column("Distribution", style="magenta")

        max_year = max(year_counts.values()) if year_counts else 0

        def year_sort_key(item: tuple[str, int]) -> tuple[int, int]:
            label = item[0]
            if label == "Unknown":
                return (1, 0)
            if label.isdigit():
                return (0, -int(label))
            return (0, 0)

        for year, count in sorted(year_counts.items(), key=year_sort_key):
            percentage = (count / total * 100) if total else 0
            year_table.add_row(
                year,
                str(count),
                f"{percentage:.1f}%",
                format_distribution(count, max_year),
            )
        console.print(year_table)

        month_table = Table(title="Publication Month Statistics")
        month_table.add_column("Month", style="cyan")
        month_table.add_column("Count", style="green", justify="right")
        month_table.add_column("Percentage", style="yellow", justify="right")
        month_table.add_column("Distribution", style="magenta")

        max_month = max(month_counts.values()) if month_counts else 0

        def month_sort_key(item: tuple[str, int]) -> int:
            if item[0] == "Unknown":
                return 99
            if item[0] in MONTH_NAMES:
                return MONTH_NAMES.index(item[0])
            return 98

        for month, count in sorted(month_counts.items(), key=month_sort_key):
            percentage = (count / total * 100) if total else 0
            month_table.add_row(
                month,
                str(count),
                f"{percentage:.1f}%",
                format_distribution(count, max_month),
            )
        console.print(month_table)

        if journal_counts:
            journal_table = Table(title=f"Top {top_n} Journals")
            journal_table.add_column("Journal", style="cyan")
            journal_table.add_column("Count", style="green", justify="right")
            journal_table.add_column("Percentage", style="yellow", justify="right")
            for journal, count in sorted(
                journal_counts.items(), key=lambda item: item[1], reverse=True
            )[:top_n]:
                percentage = (count / total * 100) if total else 0
                journal_table.add_row(journal, str(count), f"{percentage:.1f}%")
            console.print(journal_table)

        if conference_counts:
            conference_table = Table(title=f"Top {top_n} Conferences")
            conference_table.add_column("Conference", style="cyan")
            conference_table.add_column("Count", style="green", justify="right")
            conference_table.add_column("Percentage", style="yellow", justify="right")
            for conference, count in sorted(
                conference_counts.items(), key=lambda item: item[1], reverse=True
            )[:top_n]:
                percentage = (count / total * 100) if total else 0
                conference_table.add_row(conference, str(count), f"{percentage:.1f}%")
            console.print(conference_table)

        if other_venue_counts:
            other_table = Table(title=f"Top {top_n} Other Venues")
            other_table.add_column("Venue", style="cyan")
            other_table.add_column("Count", style="green", justify="right")
            other_table.add_column("Percentage", style="yellow", justify="right")
            for venue, count in sorted(
                other_venue_counts.items(), key=lambda item: item[1], reverse=True
            )[:top_n]:
                percentage = (count / total * 100) if total else 0
                other_table.add_row(venue, str(count), f"{percentage:.1f}%")
            console.print(other_table)

        if author_counts:
            author_table = Table(title=f"Top {top_n} Authors")
            author_table.add_column("Author", style="cyan")
            author_table.add_column("Papers", style="green", justify="right")
            author_table.add_column("Percentage", style="yellow", justify="right")
            for author, count in sorted(
                author_counts.items(), key=lambda item: item[1], reverse=True
            )[:top_n]:
                percentage = (count / total * 100) if total else 0
                author_table.add_row(author, str(count), f"{percentage:.1f}%")
            console.print(author_table)

        if tag_counts:
            tag_table = Table(title=f"Top {top_n} Tags")
            tag_table.add_column("Tag", style="cyan")
            tag_table.add_column("Count", style="green", justify="right")
            tag_table.add_column("Percentage", style="yellow", justify="right")
            for tag, count in sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[
                :top_n
            ]:
                percentage = (count / total * 100) if total else 0
                tag_table.add_row(tag, str(count), f"{percentage:.1f}%")
            console.print(tag_table)

        if keyword_counts:
            keyword_table = Table(title=f"Top {top_n} Keywords")
            keyword_table.add_column("Keyword", style="cyan")
            keyword_table.add_column("Count", style="green", justify="right")
            keyword_table.add_column("Percentage", style="yellow", justify="right")
            for keyword, count in sorted(
                keyword_counts.items(), key=lambda item: item[1], reverse=True
            )[:top_n]:
                percentage = (count / total * 100) if total else 0
                keyword_table.add_row(keyword, str(count), f"{percentage:.1f}%")
            console.print(keyword_table)

    @db_group.command("serve")
    @click.option(
        "-i", "--input", "input_paths", multiple=True, required=True, help="Input JSON file path"
    )
    @click.option("-b", "--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        default=(),
        help="Optional markdown root directory (repeatable) for source viewing",
    )
    @click.option(
        "--md-translated-root",
        "md_translated_roots",
        multiple=True,
        default=(),
        help="Optional markdown root directory (repeatable) for translated viewing",
    )
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        default=(),
        help="Optional PDF root directory (repeatable) for in-page PDF viewing",
    )
    @click.option(
        "--cache-dir", "cache_dir", default=None, help="Cache directory for merged inputs"
    )
    @click.option("--no-cache", "no_cache", is_flag=True, help="Disable cache for db serve")
    @click.option(
        "--static-base-url",
        "static_base_url",
        default=None,
        help="Static asset base URL (e.g. https://static.example.com)",
    )
    @click.option(
        "--static-mode",
        "static_mode",
        type=click.Choice(["auto", "dev", "prod"]),
        default="auto",
        show_default=True,
        help="Static asset mode (dev uses local assets, prod uses static base URL)",
    )
    @click.option(
        "--static-export-dir",
        "static_export_dir",
        default=None,
        help="Optional export directory for hashed static assets",
    )
    @click.option(
        "--pdfjs-cdn-base-url",
        "pdfjs_cdn_base_url",
        default=None,
        help="PDF.js CDN base URL (defaults to jsDelivr)",
    )
    @click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
    @click.option("--port", default=8000, type=int, show_default=True, help="Bind port")
    @click.option(
        "--embed-db", "embed_db", default=None, help="LanceDB directory for semantic search"
    )
    @click.option(
        "--search-access-token",
        "search_access_token",
        default=None,
        envvar="SEARCH_ACCESS_TOKEN",
        help="Token to gate semantic search",
    )
    @click.option(
        "-c", "--config", "config_path", default="config.toml", help="Path to config.toml"
    )
    @click.option(
        "--language",
        "fallback_language",
        default="en",
        show_default=True,
        help="Fallback output language for rendering",
    )
    def serve(
        input_paths: tuple[str, ...],
        bibtex_path: str | None,
        md_roots: tuple[str, ...],
        md_translated_roots: tuple[str, ...],
        pdf_roots: tuple[str, ...],
        cache_dir: str | None,
        no_cache: bool,
        static_base_url: str | None,
        static_mode: str,
        static_export_dir: str | None,
        pdfjs_cdn_base_url: str | None,
        host: str,
        port: int,
        embed_db: str | None,
        search_access_token: str | None,
        config_path: str,
        fallback_language: str,
    ) -> None:
        """Serve a local, read-only web UI for a paper database JSON file."""
        from deepresearch_flow.paper.web.app import create_app
        import uvicorn

        try:
            paper_config = load_config(config_path)
            app = create_app(
                db_paths=[Path(path) for path in input_paths],
                fallback_language=fallback_language,
                bibtex_path=Path(bibtex_path) if bibtex_path else None,
                md_roots=[Path(root) for root in md_roots],
                md_translated_roots=[Path(root) for root in md_translated_roots],
                pdf_roots=[Path(root) for root in pdf_roots],
                cache_dir=Path(cache_dir) if cache_dir else None,
                use_cache=not no_cache,
                static_base_url=static_base_url,
                static_mode=static_mode,
                static_export_dir=Path(static_export_dir) if static_export_dir else None,
                pdfjs_cdn_base_url=pdfjs_cdn_base_url,
                embed_db=Path(embed_db) if embed_db else None,
                search_access_token=search_access_token,
                paper_config=paper_config,
            )
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Serving on http://{host}:{port} (Ctrl+C to stop)")
        uvicorn.run(app, host=host, port=port, log_level="info")

    @db_group.command("generate-tags")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option(
        "-c", "--config", "config_path", default="config.toml", help="Path to config.toml"
    )
    @click.option("-m", "--model", "model_ref", required=True, help="provider/model")
    @click.option("-w", "--workers", "workers", default=4, type=int, help="Concurrent workers")
    def generate_tags(
        input_path: str, output_path: str, config_path: str, model_ref: str, workers: int
    ) -> None:
        async def _run() -> None:
            config = load_config(config_path)
            selector = parse_model_selector(model_ref, config)
            route_pool = RoutePool.from_selector(
                config,
                selector,
                cooldown_seconds=max(1.0, float(config.extract.backoff_base_seconds)),
            )
            provider_name, model_name = model_ref.split("/", 1)
            provider, _ = resolve_model_capability(provider_name, model_name, config.providers)

            papers = load_json(Path(input_path))
            semaphore = asyncio.Semaphore(workers)

            async with httpx.AsyncClient() as client:

                async def process_one(paper: dict[str, Any]) -> None:
                    async with semaphore:
                        tags = await generate_tags_for_paper(
                            client,
                            provider,
                            model_name,
                            None,
                            paper,
                            max_retries=config.extract.max_retries,
                            backoff_base=config.extract.backoff_base_seconds,
                            backoff_max=config.extract.backoff_max_seconds,
                            route_pool=route_pool,
                        )
                        paper["ai_generated_tags"] = tags

                await asyncio.gather(*(process_one(paper) for paper in papers))

            write_json(Path(output_path), papers)
            click.echo(f"Generated tags for {len(papers)} papers")

        with provider_window_error_as_click():
            asyncio.run(_run())

    @db_group.command("filter")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option("-t", "--tags", default=None, help="Comma-separated tags")
    @click.option("-y", "--years", default=None, help="Year range (e.g. 2018-2024, -2019, 2020-)")
    @click.option("-a", "--authors", default=None, help="Comma-separated author names")
    @click.option("-l", "--limit", default=None, type=int, help="Limit results")
    @click.option("-r", "--order", type=click.Choice(["asc", "desc"]), default="desc")
    def filter_papers(
        input_path: str,
        output_path: str,
        tags: str | None,
        years: str | None,
        authors: str | None,
        limit: int | None,
        order: str,
    ) -> None:
        papers = load_json(Path(input_path))
        tag_set = {tag.strip() for tag in tags.split(",")} if tags else set()
        author_set = {a.strip() for a in authors.split(",")} if authors else set()

        def year_match(paper: dict[str, Any]) -> bool:
            if not years:
                return True
            year = parse_publication_year(paper)
            if year is None:
                return False
            if years.startswith("-"):
                return year <= int(years[1:])
            if years.endswith("-"):
                return year >= int(years[:-1])
            if "-" in years:
                start, end = years.split("-", 1)
                return int(start) <= year <= int(end)
            return year == int(years)

        filtered = []
        for paper in papers:
            if tag_set:
                paper_tags = set(paper.get("ai_generated_tags") or [])
                if not paper_tags.intersection(tag_set):
                    continue
            if author_set:
                paper_authors = set(normalize_authors(paper.get("paper_authors")))
                if not paper_authors.intersection(author_set):
                    continue
            if not year_match(paper):
                continue
            filtered.append(paper)

        filtered.sort(key=lambda p: parse_publication_year(p) or 0, reverse=(order == "desc"))
        if limit:
            filtered = filtered[:limit]
        write_json(Path(output_path), filtered)
        click.echo(f"Filtered down to {len(filtered)} papers")

    @db_group.group("merge")
    def merge_group() -> None:
        """Merge paper JSON inputs."""

    def _summarize_merge(output_path: Path, merged: Any, *, input_count: int) -> None:
        items: list[dict[str, Any]] = []
        if isinstance(merged, dict):
            raw_items = merged.get("papers")
            if isinstance(raw_items, list):
                items = [item for item in raw_items if isinstance(item, dict)]
        elif isinstance(merged, list):
            items = [item for item in merged if isinstance(item, dict)]

        field_set: set[str] = set()
        for item in items:
            field_set.update(item.keys())
        field_list = sorted(field_set)

        console = Console()
        summary = Table(title="Merge Summary")
        summary.add_column("Metric", style="bold")
        summary.add_column("Value")
        summary.add_row("Inputs", str(input_count))
        summary.add_row("Items", str(len(items)))
        summary.add_row("Fields", str(len(field_list)))
        summary.add_row("Output", str(output_path))
        console.print(summary)

        if field_list:
            field_table = Table(title="Fields")
            field_table.add_column("Name")
            for name in field_list:
                field_table.add_row(name)
            console.print(field_table)

    def _bibtex_entry_score(entry: Any) -> int:
        fields = getattr(entry, "fields", {}) or {}
        persons = getattr(entry, "persons", {}) or {}
        person_count = sum(len(people) for people in persons.values())
        return len(fields) + len(persons) + person_count

    def _summarize_bibtex_merge(
        output_path: Path, *, input_count: int, entry_count: int, duplicate_count: int
    ) -> None:
        summary = Table(title="BibTeX Merge Summary")
        summary.add_column("Metric", style="bold")
        summary.add_column("Value")
        summary.add_row("Inputs", str(input_count))
        summary.add_row("Entries", str(entry_count))
        summary.add_row("Duplicates", str(duplicate_count))
        summary.add_row("Output", str(output_path))
        Console().print(summary)

    @merge_group.command("library")
    @click.option(
        "-i", "--inputs", "input_paths", multiple=True, required=True, help="Input JSON files"
    )
    @click.option(
        "--template-tag", "template_tag", default=None, help="Template tag for merged output"
    )
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    def merge_library(
        input_paths: Iterable[str], template_tag: str | None, output_path: str
    ) -> None:
        paths = [Path(path) for path in input_paths]
        merged: list[dict[str, Any]] = []
        tag_candidates: list[str] = []
        for path in paths:
            payload = load_json(path)
            if isinstance(payload, dict):
                tag = str(payload.get("template_tag") or "")
                if tag:
                    tag_candidates.append(tag)
                papers = payload.get("papers")
                if isinstance(papers, list):
                    merged.extend(papers)
                else:
                    raise click.ClickException(
                        "Input JSON must be a list or {template_tag, papers}"
                    )
            elif isinstance(payload, list):
                merged.extend(payload)
            else:
                raise click.ClickException("Input JSON must be a list or {template_tag, papers}")
        if not template_tag:
            inferred = ""
            for paper in merged:
                if not isinstance(paper, dict):
                    continue
                inferred = str(paper.get("prompt_template") or paper.get("template_tag") or "")
                if inferred:
                    break
            if inferred:
                template_tag = inferred
        if tag_candidates and not template_tag:
            template_tag = tag_candidates[0]
        if not template_tag:
            template_tag = "unknown"
        if tag_candidates and any(tag != template_tag for tag in tag_candidates):
            click.echo("Warning: multiple template_tag values detected in inputs; using first")
        output = Path(output_path)
        bundle = {"template_tag": template_tag, "papers": merged}
        write_json(output, bundle)
        _summarize_merge(output, bundle, input_count=len(paths))

    @merge_group.command("templates")
    @click.option(
        "-i", "--inputs", "input_paths", multiple=True, required=True, help="Input JSON files"
    )
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    def merge_templates(input_paths: Iterable[str], output_path: str) -> None:
        from deepresearch_flow.paper import db_ops

        paths = [Path(path) for path in input_paths]
        inputs = db_ops._load_paper_inputs(paths)
        if not inputs:
            raise click.ClickException("No input JSON files provided")

        groups: list[dict[str, Any]] = []
        base_papers: list[dict[str, Any]] = []
        hash_to_group: dict[str, int] = {}
        paper_id_to_group: dict[int, int] = {}
        paper_index: dict[str, list[dict[str, Any]]] = {}

        def rebuild_index() -> None:
            nonlocal paper_index, paper_id_to_group
            paper_index = db_ops._build_paper_index(base_papers)
            paper_id_to_group = {id(paper): idx for idx, paper in enumerate(base_papers)}

        def add_group(template_tag: str, paper: dict[str, Any]) -> None:
            group = {
                "templates": {template_tag: paper},
                "template_order": [template_tag],
            }
            groups.append(group)
            base_papers.append(paper)
            source_hash = str(paper.get("source_hash") or "")
            if source_hash:
                hash_to_group[source_hash] = len(groups) - 1
            rebuild_index()

        stats: dict[str, dict[str, int]] = {}
        diff_counts: dict[tuple[str, str], int] = {}
        diff_samples: list[tuple[str, str, str, str, str]] = []
        first_tag = str(inputs[0].get("template_tag") or "")
        base_items = inputs[0].get("papers") or []
        stats[first_tag] = {"total": len(base_items), "matched": len(base_items), "skipped": 0}
        for paper in base_items:
            if not isinstance(paper, dict):
                raise click.ClickException("Input papers must be objects")
            db_ops._prepare_paper_matching_fields(paper)
            add_group(first_tag, paper)

        for bundle in inputs[1:]:
            template_tag = str(bundle.get("template_tag") or "")
            items = bundle.get("papers") or []
            matched = 0
            skipped = 0
            for paper in items:
                if not isinstance(paper, dict):
                    raise click.ClickException("Input papers must be objects")
                db_ops._prepare_paper_matching_fields(paper)
                source_hash = str(paper.get("source_hash") or "")
                match_idx: int | None = None
                if source_hash and source_hash in hash_to_group:
                    match_idx = hash_to_group[source_hash]
                else:
                    match_paper, _, _ = db_ops._resolve_paper_by_title_and_meta(paper, paper_index)
                    if match_paper is not None:
                        match_idx = paper_id_to_group.get(id(match_paper))
                if match_idx is None:
                    skipped += 1
                    continue
                matched += 1
                group = groups[match_idx]
                base_templates = group.get("templates") or {}
                base_paper = base_templates.get(first_tag)
                if isinstance(base_paper, dict):
                    for field in ("source_hash", "paper_title", "publication_date"):
                        base_value = str(base_paper.get(field) or "")
                        other_value = str(paper.get(field) or "")
                        if base_value == other_value:
                            continue
                        diff_counts[(template_tag, field)] = (
                            diff_counts.get((template_tag, field), 0) + 1
                        )
                        if len(diff_samples) < 50:
                            diff_samples.append(
                                (
                                    template_tag,
                                    field,
                                    str(base_paper.get("paper_title") or ""),
                                    base_value,
                                    other_value,
                                )
                            )
                templates = group.setdefault("templates", {})
                templates[template_tag] = paper
                order = group.setdefault("template_order", [])
                if template_tag not in order:
                    order.append(template_tag)
            stats[template_tag] = {"total": len(items), "matched": matched, "skipped": skipped}

        merged: list[dict[str, Any]] = []
        for group in groups:
            templates = group.get("templates") or {}
            order = group.get("template_order") or list(templates.keys())
            entry: dict[str, Any] = {}
            for tag in order:
                paper = templates.get(tag)
                if not isinstance(paper, dict):
                    continue
                for key, value in paper.items():
                    if key not in entry:
                        entry[key] = value
            merged.append(entry)

        output = Path(output_path)
        write_json(output, merged)
        _summarize_merge(output, merged, input_count=len(paths))

        stat_table = Table(title="Template Merge Stats")
        stat_table.add_column("Template")
        stat_table.add_column("Total", justify="right")
        stat_table.add_column("Matched", justify="right")
        stat_table.add_column("Skipped", justify="right")
        for tag, values in stats.items():
            stat_table.add_row(
                tag or "(unknown)",
                str(values.get("total", 0)),
                str(values.get("matched", 0)),
                str(values.get("skipped", 0)),
            )
        Console().print(stat_table)

        if diff_counts:
            diff_table = Table(title="Template Field Diff Summary")
            diff_table.add_column("Template")
            diff_table.add_column("Field")
            diff_table.add_column("Count", justify="right")
            for (template_tag, field), count in sorted(diff_counts.items()):
                diff_table.add_row(template_tag or "(unknown)", field, str(count))
            Console().print(diff_table)

        if diff_samples:
            sample_table = Table(title="Template Field Diff Samples (up to 50)")
            sample_table.add_column("Template")
            sample_table.add_column("Field")
            sample_table.add_column("Base Title")
            sample_table.add_column("Base Value")
            sample_table.add_column("Other Value")
            for row in diff_samples:
                sample_table.add_row(*row)
            Console().print(sample_table)

    @merge_group.command("bibtex")
    @click.option(
        "-i", "--input", "input_paths", multiple=True, required=True, help="Input BibTeX file paths"
    )
    @click.option("-o", "--output", "output_path", required=True, help="Output BibTeX file path")
    def merge_bibtex(input_paths: Iterable[str], output_path: str) -> None:
        if not PYBTEX_AVAILABLE:
            raise click.ClickException("pybtex is required for merge bibtex")

        paths = [Path(path) for path in input_paths]
        if not paths:
            raise click.ClickException("No BibTeX inputs provided")

        for path in paths:
            if not path.is_file():
                raise click.ClickException(f"BibTeX file not found: {path}")

        merged_entries: dict[str, tuple[Any, int]] = {}
        duplicate_keys: list[str] = []
        duplicate_seen: set[str] = set()

        for path in paths:
            bib_data = parse_file(str(path))
            for key, entry in bib_data.entries.items():
                score = _bibtex_entry_score(entry)
                if key not in merged_entries:
                    merged_entries[key] = (entry, score)
                    continue
                if key not in duplicate_seen:
                    duplicate_seen.add(key)
                    duplicate_keys.append(key)
                _, existing_score = merged_entries[key]
                if score > existing_score:
                    merged_entries[key] = (entry, score)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        out_data = BibliographyData()
        for key, (entry, _) in merged_entries.items():
            out_data.entries[key] = entry
        with output.open("w", encoding="utf-8") as handle:
            Writer().write_stream(out_data, handle)

        _summarize_bibtex_merge(
            output,
            input_count=len(paths),
            entry_count=len(merged_entries),
            duplicate_count=len(duplicate_keys),
        )

        if duplicate_keys:
            preview_limit = 20
            preview = ", ".join(duplicate_keys[:preview_limit])
            if len(duplicate_keys) > preview_limit:
                preview = f"{preview}, ... (+{len(duplicate_keys) - preview_limit} more)"
            note = "Kept entry with most fields; ties keep first input order."
            Console().print(
                Panel(
                    f"{note}\n{preview}",
                    title=f"Duplicate keys ({len(duplicate_keys)})",
                    style="yellow",
                )
            )

    @db_group.command("render-md")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option(
        "-d", "--output-dir", "output_dir", default="rendered_md", help="Output directory"
    )
    @click.option(
        "-t",
        "--markdown-template",
        "--template",
        "template_path",
        default=None,
        help="Jinja2 template path",
    )
    @click.option(
        "-n",
        "--template-name",
        "template_name",
        default=None,
        type=click.Choice(list_template_names()),
        help="Built-in template name",
    )
    @click.option(
        "-T",
        "--template-dir",
        "template_dir",
        default=None,
        help="Directory containing render.j2",
    )
    @click.option(
        "-l",
        "--language",
        "output_language",
        default="en",
        show_default=True,
        help="Fallback output language for rendering",
    )
    def render_md(
        input_path: str,
        output_dir: str,
        template_path: str | None,
        template_name: str | None,
        template_dir: str | None,
        output_language: str,
    ) -> None:
        papers = load_json(Path(input_path))
        out_dir = Path(output_dir)
        try:
            template = resolve_render_template(template_path, template_name, template_dir)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        rendered = render_papers(papers, out_dir, template, output_language)
        click.echo(f"Rendered {rendered} markdown files")

    @db_group.command("extract")
    @click.option("--json", "target_json", default=None, help="Target JSON database path")
    @click.option("--input-json", "input_json", default=None, help="Reference JSON file path")
    @click.option(
        "--pdf-root",
        "pdf_roots",
        multiple=True,
        help="PDF root directories for reference (repeatable)",
    )
    @click.option(
        "--md-root",
        "md_roots",
        multiple=True,
        help="Markdown root directories for reference (repeatable)",
    )
    @click.option(
        "--md-translated-root",
        "md_translated_roots",
        multiple=True,
        help="Translated Markdown root directories to extract from (repeatable)",
    )
    @click.option(
        "--md-source-root",
        "md_source_roots",
        multiple=True,
        help="Source Markdown root directories to extract from (repeatable)",
    )
    @click.option("--output-json", "output_json", default=None, help="Output JSON file path")
    @click.option(
        "--output-md-translated-root",
        "output_md_translated_root",
        default=None,
        help="Output directory for matched translated Markdown",
    )
    @click.option(
        "--output-md-root",
        "output_md_root",
        default=None,
        help="Output directory for matched source Markdown",
    )
    @click.option(
        "-b",
        "--input-bibtex",
        "input_bibtex",
        default=None,
        help="Reference BibTeX file path",
    )
    @click.option(
        "--lang", "lang", default=None, help="Language code for translated Markdown (e.g., zh)"
    )
    @click.option("--output-csv", "output_csv", default=None, help="Path to export results as CSV")
    def extract(
        target_json: str | None,
        input_json: str | None,
        pdf_roots: tuple[str, ...],
        md_roots: tuple[str, ...],
        md_translated_roots: tuple[str, ...],
        md_source_roots: tuple[str, ...],
        output_json: str | None,
        output_md_translated_root: str | None,
        output_md_root: str | None,
        input_bibtex: str | None,
        lang: str | None,
        output_csv: str | None,
    ) -> None:
        from deepresearch_flow.paper import db_ops
        from deepresearch_flow.paper.utils import stable_hash

        if input_json and input_bibtex:
            raise click.ClickException("Use only one of --input-json or --input-bibtex")

        if target_json is None and input_json is not None:
            target_json = input_json

        has_reference = bool(pdf_roots or md_roots or input_json or input_bibtex)
        if not has_reference:
            raise click.ClickException(
                "Provide at least one reference input: --pdf-root, --md-root, --input-json, or --input-bibtex"
            )
        if not target_json and not md_translated_roots and not md_source_roots:
            raise click.ClickException(
                "Provide --json and/or --md-translated-root and/or --md-source-root"
            )
        if target_json and not output_json:
            raise click.ClickException("--output-json is required when using --json")
        if output_json and not target_json:
            raise click.ClickException("--json is required when using --output-json")
        if md_translated_roots and not output_md_translated_root:
            raise click.ClickException(
                "--output-md-translated-root is required when using --md-translated-root"
            )
        if output_md_translated_root and not md_translated_roots:
            raise click.ClickException(
                "--md-translated-root is required when using --output-md-translated-root"
            )
        if md_source_roots and not output_md_root:
            raise click.ClickException("--output-md-root is required when using --md-source-root")
        if output_md_root and not md_source_roots:
            raise click.ClickException("--md-source-root is required when using --output-md-root")
        if md_translated_roots and not lang:
            raise click.ClickException("--lang is required when extracting translated Markdown")

        pdf_root_paths = [Path(path) for path in pdf_roots]
        md_root_paths = [Path(path) for path in md_roots]
        translated_root_paths = [Path(path) for path in md_translated_roots]
        source_root_paths = [Path(path) for path in md_source_roots]
        reference_json_path = Path(input_json) if input_json else None
        reference_bibtex_path = Path(input_bibtex) if input_bibtex else None

        reference_papers: list[dict[str, Any]] = []
        if reference_json_path:
            if not reference_json_path.is_file():
                raise click.ClickException(f"Reference JSON not found: {reference_json_path}")
            reference_papers, _ = load_json_payload(reference_json_path)
        if reference_bibtex_path:
            if not reference_bibtex_path.is_file():
                raise click.ClickException(f"Reference BibTeX not found: {reference_bibtex_path}")
            if not db_ops.PYBTEX_AVAILABLE:
                raise click.ClickException("pybtex is required for --input-bibtex support")
            bib_data = db_ops.parse_file(str(reference_bibtex_path))
            for key, entry in bib_data.entries.items():
                title = entry.fields.get("title")
                if not title:
                    continue
                year = entry.fields.get("year") or ""
                year = str(year) if str(year).isdigit() else ""
                authors = []
                for person in entry.persons.get("author", []):
                    authors.append(str(person))
                reference_papers.append(
                    {
                        "paper_title": str(title),
                        "paper_authors": authors,
                        "publication_date": year,
                        "source_path": f"bibtex:{key}",
                    }
                )

        reference_index: dict[str, list[dict[str, Any]]] = {}
        for paper in reference_papers:
            if "source_path" not in paper and reference_json_path:
                paper["source_path"] = str(reference_json_path)
            db_ops._prepare_paper_matching_fields(paper)
        if reference_papers:
            reference_index = db_ops._build_paper_index(reference_papers)

        all_results: list[Any] = []

        if target_json:
            target_json_path = Path(target_json)
            if not target_json_path.is_file():
                raise click.ClickException(f"Target JSON not found: {target_json_path}")
            papers, payload = load_json_payload(target_json_path)

            results: list[Any] = []
            matched_indices: set[int]
            if pdf_root_paths or md_root_paths:
                results, match_pairs, _, _ = db_ops.compare_datasets_with_pairs(
                    json_paths_a=[target_json_path],
                    pdf_roots_b=pdf_root_paths,
                    md_roots_b=md_root_paths,
                    bibtex_path=None,
                    lang=None,
                    show_progress=True,
                )
                matched_indices = {idx_a for idx_a, _, _, _ in match_pairs}
                all_results.extend(results)
            else:
                matched_indices = set(range(len(papers)))

            matched_reference_ids: set[int] = set()
            if reference_index:

                def detail_score(paper: dict[str, Any]) -> tuple[int, int]:
                    non_empty = 0
                    total_len = 0
                    for value in paper.values():
                        if value is None:
                            continue
                        if isinstance(value, (list, dict)):
                            if value:
                                non_empty += 1
                                total_len += len(
                                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                                )
                        else:
                            text = str(value).strip()
                            if text:
                                non_empty += 1
                                total_len += len(text)
                    return non_empty, total_len

                def resolve_reference_match(
                    paper: dict[str, Any],
                ) -> tuple[dict[str, Any] | None, str | None, float]:
                    match_paper, match_type, match_score = db_ops._resolve_paper_by_title_and_meta(
                        paper, reference_index
                    )
                    if match_paper is not None:
                        return match_paper, match_type, match_score
                    year = str(paper.get("_year") or "").strip()
                    if not year.isdigit():
                        return None, None, 0.0
                    authors = paper.get("_authors") or []
                    author_key = ""
                    if authors:
                        author_key = db_ops._normalize_author_key(str(authors[0]))
                    candidates: list[dict[str, Any]] = []
                    fallback_type = "year_relaxed"
                    if author_key:
                        candidates = reference_index.get(f"authoryear:{year}:{author_key}", [])
                        if candidates:
                            fallback_type = "author_year_relaxed"
                    if not candidates:
                        candidates = reference_index.get(f"year:{year}", [])
                    if not candidates:
                        return None, None, 0.0
                    title_key = db_ops._normalize_title_key(str(paper.get("paper_title") or ""))
                    match, score = db_ops._adaptive_similarity_match_papers(title_key, candidates)
                    if match is None:
                        return candidates[0], fallback_type, 0.0
                    return match, fallback_type, score

                base_indices = set(matched_indices)
                best_matches: dict[int, tuple[int, tuple[int, int], str | None, float]] = {}
                for idx, paper in enumerate(papers):
                    if idx not in matched_indices:
                        continue
                    db_ops._prepare_paper_matching_fields(paper)
                    match_paper, match_type, match_score = resolve_reference_match(paper)
                    if match_paper is None:
                        continue
                    ref_id = id(match_paper)
                    score = detail_score(paper)
                    current = best_matches.get(ref_id)
                    if current is None:
                        best_matches[ref_id] = (idx, score, match_type, match_score)
                        continue
                    if score > current[1] or (score == current[1] and match_score > current[3]):
                        best_matches[ref_id] = (idx, score, match_type, match_score)

                matched_reference_ids = set(best_matches.keys())
                matched_indices = {idx for idx, *_ in best_matches.values()}

            matched_papers = [paper for idx, paper in enumerate(papers) if idx in matched_indices]
            deduped_papers: list[Any] = []
            seen_titles: set[str] = set()
            for paper in matched_papers:
                title_key = db_ops._normalize_title_key(str(paper.get("paper_title") or ""))
                if title_key:
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                deduped_papers.append(paper)
            if len(deduped_papers) != len(matched_papers):
                removed = len(matched_papers) - len(deduped_papers)
                click.echo(f"Deduplicated {removed} entries by normalized title.")
            matched_papers = deduped_papers
            output_path = Path(output_json) if output_json else None
            if output_path is None:
                raise click.ClickException("--output-json is required when using --json")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if payload is None:
                write_json(output_path, matched_papers)
            else:
                output_payload = dict(payload)
                output_payload["papers"] = matched_papers
                write_json(output_path, output_payload)
            click.echo(f"Extracted {len(matched_papers)} JSON entries to {output_path}")

            if output_csv and reference_papers:
                match_meta_by_ref_id = {
                    ref_id: (idx, match_type, match_score)
                    for ref_id, (idx, _, match_type, match_score) in best_matches.items()
                }
                for ref in reference_papers:
                    ref_id = id(ref)
                    ref_title = str(ref.get("paper_title") or "")
                    ref_hash = stable_hash(str(ref_title or ref.get("source_path") or ""))
                    ref_path = str(ref.get("source_path") or "")
                    if ref_id in match_meta_by_ref_id:
                        idx, match_type, match_score = match_meta_by_ref_id[ref_id]
                        paper = papers[idx]
                        paper_hash = str(paper.get("source_hash") or "") or stable_hash(
                            str(paper.get("paper_title") or "")
                        )
                        all_results.append(
                            db_ops.CompareResult(
                                side="MATCH",
                                source_hash=ref_hash,
                                title=ref_title,
                                match_status="matched_pair",
                                match_type=match_type,
                                match_score=match_score,
                                source_path=ref_path,
                                other_source_hash=paper_hash,
                                other_title=str(paper.get("paper_title") or ""),
                                other_source_path=str(paper.get("source_path") or ""),
                                lang=None,
                            )
                        )
                        continue
                    all_results.append(
                        db_ops.CompareResult(
                            side="B",
                            source_hash=ref_hash,
                            title=ref_title,
                            match_status="only_in_B",
                            match_type=None,
                            match_score=0.0,
                            source_path=ref_path,
                            other_source_hash=None,
                            other_title=None,
                            other_source_path=None,
                            lang=None,
                        )
                    )

                for idx in sorted(base_indices - matched_indices):
                    paper = papers[idx]
                    paper_title = str(paper.get("paper_title") or "")
                    paper_hash = str(paper.get("source_hash") or "") or stable_hash(paper_title)
                    all_results.append(
                        db_ops.CompareResult(
                            side="A",
                            source_hash=paper_hash,
                            title=paper_title,
                            match_status="only_in_A",
                            match_type=None,
                            match_score=0.0,
                            source_path=str(paper.get("source_path") or ""),
                            other_source_hash=None,
                            other_title=None,
                            other_source_path=None,
                            lang=None,
                        )
                    )

        copied_count = 0
        if md_translated_roots:
            output_root = Path(output_md_translated_root) if output_md_translated_root else None
            if output_root is None:
                raise click.ClickException(
                    "--output-md-translated-root is required when using --md-translated-root"
                )
            results, match_pairs, dataset_a, _ = db_ops.compare_datasets_with_pairs(
                md_translated_roots_a=translated_root_paths,
                pdf_roots_b=pdf_root_paths,
                md_roots_b=md_root_paths,
                lang=lang,
                show_progress=True,
            )
            matched_indices = {idx_a for idx_a, _, _, _ in match_pairs}
            copy_iter = tqdm(
                enumerate(dataset_a.papers),
                total=len(dataset_a.papers),
                desc="copy translated",
                unit="file",
            )
            for idx, paper in copy_iter:
                if idx not in matched_indices:
                    continue
                source_path = paper.get("source_path")
                if not source_path:
                    continue
                source = Path(str(source_path))
                relative = resolve_relative_path(source, translated_root_paths)
                destination = output_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_count += 1
            click.echo(f"Copied {copied_count} translated Markdown files to {output_root}")
            all_results.extend(results)

        if md_source_roots:
            output_root = Path(output_md_root) if output_md_root else None
            if output_root is None:
                raise click.ClickException(
                    "--output-md-root is required when using --md-source-root"
                )
            results, match_pairs, dataset_a, _ = db_ops.compare_datasets_with_pairs(
                md_roots_a=source_root_paths,
                pdf_roots_b=pdf_root_paths,
                md_roots_b=md_root_paths,
                lang=None,
                show_progress=True,
            )
            matched_indices = {idx_a for idx_a, _, _, _ in match_pairs}
            copied_source = 0
            copy_iter = tqdm(
                enumerate(dataset_a.papers),
                total=len(dataset_a.papers),
                desc="copy source",
                unit="file",
            )
            for idx, paper in copy_iter:
                if idx not in matched_indices:
                    continue
                source_path = paper.get("source_path")
                if not source_path:
                    continue
                source = Path(str(source_path))
                relative = resolve_relative_path(source, source_root_paths)
                destination = output_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_source += 1
            click.echo(f"Copied {copied_source} source Markdown files to {output_root}")
            copied_count += copied_source
            all_results.extend(results)

        if output_csv:
            output_path = Path(output_csv)
            export_compare_csv(all_results, output_path)
            click.echo(f"Results exported to: {output_path}")

    @db_group.command("verify")
    @click.option("--input-json", "input_json", required=True, help="Input JSON file path")
    @click.option(
        "--output-json",
        "output_json",
        required=True,
        help="Output verification report JSON path",
    )
    @click.option(
        "--prompt-template",
        "prompt_template",
        default=None,
        type=click.Choice(list_template_names()),
        help="Prompt template to load schema (e.g., deep_read)",
    )
    @click.option(
        "-s",
        "--schema-json",
        "--schema",
        "schema_json",
        default=None,
        help="Custom schema JSON path",
    )
    @click.option(
        "--ignore-field",
        "ignore_fields",
        multiple=True,
        help="Schema field to ignore when checking empties (repeatable)",
    )
    def verify(
        input_json: str,
        output_json: str,
        prompt_template: str | None,
        schema_json: str | None,
        ignore_fields: tuple[str, ...],
    ) -> None:
        if prompt_template and schema_json:
            raise click.ClickException("Use only one of --prompt-template or --schema-json")
        if not prompt_template and not schema_json:
            raise click.ClickException("Provide --prompt-template or --schema-json")

        input_path = Path(input_json)
        if not input_path.is_file():
            raise click.ClickException(f"Input JSON not found: {input_path}")

        papers, payload = load_json_payload(input_path)
        template_tag = (
            prompt_template
            or (payload.get("template_tag") if isinstance(payload, dict) else None)
            or "custom"
        )

        try:
            if schema_json:
                schema = load_schema(schema_json)
            else:
                schema = load_schema_for_template(prompt_template or template_tag)
        except SchemaError as exc:
            raise click.ClickException(str(exc)) from exc
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        ignore_set = {field.strip() for field in ignore_fields if field.strip()}
        properties = schema.get("properties", {})
        schema_fields = sorted(
            field
            for field in (set(properties.keys()) | set(schema.get("required", [])))
            if field not in ignore_set
        )
        if not schema_fields:
            raise click.ClickException("Schema does not define any properties")

        stage_defs = get_stage_definitions(prompt_template or template_tag)
        field_stage_map: dict[str, str] = {}
        for stage_def in stage_defs:
            for field in stage_def.fields:
                if field in ignore_set:
                    continue
                field_stage_map.setdefault(field, stage_def.name)

        report_items: list[dict[str, Any]] = []
        for paper in papers:
            if not isinstance(paper, dict):
                continue
            missing_fields = [
                field
                for field in schema_fields
                if field not in paper or is_empty_value(paper.get(field))
            ]
            if not missing_fields:
                continue
            item: dict[str, Any] = {
                "source_path": str(paper.get("source_path") or ""),
                "paper_title": str(paper.get("paper_title") or ""),
                "missing_fields": missing_fields,
            }
            if field_stage_map and all(field in field_stage_map for field in missing_fields):
                item["retry_stages"] = sorted({field_stage_map[field] for field in missing_fields})
            report_items.append(item)

        report_payload = {
            "template_tag": template_tag,
            "schema_fields": schema_fields,
            "items": report_items,
        }

        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, report_payload)

        console = Console()
        total_missing = sum(len(item["missing_fields"]) for item in report_items)
        summary_table = Table(title="db verify summary")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Value", style="white", overflow="fold")
        summary_table.add_row("Input", str(input_path))
        summary_table.add_row("Template", template_tag)
        summary_table.add_row("Items", str(len(papers)))
        summary_table.add_row("Items with missing fields", str(len(report_items)))
        summary_table.add_row("Total missing fields", str(total_missing))
        if ignore_set:
            summary_table.add_row("Ignored fields", ", ".join(sorted(ignore_set)))
        summary_table.add_row("Output", str(output_path))
        console.print(summary_table)

        if report_items:
            field_counts: dict[str, int] = {field: 0 for field in schema_fields}
            for item in report_items:
                for field in item["missing_fields"]:
                    field_counts[field] = field_counts.get(field, 0) + 1

            count_table = Table(title="Missing field counts")
            count_table.add_column("Field", style="cyan")
            count_table.add_column("Missing", style="yellow", justify="right")
            for field, count in sorted(field_counts.items(), key=lambda x: (-x[1], x[0])):
                if count:
                    count_table.add_row(field, str(count))
            console.print(count_table)

            detail_table = Table(title="Missing field details")
            detail_table.add_column("#", style="dim", justify="right")
            detail_table.add_column("Title", style="white", overflow="fold")
            detail_table.add_column("Source Path", style="cyan", overflow="fold")
            detail_table.add_column("Missing Fields", style="yellow", overflow="fold")
            detail_table.add_column("Retry Stages", style="green", overflow="fold")
            for idx, item in enumerate(report_items, start=1):
                retry_stages = item.get("retry_stages") or []
                detail_table.add_row(
                    str(idx),
                    item.get("paper_title") or "",
                    item.get("source_path") or "",
                    ", ".join(item.get("missing_fields", [])),
                    ", ".join(retry_stages),
                )
            console.print(detail_table)
        else:
            console.print(Panel("[green]No missing fields detected.[/green]", expand=False))

    @db_group.command("transfer-pdfs")
    @click.option(
        "--input-list", "input_list", required=True, help="Text file containing PDF paths"
    )
    @click.option("--output-dir", "output_dir", required=True, help="Output directory")
    @click.option("--move", "move_files", is_flag=True, help="Move PDFs instead of copying")
    @click.option("--copy", "copy_files", is_flag=True, help="Copy PDFs instead of moving")
    def transfer_pdfs(
        input_list: str,
        output_dir: str,
        move_files: bool,
        copy_files: bool,
    ) -> None:
        if move_files == copy_files:
            raise click.ClickException("Specify exactly one of --move or --copy")

        list_path = Path(input_list)
        if not list_path.is_file():
            raise click.ClickException(f"Input list not found: {list_path}")

        destination_root = Path(output_dir)
        destination_root.mkdir(parents=True, exist_ok=True)

        entries = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines()]
        entries = [line for line in entries if line]

        processed = 0
        missing = 0
        transfer_iter = tqdm(entries, total=len(entries), desc="transfer pdfs", unit="file")
        for raw in transfer_iter:
            source = Path(raw).expanduser()
            if not source.is_file():
                missing += 1
                continue
            destination = destination_root / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if move_files:
                shutil.move(str(source), str(destination))
            else:
                shutil.copy2(source, destination)
            processed += 1

        action = "Moved" if move_files else "Copied"
        click.echo(f"{action} {processed} PDFs to {destination_root}")
        if missing:
            click.echo(f"Skipped {missing} missing paths")

    @db_group.command("compare")
    @click.option(
        "-ia",
        "--input-a",
        "input_paths_a",
        multiple=True,
        help="Input JSON files for side A (repeatable)",
    )
    @click.option(
        "-ib",
        "--input-b",
        "input_paths_b",
        multiple=True,
        help="Input JSON files for side B (repeatable)",
    )
    @click.option(
        "--pdf-root-a",
        "pdf_roots_a",
        multiple=True,
        help="PDF root directories for side A (repeatable)",
    )
    @click.option(
        "--pdf-root-b",
        "pdf_roots_b",
        multiple=True,
        help="PDF root directories for side B (repeatable)",
    )
    @click.option(
        "--md-root-a",
        "md_roots_a",
        multiple=True,
        help="Markdown root directories for side A (repeatable)",
    )
    @click.option(
        "--md-root-b",
        "md_roots_b",
        multiple=True,
        help="Markdown root directories for side B (repeatable)",
    )
    @click.option(
        "--md-translated-root-a",
        "md_translated_roots_a",
        multiple=True,
        help="Translated Markdown root directories for side A (repeatable)",
    )
    @click.option(
        "--md-translated-root-b",
        "md_translated_roots_b",
        multiple=True,
        help="Translated Markdown root directories for side B (repeatable)",
    )
    @click.option("-b", "--bibtex", "bibtex_path", default=None, help="Optional BibTeX file path")
    @click.option(
        "--lang", "lang", default=None, help="Language code for translated comparisons (e.g., zh)"
    )
    @click.option("--output-csv", "output_csv", default=None, help="Path to export results as CSV")
    @click.option(
        "--output-only-in-b",
        "output_only_in_b",
        default=None,
        help="Path to export only-in-B source paths as a newline list",
    )
    @click.option(
        "--sample-limit",
        "sample_limit",
        default=5,
        type=int,
        show_default=True,
        help="Number of sample items to show in terminal output",
    )
    def compare(
        input_paths_a: tuple[str, ...],
        input_paths_b: tuple[str, ...],
        pdf_roots_a: tuple[str, ...],
        pdf_roots_b: tuple[str, ...],
        md_roots_a: tuple[str, ...],
        md_roots_b: tuple[str, ...],
        md_translated_roots_a: tuple[str, ...],
        md_translated_roots_b: tuple[str, ...],
        bibtex_path: str | None,
        lang: str | None,
        output_csv: str | None,
        output_only_in_b: str | None,
        sample_limit: int,
    ) -> None:
        """Compare two datasets and report matches and differences."""
        from deepresearch_flow.paper.db_ops import compare_datasets

        # Validate that at least one input is provided for each side
        has_input_a = bool(input_paths_a or pdf_roots_a or md_roots_a or md_translated_roots_a)
        has_input_b = bool(input_paths_b or pdf_roots_b or md_roots_b or md_translated_roots_b)

        if not has_input_a:
            raise click.ClickException(
                "Side A must have at least one input: --input-a, --pdf-root-a, --md-root-a, or --md-translated-root-a"
            )
        if not has_input_b:
            raise click.ClickException(
                "Side B must have at least one input: --input-b, --pdf-root-b, --md-root-b, or --md-translated-root-b"
            )
        if (md_translated_roots_a or md_translated_roots_b) and not lang:
            raise click.ClickException(
                "--lang is required when comparing translated Markdown datasets"
            )

        # Run comparison
        try:
            results = compare_datasets(
                json_paths_a=[Path(p) for p in input_paths_a],
                pdf_roots_a=[Path(p) for p in pdf_roots_a],
                md_roots_a=[Path(p) for p in md_roots_a],
                md_translated_roots_a=[Path(p) for p in md_translated_roots_a],
                json_paths_b=[Path(p) for p in input_paths_b],
                pdf_roots_b=[Path(p) for p in pdf_roots_b],
                md_roots_b=[Path(p) for p in md_roots_b],
                md_translated_roots_b=[Path(p) for p in md_translated_roots_b],
                bibtex_path=Path(bibtex_path) if bibtex_path else None,
                lang=lang,
                show_progress=True,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        # Calculate statistics
        total_a = sum(1 for r in results if r.side == "A")
        total_b = sum(1 for r in results if r.side == "B")
        matched = sum(1 for r in results if r.side == "MATCH")
        only_in_a = sum(1 for r in results if r.side == "A" and r.match_status == "only_in_A")
        only_in_b = sum(1 for r in results if r.side == "B" and r.match_status == "only_in_B")

        console = Console()

        # Print summary table
        summary_table = Table(title="Comparison Summary")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Count", style="green", justify="right")
        summary_table.add_row("Total in A", str(total_a))
        summary_table.add_row("Total in B", str(total_b))
        summary_table.add_row("Matched", str(matched))
        summary_table.add_row("Only in A", str(only_in_a))
        summary_table.add_row("Only in B", str(only_in_b))
        console.print(summary_table)

        # Print match type breakdown
        match_types: dict[str, int] = {}
        for r in results:
            if r.side == "MATCH" and r.match_type:
                match_types[r.match_type] = match_types.get(r.match_type, 0) + 1

        if match_types:
            type_table = Table(title="Match Types")
            type_table.add_column("Type", style="cyan")
            type_table.add_column("Count", style="green", justify="right")
            for match_type, count in sorted(match_types.items(), key=lambda x: x[1], reverse=True):
                type_table.add_row(match_type, str(count))
            console.print(type_table)

        # Print sample results
        console.print("\n[bold]Sample Results:[/bold]")

        # Sample matched items
        matched_samples = [r for r in results if r.side == "MATCH"][:sample_limit]
        if matched_samples:
            console.print("\n[green]Matched Items:[/green]")
            for r in matched_samples:
                left = (r.title or "")[:60]
                right = (r.other_title or "")[:60]
                console.print(
                    f"  • {left} ↔ {right} (type: {r.match_type}, score: {r.match_score:.2f})"
                )

        # Sample only in A
        only_a_samples = [r for r in results if r.side == "A" and r.match_status == "only_in_A"][
            :sample_limit
        ]
        if only_a_samples:
            console.print("\n[yellow]Only in A:[/yellow]")
            for r in only_a_samples:
                console.print(f"  • {r.title[:60]}...")

        # Sample only in B
        only_b_samples = [r for r in results if r.side == "B" and r.match_status == "only_in_B"][
            :sample_limit
        ]
        if only_b_samples:
            console.print("\n[yellow]Only in B:[/yellow]")
            for r in only_b_samples:
                console.print(f"  • {r.title[:60]}...")

        # Export to CSV if requested
        if output_csv:
            output_path = Path(output_csv)
            export_compare_csv(results, output_path)
            console.print(f"\n[green]Results exported to: {output_path}[/green]")
        if output_only_in_b:
            output_path = Path(output_only_in_b)
            count = export_only_in_b_paths(results, output_path)
            console.print(
                f"\n[green]Only-in-B list exported ({count} items): {output_path}[/green]"
            )

        # Print final counts
        console.print(f"\nTotal results: {len(results)}")
