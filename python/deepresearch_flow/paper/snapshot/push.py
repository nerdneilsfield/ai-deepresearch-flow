"""Push papers from a local snapshot DB to a remote admin API."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from deepresearch_flow.paper.snapshot.common import _open_ro_conn
from deepresearch_flow.storage.config import StorageConfig

DEFAULT_BATCH_SIZE = 10
DEFAULT_TIMEOUT = 60.0
DEFAULT_SEMANTIC_MAX_ROWS = 25
DEFAULT_SEMANTIC_MAX_PAYLOAD_BYTES = 4_000_000
DEFAULT_SEMANTIC_TIMEOUT = 120.0
DEFAULT_SEMANTIC_RETRIES = 3
DEFAULT_SEMANTIC_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_PUSH_RETRIES = 2
DEFAULT_PUSH_RETRY_BACKOFF_SECONDS = 1.0
_LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


@dataclass(frozen=True)
class RemoteSemanticConfig:
    max_rows: int = DEFAULT_SEMANTIC_MAX_ROWS
    max_payload_bytes: int = DEFAULT_SEMANTIC_MAX_PAYLOAD_BYTES
    timeout: float = DEFAULT_SEMANTIC_TIMEOUT
    retries: int = DEFAULT_SEMANTIC_RETRIES
    retry_backoff_seconds: float = DEFAULT_SEMANTIC_RETRY_BACKOFF_SECONDS


@dataclass(frozen=True)
class RemoteConfig:
    api_base_url: str
    admin_token: str
    batch_size: int = DEFAULT_BATCH_SIZE
    storage: StorageConfig | None = None
    semantic: RemoteSemanticConfig = field(default_factory=RemoteSemanticConfig)


def _requires_secure_transport(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http":
        return False
    return (parsed.hostname or "").lower() not in _LOCAL_HTTP_HOSTS


def _validate_authenticated_url(url: str) -> None:
    if _requires_secure_transport(url):
        raise ValueError("remote.api_base_url must use HTTPS when sending admin_token outside localhost")


@dataclass
class PushStats:
    total: int = 0
    added: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    paper_ids: list[str] = field(default_factory=list)
    batches_sent: int = 0


def _resolve_env(value: str, field_name: str, config_path: Path) -> str:
    """Resolve env: prefix for a config value."""
    if not value.startswith("env:"):
        return value
    env_name = value.split(":", 1)[1]
    resolved = os.environ.get(env_name, "")
    if not resolved:
        raise ValueError(
            f"Environment variable '{env_name}' is not set "
            f"(referenced as 'env:{env_name}' for {field_name} in {config_path})"
        )
    return resolved


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
    _validate_authenticated_url(api_base_url)

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
    storage_raw = remote.get("storage")
    storage: StorageConfig | None = None
    if storage_raw:
        s_type = str(storage_raw.get("type") or "")
        if not s_type:
            raise ValueError(f"remote.storage.type is required in {config_path}")
        s_url = str(storage_raw.get("url") or "").rstrip("/")
        if not s_url:
            raise ValueError(f"remote.storage.url is required in {config_path}")
        s_user = str(storage_raw.get("username") or "")
        if not s_user:
            raise ValueError(f"remote.storage.username is required in {config_path}")
        s_pass = str(storage_raw.get("password") or "")
        if not s_pass:
            raise ValueError(f"remote.storage.password is required in {config_path}")
        s_pass = _resolve_env(s_pass, "remote.storage.password", config_path)
        storage = StorageConfig(type=s_type, url=s_url, username=s_user, password=s_pass)

    semantic_raw = remote.get("semantic") or {}
    if not isinstance(semantic_raw, dict):
        raise ValueError(f"remote.semantic must be a table in {config_path}")

    semantic_max_rows = int(semantic_raw.get("max_rows", DEFAULT_SEMANTIC_MAX_ROWS))
    if semantic_max_rows < 1:
        raise ValueError(f"remote.semantic.max_rows must be at least 1, got {semantic_max_rows}")

    semantic_max_payload_bytes = int(
        semantic_raw.get("max_payload_bytes", DEFAULT_SEMANTIC_MAX_PAYLOAD_BYTES)
    )
    if semantic_max_payload_bytes < 1024:
        raise ValueError(
            "remote.semantic.max_payload_bytes must be at least 1024, "
            f"got {semantic_max_payload_bytes}"
        )

    semantic_timeout = float(semantic_raw.get("timeout", DEFAULT_SEMANTIC_TIMEOUT))
    if semantic_timeout <= 0:
        raise ValueError(f"remote.semantic.timeout must be > 0, got {semantic_timeout}")

    semantic_retries = int(semantic_raw.get("retries", DEFAULT_SEMANTIC_RETRIES))
    if semantic_retries < 0:
        raise ValueError(f"remote.semantic.retries must be >= 0, got {semantic_retries}")

    semantic_retry_backoff_seconds = float(
        semantic_raw.get("retry_backoff_seconds", DEFAULT_SEMANTIC_RETRY_BACKOFF_SECONDS)
    )
    if semantic_retry_backoff_seconds < 0:
        raise ValueError(
            "remote.semantic.retry_backoff_seconds must be >= 0, "
            f"got {semantic_retry_backoff_seconds}"
        )

    return RemoteConfig(
        api_base_url=api_base_url,
        admin_token=admin_token,
        batch_size=batch_size,
        storage=storage,
        semantic=RemoteSemanticConfig(
            max_rows=semantic_max_rows,
            max_payload_bytes=semantic_max_payload_bytes,
            timeout=semantic_timeout,
            retries=semantic_retries,
            retry_backoff_seconds=semantic_retry_backoff_seconds,
        ),
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


def _should_retry_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code < 600


def _post_batch_with_retries(
    client: httpx.Client,
    url: str,
    batch: list[dict[str, Any]],
    headers: dict[str, str],
    *,
    retries: int,
    retry_backoff_seconds: float,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            resp = client.post(url, json={"papers": batch}, headers=headers)
            if attempt < retries and _should_retry_status(resp.status_code):
                attempt += 1
                sleep_fn(retry_backoff_seconds * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise ValueError("admin API response must be a JSON object")
        except httpx.TransportError:
            if attempt >= retries:
                raise
            attempt += 1
            sleep_fn(retry_backoff_seconds * attempt)

def push_papers(
    papers: list[dict[str, Any]],
    config: RemoteConfig,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    on_batch: Any | None = None,
    retries: int = DEFAULT_PUSH_RETRIES,
    retry_backoff_seconds: float = DEFAULT_PUSH_RETRY_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
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
    _validate_authenticated_url(config.api_base_url)
    if retries < 0:
        raise ValueError(f"retries must be >= 0, got {retries}")
    if retry_backoff_seconds < 0:
        raise ValueError(f"retry_backoff_seconds must be >= 0, got {retry_backoff_seconds}")

    stats = PushStats(total=len(papers))
    url = f"{config.api_base_url}/api/v1/admin/papers"
    headers = {
        "Authorization": f"Bearer {config.admin_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        for batch_idx in range(0, len(papers), config.batch_size):
            batch = papers[batch_idx : batch_idx + config.batch_size]
            data = _post_batch_with_retries(
                client,
                url,
                batch,
                headers,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                sleep_fn=sleep_fn,
            )

            stats.added += data.get("added", 0)
            stats.skipped += data.get("skipped", 0)
            stats.errors.extend(data.get("errors", []))
            stats.paper_ids.extend(data.get("paper_ids", []))
            stats.batches_sent += 1

            if on_batch:
                on_batch(batch_idx // config.batch_size, len(batch), data)

    return stats
