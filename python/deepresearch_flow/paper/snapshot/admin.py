"""Admin API for managing papers in the snapshot database.

Provides endpoints under ``/api/v1/admin/`` for adding and deleting papers.
Authentication is via Bearer token in the ``Authorization`` header.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from deepresearch_flow.paper.vector_store import SHARED_TEMPLATE_KEY
from deepresearch_flow.paper.snapshot.bibtex_utils import (
    extract_canonical_doi,
    extract_current_bibtex_payload,
)
from deepresearch_flow.paper.snapshot.common import _open_ro_conn, _open_rw_conn
from deepresearch_flow.paper.snapshot.identity import (
    build_paper_key_candidates,
    choose_preferred_key,
    paper_id_for_key,
)
from deepresearch_flow.paper.snapshot.schema import (
    init_snapshot_db,
    recompute_facet_counts,
    recompute_facet_edges,
    recompute_paper_index,
)
from deepresearch_flow.paper.snapshot.text import insert_cjk_spaces, markdown_to_plain_text
from deepresearch_flow.paper.snapshot.update import (
    _canonical_template_tag,
    _choose_preferred_template,
    _extract_template_markdown,
    _extract_template_payloads,
    _extract_venue,
    _facet_node_id,
    _link_dim,
    _normalize_authors,
    _normalize_str_list,
    _parse_year_month,
    _summary_preview,
)

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 200
_MAX_SEMANTIC_PAYLOAD_BYTES = 32 * 1024 * 1024
_SLOW_SEMANTIC_PHASE_SECONDS = 1.0
_SLOW_SEMANTIC_BATCH_SECONDS = 3.0


@dataclass(frozen=True)
class AdminConfig:
    snapshot_db: Path
    admin_token: str
    embed_db: Path | None = None
    embed_dimensions: int | None = None


def _semantic_tag_label(template_tag: str) -> str:
    return template_tag or SHARED_TEMPLATE_KEY


def _log_semantic_phase(
    doc_id: str, template_tag: str, phase: str, started_at: float, *, detail: str = ""
) -> float:
    elapsed = time.perf_counter() - started_at
    log_fn = logger.warning if elapsed >= _SLOW_SEMANTIC_PHASE_SECONDS else logger.info
    suffix = f" {detail}" if detail else ""
    log_fn(
        "semantic batch phase doc=%s tag=%s phase=%s elapsed_ms=%.1f%s",
        doc_id,
        _semantic_tag_label(template_tag),
        phase,
        elapsed * 1000.0,
        suffix,
    )
    return elapsed


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(request: Request) -> bool:
    """Return ``True`` if the request is authenticated, ``False`` otherwise."""
    cfg: AdminConfig = request.app.state.admin_cfg
    if not cfg.admin_token:
        logger.warning("Admin API request rejected: no token configured")
        return False
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], cfg.admin_token)


_AUTH_ERROR = JSONResponse({"error": "unauthorized"}, status_code=401)


# ---------------------------------------------------------------------------
# Paper identity resolution (slim version from update.py)
# ---------------------------------------------------------------------------


def _resolve_paper_identity(
    conn: sqlite3.Connection, paper: dict[str, Any]
) -> tuple[str, str, str, str | None, list[Any]]:
    explicit_id = str(paper.get("paper_id") or paper.get("id") or "").strip()
    candidates = build_paper_key_candidates(paper)
    for cand in candidates:
        row = conn.execute(
            "SELECT paper_id FROM paper_key_alias WHERE paper_key = ?",
            (cand.paper_key,),
        ).fetchone()
        if row:
            return (
                str(row["paper_id"]),
                cand.paper_key,
                cand.key_type,
                cand.meta_fingerprint,
                candidates,
            )

    preferred = choose_preferred_key(candidates)
    paper_id = explicit_id or paper_id_for_key(preferred.paper_key)
    return paper_id, preferred.paper_key, preferred.key_type, preferred.meta_fingerprint, candidates


# ---------------------------------------------------------------------------
# Metadata-only paper insertion (no static file I/O)
# ---------------------------------------------------------------------------


@dataclass
class _InsertStats:
    added: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    paper_ids: list[str] = field(default_factory=list)


def _insert_paper_metadata(
    conn: sqlite3.Connection,
    paper: dict[str, Any],
    index: int,
    stats: _InsertStats,
) -> None:
    """Insert a single paper's metadata into the database.

    Skips static file operations (PDF, markdown, images).  Populates the paper
    table, facet dimension/join tables, facet graph, and FTS indices.
    """
    title = str(paper.get("paper_title") or "").strip()
    if not title:
        stats.errors.append({"index": index, "error": "missing paper_title"})
        return

    paper_id, paper_key, paper_key_type, _, candidates = _resolve_paper_identity(conn, paper)

    existing = conn.execute("SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)).fetchone()
    if existing:
        stats.skipped += 1
        return

    year, month = _parse_year_month(paper)
    publication_date = str(paper.get("publication_date") or "").strip()
    venue = _extract_venue(paper)

    bib = paper.get("bibtex") if isinstance(paper.get("bibtex"), dict) else None
    bib_fields = (
        bib.get("fields") if isinstance(bib, dict) and isinstance(bib.get("fields"), dict) else {}
    )
    doi = extract_canonical_doi(paper, bib_fields or {})
    bibtex_raw, bibtex_key, entry_type, _ = extract_current_bibtex_payload(paper)
    if paper_key_type == "bib" and paper_key.startswith("bib:") and not bibtex_key:
        bibtex_key = paper_key.split(":", 1)[1]

    output_language = str(paper.get("output_language") or "").strip()
    provider = str(paper.get("provider") or "").strip()
    model = str(paper.get("model") or "").strip()
    prompt_template = str(paper.get("prompt_template") or paper.get("template_tag") or "").strip()
    extracted_at = str(paper.get("extracted_at") or "").strip()

    source_hash = str(paper.get("source_hash") or "").strip()
    pdf_content_hash = str(paper.get("pdf_content_hash") or "").strip()
    source_md_content_hash = str(paper.get("source_md_content_hash") or "").strip()

    has_real_templates = isinstance(paper.get("templates"), dict) and any(
        isinstance(v, dict) and v for v in paper["templates"].values()
    )
    template_payloads = _extract_template_payloads(paper)
    preferred_summary_template = _choose_preferred_template(paper, template_payloads)
    preferred_markdown = _extract_template_markdown(
        template_payloads.get(preferred_summary_template, {})
    )

    # Prefer the preview carried from source DB when no real template content
    # exists (avoids polluting preview/FTS with raw JSON dumps of the paper dict)
    incoming_preview = str(paper.get("summary_preview") or "").strip()
    if has_real_templates:
        summary_preview = _summary_preview(preferred_markdown)
    else:
        summary_preview = incoming_preview

    conn.execute(
        """
        INSERT INTO paper (
            paper_id, paper_key, paper_key_type, doi, title, year, month,
            publication_date, venue, preferred_summary_template, summary_preview,
            source_hash, output_language, provider, model, prompt_template,
            extracted_at, pdf_content_hash, source_md_content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            paper_key,
            paper_key_type,
            doi,
            title,
            year,
            month,
            publication_date,
            venue,
            preferred_summary_template,
            summary_preview,
            source_hash,
            output_language,
            provider,
            model,
            prompt_template,
            extracted_at,
            pdf_content_hash,
            source_md_content_hash,
        ),
    )

    # BibTeX
    if bibtex_raw:
        conn.execute(
            "INSERT OR REPLACE INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type) VALUES (?, ?, ?, ?)",
            (paper_id, bibtex_raw, bibtex_key, entry_type),
        )

    # Paper key aliases
    for cand in candidates:
        conn.execute(
            "INSERT OR REPLACE INTO paper_key_alias(paper_key, paper_id, paper_key_type, meta_fingerprint) VALUES (?, ?, ?, ?)",
            (
                cand.paper_key,
                paper_id,
                cand.key_type,
                cand.meta_fingerprint if cand.key_type == "meta" else None,
            ),
        )

    # Summary templates (metadata only, no JSON file writes)
    ordered_template_tags = sorted(template_payloads.keys(), key=lambda t: t.lower())
    for template_tag in ordered_template_tags:
        conn.execute(
            "INSERT OR IGNORE INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
            (paper_id, template_tag),
        )

    # Translations (from pre-existing data in the paper dict)
    translations = paper.get("translations") or {}
    if isinstance(translations, dict):
        for lang, md_hash in translations.items():
            lang_norm = str(lang or "").strip().lower()
            md_hash_str = str(md_hash or "").strip()
            if lang_norm and md_hash_str:
                conn.execute(
                    "INSERT OR REPLACE INTO paper_translation(paper_id, lang, md_content_hash) VALUES (?, ?, ?)",
                    (paper_id, lang_norm, md_hash_str),
                )

    # Facet dimensions
    authors = _normalize_authors(paper.get("paper_authors"))
    keywords = _normalize_str_list(paper.get("keywords"))
    institutions = _normalize_str_list(paper.get("paper_institutions"))
    tags = _normalize_str_list(paper.get("ai_generated_tags"))

    _link_dim(
        conn,
        paper_id=paper_id,
        table="author",
        id_col="author_id",
        join_table="paper_author",
        join_col="author_id",
        values=authors,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="keyword",
        id_col="keyword_id",
        join_table="paper_keyword",
        join_col="keyword_id",
        values=keywords,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="institution",
        id_col="institution_id",
        join_table="paper_institution",
        join_col="institution_id",
        values=institutions,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="tag",
        id_col="tag_id",
        join_table="paper_tag",
        join_col="tag_id",
        values=tags,
    )
    _link_dim(
        conn,
        paper_id=paper_id,
        table="venue",
        id_col="venue_id",
        join_table="paper_venue",
        join_col="venue_id",
        values=[venue] if venue else [],
    )

    # Facet graph
    graph_nodes: set[int] = set()

    def _add_nodes(facet_type: str, values: list[str] | str | None) -> None:
        if values is None:
            return
        iterable = values if isinstance(values, list) else [values]
        for item in iterable:
            node_id = _facet_node_id(conn, facet_type, str(item) if item is not None else None)
            if node_id is not None:
                graph_nodes.add(node_id)

    _add_nodes("author", authors)
    _add_nodes("keyword", keywords)
    _add_nodes("institution", institutions)
    _add_nodes("tag", tags)
    _add_nodes("venue", venue)
    _add_nodes("year", year)
    _add_nodes("month", month)
    _add_nodes("summary_template", ordered_template_tags)
    _add_nodes("output_language", output_language)
    _add_nodes("provider", provider)
    _add_nodes("model", model)
    _add_nodes("prompt_template", prompt_template)
    translation_langs = list(translations.keys()) if isinstance(translations, dict) else []
    _add_nodes("translation_lang", translation_langs)

    for node_id in graph_nodes:
        conn.execute(
            "INSERT OR IGNORE INTO paper_facet(paper_id, node_id) VALUES (?, ?)",
            (paper_id, node_id),
        )

    node_list = sorted(graph_nodes)
    if len(node_list) > 1:
        edge_rows = [
            (left, right) for idx, left in enumerate(node_list) for right in node_list[idx + 1 :]
        ]
        conn.executemany(
            """
            INSERT INTO facet_edge(node_id_a, node_id_b, paper_count)
            VALUES (?, ?, 1)
            ON CONFLICT(node_id_a, node_id_b)
            DO UPDATE SET paper_count = paper_count + 1
            """,
            edge_rows,
        )

    # FTS indices — use real template content when available, otherwise fall back
    # to summary_preview to avoid indexing JSON dumps of the paper dict
    if has_real_templates:
        summary_text = markdown_to_plain_text(
            " ".join(
                _extract_template_markdown(template_payloads[tag]) for tag in ordered_template_tags
            )
        )
    else:
        summary_text = summary_preview
    metadata_text = " ".join(
        part
        for part in [
            title,
            " ".join(authors),
            venue,
            " ".join(keywords),
            " ".join(institutions),
            year,
            doi or "",
        ]
        if part
    )
    conn.execute(
        "INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        (
            paper_id,
            insert_cjk_spaces(title),
            insert_cjk_spaces(summary_text),
            "",
            "",
            insert_cjk_spaces(metadata_text),
        ),
    )
    conn.execute(
        "INSERT INTO paper_fts_trigram(paper_id, title, venue) VALUES (?, ?, ?)",
        (paper_id, title.lower(), venue.lower()),
    )

    stats.added += 1
    stats.paper_ids.append(paper_id)


