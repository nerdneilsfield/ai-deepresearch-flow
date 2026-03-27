"""Push papers from a local snapshot DB to a remote admin API."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from deepresearch_flow.paper.snapshot.common import _open_ro_conn

DEFAULT_BATCH_SIZE = 100
DEFAULT_TIMEOUT = 60.0


@dataclass(frozen=True)
class RemoteConfig:
    api_base_url: str
    admin_token: str
    batch_size: int = DEFAULT_BATCH_SIZE


@dataclass
class PushStats:
    total: int = 0
    added: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    paper_ids: list[str] = field(default_factory=list)
    batches_sent: int = 0


def load_remote_config(config_path: Path) -> RemoteConfig:
    """Load remote configuration from a TOML file.

    Supports ``env:VAR_NAME`` syntax for ``admin_token``.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib  # type: ignore[no-redef]

    text = config_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    remote = data.get("remote", {})

    api_base_url = str(remote.get("api_base_url") or "").rstrip("/")
    if not api_base_url:
        raise ValueError(f"remote.api_base_url is required in {config_path}")

    admin_token = str(remote.get("admin_token") or "")
    if admin_token.startswith("env:"):
        env_name = admin_token.split(":", 1)[1]
        admin_token = os.environ.get(env_name, "")
    if not admin_token:
        raise ValueError(
            f"remote.admin_token is required in {config_path} "
            "(use 'env:VAR_NAME' to read from environment)"
        )

    batch_size = int(remote.get("batch_size", DEFAULT_BATCH_SIZE))
    if batch_size < 1 or batch_size > 200:
        raise ValueError(
            f"remote.batch_size must be between 1 and 200, got {batch_size}"
        )
    return RemoteConfig(
        api_base_url=api_base_url,
        admin_token=admin_token,
        batch_size=batch_size,
    )


# ---------------------------------------------------------------------------
# Extract papers from local snapshot DB
# ---------------------------------------------------------------------------

def _fetch_facet_values(
    conn: sqlite3.Connection,
    paper_id: str,
    table: str,
    id_col: str,
    join_table: str,
) -> list[str]:
    rows = conn.execute(
        f"SELECT t.value FROM {table} t "
        f"JOIN {join_table} j ON t.{id_col} = j.{id_col} "
        f"WHERE j.paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [str(row["value"]) for row in rows]


def _fetch_bibtex(conn: sqlite3.Connection, paper_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT bibtex_raw, bibtex_key, entry_type FROM paper_bibtex WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "raw": row["bibtex_raw"],
        "key": row["bibtex_key"],
        "type": row["entry_type"],
    }


def _fetch_translations(conn: sqlite3.Connection, paper_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT lang, md_content_hash FROM paper_translation WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return {str(row["lang"]): str(row["md_content_hash"]) for row in rows}


def _fetch_template_tags(conn: sqlite3.Connection, paper_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT template_tag FROM paper_summary WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()
    return [str(row["template_tag"]) for row in rows]


def _load_summary_payloads(
    paper_id: str,
    template_tags: list[str],
    static_export_dir: Path | None,
) -> dict[str, dict[str, Any]]:
    """Load summary JSON payloads from static export directory."""
    if not static_export_dir:
        return {}
    templates: dict[str, dict[str, Any]] = {}
    for tag in template_tags:
        path = static_export_dir / "summary" / paper_id / f"{tag}.json"
        if path.exists():
            try:
                templates[tag] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    return templates


def extract_papers_from_db(
    db_path: Path,
    *,
    static_export_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Extract all papers from a snapshot DB as dicts suitable for the admin API."""
    conn = _open_ro_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT paper_id, paper_key, paper_key_type, doi, title, year, month,
                   publication_date, venue, preferred_summary_template, summary_preview,
                   source_hash, output_language, provider, model, prompt_template,
                   extracted_at, pdf_content_hash, source_md_content_hash
            FROM paper
            ORDER BY paper_id
            """
        ).fetchall()

        papers: list[dict[str, Any]] = []
        for row in rows:
            paper_id = str(row["paper_id"])
            template_tags = _fetch_template_tags(conn, paper_id)
            templates = _load_summary_payloads(paper_id, template_tags, static_export_dir)

            paper: dict[str, Any] = {
                "paper_id": paper_id,
                "paper_title": row["title"],
                "paper_authors": _fetch_facet_values(conn, paper_id, "author", "author_id", "paper_author"),
                "keywords": _fetch_facet_values(conn, paper_id, "keyword", "keyword_id", "paper_keyword"),
                "paper_institutions": _fetch_facet_values(conn, paper_id, "institution", "institution_id", "paper_institution"),
                "ai_generated_tags": _fetch_facet_values(conn, paper_id, "tag", "tag_id", "paper_tag"),
                "publication_venue": row["venue"] or "",
                "publication_date": row["publication_date"] or "",
                "source_hash": row["source_hash"] or "",
                "output_language": row["output_language"] or "",
                "provider": row["provider"] or "",
                "model": row["model"] or "",
                "prompt_template": row["prompt_template"] or "",
                "extracted_at": row["extracted_at"] or "",
                "pdf_content_hash": row["pdf_content_hash"] or "",
                "source_md_content_hash": row["source_md_content_hash"] or "",
                "summary_preview": row["summary_preview"] or "",
            }

            if row["doi"]:
                paper["doi"] = row["doi"]

            bib = _fetch_bibtex(conn, paper_id)
            if bib:
                paper["bibtex"] = bib

            translations = _fetch_translations(conn, paper_id)
            if translations:
                paper["translations"] = translations

            if templates:
                paper["templates"] = templates

            papers.append(paper)

        return papers
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Push to remote
# ---------------------------------------------------------------------------

def push_papers(
    papers: list[dict[str, Any]],
    config: RemoteConfig,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    on_batch: Any | None = None,
) -> PushStats:
    """Push papers to the remote admin API in batches.

    Args:
        papers: Paper dicts to push.
        config: Remote API configuration.
        timeout: HTTP request timeout in seconds.
        on_batch: Optional callback ``(batch_index, batch_size, response_data)``
                  called after each successful batch.

    Returns:
        Aggregated push statistics.
    """
    stats = PushStats(total=len(papers))
    url = f"{config.api_base_url}/api/v1/admin/papers"
    headers = {
        "Authorization": f"Bearer {config.admin_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        for batch_idx in range(0, len(papers), config.batch_size):
            batch = papers[batch_idx : batch_idx + config.batch_size]
            resp = client.post(url, json={"papers": batch}, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            stats.added += data.get("added", 0)
            stats.skipped += data.get("skipped", 0)
            stats.errors.extend(data.get("errors", []))
            stats.paper_ids.extend(data.get("paper_ids", []))
            stats.batches_sent += 1

            if on_batch:
                on_batch(batch_idx // config.batch_size, len(batch), data)

    return stats
