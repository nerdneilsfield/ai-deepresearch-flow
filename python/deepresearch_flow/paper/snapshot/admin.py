"""Admin API for managing papers in the snapshot database.

Provides endpoints under ``/api/v1/admin/`` for adding and deleting papers.
Authentication is via Bearer token in the ``Authorization`` header.
"""

from __future__ import annotations

import hmac
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from deepresearch_flow.paper.snapshot.bibtex_utils import (
    extract_canonical_doi,
    extract_current_bibtex_payload,
)
from deepresearch_flow.paper.snapshot.common import _open_rw_conn
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


@dataclass(frozen=True)
class AdminConfig:
    snapshot_db: Path
    admin_token: str


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth(request: Request) -> str | None:
    """Return an error message if auth fails, or ``None`` if OK."""
    cfg: AdminConfig = request.app.state.admin_cfg
    if not cfg.admin_token:
        return "admin API is disabled"
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return "unauthorized"
    if not hmac.compare_digest(auth[7:], cfg.admin_token):
        return "unauthorized"
    return None


def _auth_error(msg: str) -> JSONResponse:
    return JSONResponse({"error": "unauthorized", "detail": msg}, status_code=401)


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
            return str(row["paper_id"]), cand.paper_key, cand.key_type, cand.meta_fingerprint, candidates

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

    existing = conn.execute(
        "SELECT 1 FROM paper WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if existing:
        stats.skipped += 1
        return

    year, month = _parse_year_month(paper)
    publication_date = str(paper.get("publication_date") or "").strip()
    venue = _extract_venue(paper)

    bib = paper.get("bibtex") if isinstance(paper.get("bibtex"), dict) else None
    bib_fields = bib.get("fields") if isinstance(bib, dict) and isinstance(bib.get("fields"), dict) else {}
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
    preferred_markdown = _extract_template_markdown(template_payloads.get(preferred_summary_template, {}))

    # Prefer the preview carried from source DB when no real template content
    # exists (avoids polluting preview/FTS with raw JSON dumps of the paper dict)
    incoming_preview = str(paper.get("summary_preview") or "").strip()
    if has_real_templates:
        summary_preview = _summary_preview(preferred_markdown)
    else:
        summary_preview = incoming_preview or _summary_preview(preferred_markdown)

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
            paper_id, paper_key, paper_key_type, doi, title, year, month,
            publication_date, venue, preferred_summary_template,
            summary_preview,
            source_hash, output_language, provider, model, prompt_template,
            extracted_at, pdf_content_hash, source_md_content_hash,
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
            (cand.paper_key, paper_id, cand.key_type, cand.meta_fingerprint if cand.key_type == "meta" else None),
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

    _link_dim(conn, paper_id=paper_id, table="author", id_col="author_id", join_table="paper_author", join_col="author_id", values=authors)
    _link_dim(conn, paper_id=paper_id, table="keyword", id_col="keyword_id", join_table="paper_keyword", join_col="keyword_id", values=keywords)
    _link_dim(conn, paper_id=paper_id, table="institution", id_col="institution_id", join_table="paper_institution", join_col="institution_id", values=institutions)
    _link_dim(conn, paper_id=paper_id, table="tag", id_col="tag_id", join_table="paper_tag", join_col="tag_id", values=tags)
    _link_dim(conn, paper_id=paper_id, table="venue", id_col="venue_id", join_table="paper_venue", join_col="venue_id", values=[venue] if venue else [])

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
        edge_rows = [(left, right) for idx, left in enumerate(node_list) for right in node_list[idx + 1:]]
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
            " ".join(_extract_template_markdown(template_payloads[tag]) for tag in ordered_template_tags)
        )
    else:
        summary_text = summary_preview
    metadata_text = " ".join(
        part for part in [title, " ".join(authors), venue, " ".join(keywords), " ".join(institutions), year, doi or ""]
        if part
    )
    conn.execute(
        "INSERT INTO paper_fts(paper_id, title, summary, source, translated, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        (paper_id, insert_cjk_spaces(title), insert_cjk_spaces(summary_text), "", "", insert_cjk_spaces(metadata_text)),
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
    auth_err = _check_auth(request)
    if auth_err:
        return _auth_error(auth_err)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "bad_request", "detail": "invalid JSON body"}, status_code=400)

    papers = body.get("papers") if isinstance(body, dict) else None
    if not isinstance(papers, list):
        return JSONResponse(
            {"error": "bad_request", "detail": "body must be {\"papers\": [...]}"},
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

    return JSONResponse({
        "added": stats.added,
        "skipped": stats.skipped,
        "errors": stats.errors,
        "paper_ids": stats.paper_ids,
    })


async def _admin_delete_paper(request: Request) -> JSONResponse:
    auth_err = _check_auth(request)
    if auth_err:
        return _auth_error(auth_err)

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


# ---------------------------------------------------------------------------
# Sub-application factory
# ---------------------------------------------------------------------------

def create_admin_app(*, snapshot_db: Path, admin_token: str) -> Starlette:
    """Create the admin sub-application mounted at ``/api/v1/admin``."""
    cfg = AdminConfig(snapshot_db=snapshot_db, admin_token=admin_token)

    routes = [
        Route("/papers", _admin_add_papers, methods=["POST"]),
        Route("/papers/{paper_id:str}", _admin_delete_paper, methods=["DELETE"]),
    ]

    app = Starlette(routes=routes)
    app.state.admin_cfg = cfg
    return app