# ---------------------------------------------------------------------------
# Delete helper
# ---------------------------------------------------------------------------


def _delete_paper(conn: sqlite3.Connection, paper_id: str) -> bool:
    """Delete a paper and all its cascading relations. Returns True if found."""
    # FTS virtual tables don't support ON DELETE CASCADE
    conn.execute("DELETE FROM paper_fts WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM paper_fts_trigram WHERE paper_id = ?", (paper_id,))

    cursor = conn.execute("DELETE FROM paper WHERE paper_id = ?", (paper_id,))
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def _admin_add_papers(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _AUTH_ERROR

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"error": "bad_request", "detail": "invalid JSON body"}, status_code=400
        )

    papers = body.get("papers") if isinstance(body, dict) else None
    if not isinstance(papers, list):
        return JSONResponse(
            {"error": "bad_request", "detail": 'body must be {"papers": [...]}'},
            status_code=400,
        )

    if len(papers) > MAX_BATCH_SIZE:
        return JSONResponse(
            {"error": "bad_request", "detail": f"batch size exceeds limit ({MAX_BATCH_SIZE})"},
            status_code=400,
        )

    cfg: AdminConfig = request.app.state.admin_cfg
    stats = _InsertStats()
    conn = _open_rw_conn(cfg.snapshot_db)
    try:
        init_snapshot_db(conn)
        for idx, paper in enumerate(papers):
            if not isinstance(paper, dict):
                stats.errors.append({"index": idx, "error": "paper must be an object"})
                continue
            try:
                _insert_paper_metadata(conn, paper, idx, stats)
            except Exception as exc:
                logger.exception("Failed to insert paper at index %d", idx)
                stats.errors.append({"index": idx, "error": str(exc)})

        if stats.added > 0:
            recompute_paper_index(conn)
            recompute_facet_counts(conn)

        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.exception("Admin add papers failed")
        return JSONResponse(
            {"error": "internal_error", "detail": "batch insert failed"},
            status_code=500,
        )
    finally:
        conn.close()

    return JSONResponse(
        {
            "added": stats.added,
            "skipped": stats.skipped,
            "errors": stats.errors,
            "paper_ids": stats.paper_ids,
        }
    )


async def _admin_delete_paper(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _AUTH_ERROR

    paper_id = request.path_params["paper_id"]

    cfg: AdminConfig = request.app.state.admin_cfg
    conn = _open_rw_conn(cfg.snapshot_db)
    try:
        deleted = _delete_paper(conn, paper_id)
        if deleted:
            recompute_paper_index(conn)
            recompute_facet_counts(conn)
            recompute_facet_edges(conn)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.exception("Admin delete paper failed: %s", paper_id)
        return JSONResponse(
            {"error": "internal_error", "detail": "delete operation failed"},
            status_code=500,
        )
    finally:
        conn.close()

    if not deleted:
        return JSONResponse(
            {"error": "not_found", "detail": f"paper '{paper_id}' not found"},
            status_code=404,
        )

    return JSONResponse({"deleted": True, "paper_id": paper_id})


def _staging_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'semantic_staging' LIMIT 1"
    ).fetchone()
    return row is not None


def _ensure_staging_table(cfg: AdminConfig) -> None:
    conn = _open_rw_conn(cfg.snapshot_db)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_staging (
              staging_id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              template_tag TEXT NOT NULL,
              group_hash TEXT NOT NULL,
              part_index INTEGER NOT NULL,
              part_count INTEGER NOT NULL,
              chunk_data TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE(doc_id, template_tag, group_hash, part_index)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _stage_part(
    cfg: AdminConfig,
    doc_id: str,
    template_tag: str,
    group_hash: str,
    part_index: int,
    part_count: int,
    chunks: list[dict[str, Any]],
) -> None:
    conn = _open_rw_conn(cfg.snapshot_db)
    try:
        existing = conn.execute(
            "SELECT group_hash, part_count FROM semantic_staging WHERE doc_id = ? AND template_tag = ? LIMIT 1",
            (doc_id, template_tag),
        ).fetchone()
        if existing and (
            str(existing["group_hash"]) != group_hash or int(existing["part_count"]) != part_count
        ):
            conn.execute(
                "DELETE FROM semantic_staging WHERE doc_id = ? AND template_tag = ?",
                (doc_id, template_tag),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO semantic_staging
            (doc_id, template_tag, group_hash, part_index, part_count, chunk_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                template_tag,
                group_hash,
                part_index,
                part_count,
                json.dumps(chunks, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _count_staged_parts(cfg: AdminConfig, doc_id: str, template_tag: str, group_hash: str) -> int:
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        if not _staging_table_exists(conn):
            return 0
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM semantic_staging WHERE doc_id = ? AND template_tag = ? AND group_hash = ?",
            (doc_id, template_tag, group_hash),
        ).fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def _collect_staged_parts(
    cfg: AdminConfig, doc_id: str, template_tag: str, group_hash: str
) -> list[dict[str, Any]]:
    conn = _open_ro_conn(cfg.snapshot_db)
    try:
        if not _staging_table_exists(conn):
            return []
        rows = conn.execute(
            "SELECT chunk_data FROM semantic_staging WHERE doc_id = ? AND template_tag = ? AND group_hash = ? ORDER BY part_index",
            (doc_id, template_tag, group_hash),
        ).fetchall()
    finally:
        conn.close()
    chunks: list[dict[str, Any]] = []
    for row in rows:
        chunks.extend(json.loads(str(row["chunk_data"])))
    return chunks


def _clear_staged_parts(cfg: AdminConfig, doc_id: str, template_tag: str, group_hash: str) -> None:
    conn = _open_rw_conn(cfg.snapshot_db)
    try:
        if not _staging_table_exists(conn):
            return
        conn.execute(
            "DELETE FROM semantic_staging WHERE doc_id = ? AND template_tag = ? AND group_hash = ?",
            (doc_id, template_tag, group_hash),
        )
        conn.commit()
    finally:
        conn.close()


def _update_index_meta_after_group_write(
    vector_dir: Path,
    *,
    previous_template_keys: set[str],
    template_tag: str,
    previous_group_count: int,
    new_group_count: int,
    had_template_elsewhere: bool,
) -> None:
    from deepresearch_flow.paper.vector_store import load_index_meta, save_index_meta

    meta = load_index_meta(vector_dir)
    meta["chunk_count"] = max(
        0,
        int(meta.get("chunk_count") or 0) + new_group_count - previous_group_count,
    )

    before_doc_present = bool(previous_template_keys)
    current_template_key = template_tag or SHARED_TEMPLATE_KEY
    other_template_keys = {key for key in previous_template_keys if key != current_template_key}
    after_doc_present = bool(other_template_keys) or new_group_count > 0
    doc_count = int(meta.get("doc_count") or 0)
    if not before_doc_present and after_doc_present:
        doc_count += 1
    elif before_doc_present and not after_doc_present:
        doc_count = max(0, doc_count - 1)
    meta["doc_count"] = doc_count

    if template_tag:
        before_template_present = had_template_elsewhere or previous_group_count > 0
        after_template_present = had_template_elsewhere or new_group_count > 0
        template_count = int(meta.get("template_count") or 0)
        if not before_template_present and after_template_present:
            template_count += 1
        elif before_template_present and not after_template_present:
            template_count = max(0, template_count - 1)
        meta["template_count"] = template_count

    meta["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_index_meta(vector_dir, meta)


def _apply_semantic_chunk_batch(
    cfg: AdminConfig,
    *,
    index_meta: dict[str, Any],
    doc_id: str,
    template_tag: str,
    dimensions: int,
    all_chunks: list[dict[str, Any]],
) -> dict[str, int]:
    from deepresearch_flow.paper.vector_store import (
        ChunkRow,
        decode_vector_b64,
        delete_groups,
        open_store,
        read_admin_ingest_state,
        save_index_meta,
        validate_index_meta,
        write_chunks,
    )

    batch_started_at = time.perf_counter()
    meta_path = cfg.embed_db / "index_meta.json"
    phase_started_at = time.perf_counter()
    if meta_path.exists():
        validate_index_meta(
            cfg.embed_db,
            model=str(index_meta.get("model") or ""),
            canonical_model=str(index_meta.get("canonical_model") or "") or None,
            dimensions=dimensions,
            normalized=bool(index_meta.get("normalized")),
            provider=str(index_meta.get("provider") or ""),
        )
    else:
        save_index_meta(
            cfg.embed_db,
            {
                "model": str(index_meta.get("model") or ""),
                "canonical_model": str(
                    index_meta.get("canonical_model") or index_meta.get("model") or ""
                ),
                "dimensions": cfg.embed_dimensions
                if cfg.embed_dimensions is not None
                else dimensions,
                "normalized": bool(index_meta.get("normalized")),
                "provider": str(index_meta.get("provider") or ""),
                "index_version": int(index_meta.get("index_version") or 1),
                "doc_count": 0,
                "template_count": 0,
                "chunk_count": 0,
                "last_updated": None,
            },
        )
    _log_semantic_phase(
        doc_id,
        template_tag,
        "validate_index_meta",
        phase_started_at,
        detail=f"meta_exists={int(meta_path.exists())}",
    )

    phase_started_at = time.perf_counter()
    db = open_store(cfg.embed_db)
    _log_semantic_phase(doc_id, template_tag, "open_store", phase_started_at)

    phase_started_at = time.perf_counter()
    state = read_admin_ingest_state(db, doc_id, template_tag)
    previous_template_keys = state.previous_template_keys
    existing_by_id = state.existing_by_id
    had_template_elsewhere = state.had_template_elsewhere
    _log_semantic_phase(
        doc_id,
        template_tag,
        "read_existing_state",
        phase_started_at,
        detail=(
            f"existing_rows={len(existing_by_id)} "
            f"doc_templates={len(previous_template_keys)} "
            f"doc_had_any_rows={int(state.doc_had_any_rows)} "
            f"had_template_elsewhere={int(had_template_elsewhere)}"
        ),
    )

    inserted = 0
    updated = 0
    skipped = 0
    incoming_ids: set[str] = set()
    current_rows: list[ChunkRow] = []
    phase_started_at = time.perf_counter()
    for chunk in all_chunks:
        chunk_id = str(chunk.get("id") or "")
        incoming_ids.add(chunk_id)
        content_hash = str(chunk.get("content_hash") or "")
        if chunk_id in existing_by_id:
            if existing_by_id[chunk_id] == content_hash:
                skipped += 1
            else:
                updated += 1
        else:
            inserted += 1
        try:
            vector = decode_vector_b64(
                str(chunk.get("vector_b64") or ""),
                int(chunk.get("vector_dim") or dimensions),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid vector payload for chunk {chunk_id}: {exc}") from exc
        current_rows.append(
            ChunkRow(
                id=chunk_id,
                doc_id=str(chunk.get("doc_id") or doc_id),
                source_path=str(chunk.get("source_path") or ""),
                template_tag=str(chunk.get("template_tag") or template_tag),
                chunk_type=str(chunk.get("chunk_type") or ""),
                chunk_index=int(chunk.get("chunk_index") or 0),
                field_name=str(chunk.get("field_name") or ""),
                lang=str(chunk.get("lang") or ""),
                text=str(chunk.get("text") or ""),
                content_hash=content_hash,
                vector=vector,
                title=str(chunk.get("title") or ""),
                year=int(chunk.get("year") or 0),
                authors=str(chunk.get("authors") or ""),
                venue=str(chunk.get("venue") or ""),
                tags=str(chunk.get("tags") or ""),
            )
        )
    _log_semantic_phase(
        doc_id,
        template_tag,
        "prepare_rows",
        phase_started_at,
        detail=f"incoming_rows={len(current_rows)}",
    )

    deleted = len(set(existing_by_id) - incoming_ids)
    if existing_by_id:
        phase_started_at = time.perf_counter()
        delete_groups(db, [(doc_id, template_tag if template_tag else SHARED_TEMPLATE_KEY)])
        _log_semantic_phase(
            doc_id,
            template_tag,
            "delete_groups",
            phase_started_at,
            detail=f"deleted_rows={deleted}",
        )
    else:
        logger.info(
            "semantic batch phase doc=%s tag=%s phase=delete_groups elapsed_ms=0.0 deleted_rows=0 skipped_delete=1",
            doc_id,
            _semantic_tag_label(template_tag),
        )
    if current_rows:
        phase_started_at = time.perf_counter()
        write_chunks(db, current_rows, dimensions=dimensions)
        _log_semantic_phase(
            doc_id,
            template_tag,
            "write_chunks",
            phase_started_at,
            detail=f"written_rows={len(current_rows)}",
        )
    else:
        logger.info(
            "semantic batch phase doc=%s tag=%s phase=write_chunks elapsed_ms=0.0 written_rows=0",
            doc_id,
            _semantic_tag_label(template_tag),
        )
    phase_started_at = time.perf_counter()
    _update_index_meta_after_group_write(
        cfg.embed_db,
        previous_template_keys=previous_template_keys,
        template_tag=template_tag,
        previous_group_count=len(existing_by_id),
        new_group_count=len(current_rows),
        had_template_elsewhere=had_template_elsewhere,
    )
    _log_semantic_phase(doc_id, template_tag, "save_index_meta", phase_started_at)
    total_elapsed = time.perf_counter() - batch_started_at
    done_log_fn = logger.warning if total_elapsed >= _SLOW_SEMANTIC_BATCH_SECONDS else logger.info
    done_log_fn(
        "semantic batch done doc=%s tag=%s elapsed_ms=%.1f inserted=%d updated=%d skipped=%d deleted=%d",
        doc_id,
        _semantic_tag_label(template_tag),
        total_elapsed * 1000.0,
        inserted,
        updated,
        skipped,
        deleted,
    )
    return {
        "received": len(all_chunks),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "deleted": deleted,
    }


async def _admin_ingest_semantic_chunks(request: Request) -> JSONResponse:
    if not _check_auth(request):
        return _AUTH_ERROR

    cfg: AdminConfig = request.app.state.admin_cfg
    if cfg.embed_db is None:
        return JSONResponse({"error": "semantic_storage_unavailable"}, status_code=503)

    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > _MAX_SEMANTIC_PAYLOAD_BYTES:
        return JSONResponse({"error": "Payload Too Large"}, status_code=413)

    raw_body = await request.body()
    if len(raw_body) > _MAX_SEMANTIC_PAYLOAD_BYTES:
        return JSONResponse({"error": "Payload Too Large"}, status_code=413)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            {"error": "bad_request", "detail": "invalid JSON body"}, status_code=400
        )

    index_meta = body.get("index_meta") if isinstance(body, dict) else None
    group = body.get("group") if isinstance(body, dict) else None
    chunks = body.get("chunks") if isinstance(body, dict) else None
    if (
        not isinstance(index_meta, dict)
        or not isinstance(group, dict)
        or not isinstance(chunks, list)
    ):
        return JSONResponse(
            {"error": "bad_request", "detail": "body must include index_meta, group, and chunks"},
            status_code=400,
        )

    dimensions = int(index_meta.get("dimensions") or 0)
    if cfg.embed_dimensions is not None and dimensions != cfg.embed_dimensions:
        return JSONResponse(
            {
                "error": f"Dimension mismatch: server expects {cfg.embed_dimensions}, got {dimensions}"
            },
            status_code=400,
        )

    doc_id = str(group.get("doc_id") or "")
    template_tag = str(group.get("template_tag") or "")
    group_hash = str(group.get("group_hash") or "")
    part_index = int(group.get("part_index") or 0)
    part_count = int(group.get("part_count") or 0)
    if not doc_id or not group_hash or part_count < 1:
        return JSONResponse(
            {"error": "bad_request", "detail": "invalid group metadata"}, status_code=400
        )

    request_started_at = time.perf_counter()
    logger.info(
        "semantic batch request doc=%s tag=%s part=%d/%d chunk_count=%d payload_bytes=%d",
        doc_id,
        _semantic_tag_label(template_tag),
        part_index + 1,
        part_count,
        len(chunks),
        len(raw_body),
    )

    lock_wait_started_at = time.perf_counter()
    lock: asyncio.Lock = request.app.state.semantic_ingest_lock
    async with lock:
        _log_semantic_phase(doc_id, template_tag, "wait_for_lock", lock_wait_started_at)
        if part_count > 1:
            phase_started_at = time.perf_counter()
            _stage_part(cfg, doc_id, template_tag, group_hash, part_index, part_count, chunks)
            _log_semantic_phase(
                doc_id,
                template_tag,
                "stage_part",
                phase_started_at,
                detail=f"part={part_index + 1}/{part_count} staged_chunks={len(chunks)}",
            )
            staged_parts = _count_staged_parts(cfg, doc_id, template_tag, group_hash)
            if staged_parts < part_count:
                logger.info(
                    "semantic batch staged doc=%s tag=%s part=%d/%d staged_parts=%d/%d waiting_for_more_parts=1",
                    doc_id,
                    _semantic_tag_label(template_tag),
                    part_index + 1,
                    part_count,
                    staged_parts,
                    part_count,
                )
                return JSONResponse(
                    {
                        "received": len(chunks),
                        "inserted": 0,
                        "updated": 0,
                        "skipped": 0,
                        "deleted": 0,
                    }
                )
            logger.info(
                "semantic batch ready doc=%s tag=%s part=%d/%d staged_parts=%d/%d",
                doc_id,
                _semantic_tag_label(template_tag),
                part_index + 1,
                part_count,
                staged_parts,
                part_count,
            )
            phase_started_at = time.perf_counter()
            all_chunks = _collect_staged_parts(cfg, doc_id, template_tag, group_hash)
            _log_semantic_phase(
                doc_id,
                template_tag,
                "collect_staged_parts",
                phase_started_at,
                detail=f"collected_chunks={len(all_chunks)}",
            )
        else:
            all_chunks = chunks

        from deepresearch_flow.paper.vector_store import compute_group_hash

        expected_group_hash = compute_group_hash(
            [str(chunk.get("content_hash") or "") for chunk in all_chunks]
        )
        if expected_group_hash != group_hash:
            return JSONResponse(
                {
                    "error": "bad_request",
                    "detail": "group_hash does not match chunk content_hash values",
                },
                status_code=400,
            )

        try:
            result = await asyncio.to_thread(
                _apply_semantic_chunk_batch,
                cfg,
                index_meta=index_meta,
                doc_id=doc_id,
                template_tag=template_tag,
                dimensions=dimensions,
                all_chunks=all_chunks,
            )
        except ValueError as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
        except Exception:
            logger.exception(
                "Semantic chunk ingest failed for doc_id=%s template_tag=%s part=%s/%s",
                doc_id,
                template_tag,
                part_index,
                part_count,
            )
            raise

        if part_count > 1:
            _clear_staged_parts(cfg, doc_id, template_tag, group_hash)
        total_request_elapsed = time.perf_counter() - request_started_at
        done_log_fn = (
            logger.warning if total_request_elapsed >= _SLOW_SEMANTIC_BATCH_SECONDS else logger.info
        )
        done_log_fn(
            "semantic batch response doc=%s tag=%s part=%d/%d elapsed_ms=%.1f",
            doc_id,
            _semantic_tag_label(template_tag),
            part_index + 1,
            part_count,
            total_request_elapsed * 1000.0,
        )
        return JSONResponse(result)


# ---------------------------------------------------------------------------
# Sub-application factory
# ---------------------------------------------------------------------------


def create_admin_app(
    *,
    snapshot_db: Path,
    admin_token: str,
    embed_db: Path | None = None,
    embed_dimensions: int | None = None,
) -> Starlette:
    """Create the admin sub-application mounted at ``/api/v1/admin``."""
    cfg = AdminConfig(
        snapshot_db=snapshot_db,
        admin_token=admin_token,
        embed_db=embed_db,
        embed_dimensions=embed_dimensions,
    )
    if embed_db is not None:
        _ensure_staging_table(cfg)

    routes = [
        Route("/papers", _admin_add_papers, methods=["POST"]),
        Route("/papers/{paper_id:str}", _admin_delete_paper, methods=["DELETE"]),
        Route("/semantic/chunks/batch", _admin_ingest_semantic_chunks, methods=["POST"]),
    ]

    app = Starlette(routes=routes)
    app.state.admin_cfg = cfg
    app.state.semantic_ingest_lock = asyncio.Lock()
    return app
