# python/deepresearch_flow/paper/snapshot/bibtex_match.py
"""BibTeX matching against snapshot paper database."""
from __future__ import annotations

import difflib
import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from deepresearch_flow.paper.snapshot.bibtex_utils import extract_doi_from_bibtex_raw
from deepresearch_flow.paper.snapshot.common import _column_exists, _open_ro_conn

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MatchedEntry:
    bibtex_key: str
    paper_id: str
    match_method: Literal["doi", "title"]
    title: str
    year: str | None
    venue: str | None
    authors: list[str]


@dataclass(frozen=True)
class UnmatchedEntry:
    bibtex_key: str
    title: str | None
    search_query: str


@dataclass(frozen=True)
class MatchResult:
    matched: list[MatchedEntry]
    unmatched: list[UnmatchedEntry]


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip braces/punctuation, collapse whitespace."""
    value = title.replace("{", "").replace("}", "")
    value = _NORMALIZE_RE.sub(" ", value.lower())
    return _WHITESPACE_RE.sub(" ", value).strip()


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fetch_authors(conn: sqlite3.Connection, paper_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT f.value
        FROM paper_author j
        JOIN author f ON f.author_id = j.author_id
        WHERE j.paper_id = ?
        ORDER BY f.value ASC
        """,
        (paper_id,),
    ).fetchall()
    return [str(r["value"]) for r in rows]


def _parse_bibtex_entries(raw: str) -> list[dict[str, Any]]:
    """Parse BibTeX text into a list of entry dicts using pybtex.

    Each dict has keys: key, type, title, year, doi_raw, fields.
    Entries that fail to parse are returned with title=None.
    Per-entry parsing: a malformed entry only affects itself.
    """
    if not raw or not raw.strip():
        return []

    try:
        from pybtex.database.input.bibtex import Parser
    except ImportError:
        raise RuntimeError("pybtex is required for BibTeX matching")

    entries: list[dict[str, Any]] = []

    # Split raw text into individual entry strings so we can parse each one
    # independently. This way a malformed entry only affects itself, not
    # valid siblings in the same batch.
    entry_starts: list[int] = []
    _ENTRY_RE = re.compile(r"@(?=\w+\s*\{)")
    for m in _ENTRY_RE.finditer(raw):
        entry_starts.append(m.start())

    if not entry_starts:
        return []

    raw_segments: list[str] = []
    for i, start in enumerate(entry_starts):
        end = entry_starts[i + 1] if i + 1 < len(entry_starts) else len(raw)
        raw_segments.append(raw[start:end])

    for segment in raw_segments:
        try:
            bib_data = Parser().parse_stream(io.StringIO(segment))
            for key, entry in bib_data.entries.items():
                fields = dict(entry.fields)
                title = str(fields.get("title", "")).replace("{", "").replace("}", "").strip() or None
                year = str(fields.get("year", "")).strip() or None
                doi_raw = fields.get("doi")
                entries.append({
                    "key": key,
                    "type": entry.type,
                    "title": title,
                    "year": year,
                    "doi_raw": str(doi_raw).strip() if doi_raw else None,
                    "fields": fields,
                })
        except Exception:
            # Single entry failed to parse — extract key at minimum
            key_match = re.search(r"@\w+\s*\{([^,\s]+)", segment)
            entries.append({
                "key": key_match.group(1) if key_match else "unknown",
                "type": "unknown",
                "title": None,
                "year": None,
                "doi_raw": None,
                "fields": {},
            })

    return entries


def match_bibtex_entries(bibtex_raw: str, db_path: Path) -> MatchResult:
    """Match BibTeX entries against the snapshot paper database.

    Matching pipeline per entry (stop at first match):
    1. DOI exact match
    2. Title fuzzy match (SequenceMatcher >= 0.9, uniqueness gap >= 0.05, year cross-check)
    """
    entries = _parse_bibtex_entries(bibtex_raw)
    if not entries:
        return MatchResult(matched=[], unmatched=[])

    conn = _open_ro_conn(db_path)
    try:
        has_doi = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi else "NULL AS doi"

        # Build title prefix index from all papers in DB
        all_papers = conn.execute(
            f"SELECT paper_id, title, year, venue, {doi_select} FROM paper"
        ).fetchall()

        paper_index: list[dict[str, Any]] = []
        by_prefix: dict[str, list[int]] = {}
        doi_lookup: dict[str, dict[str, Any]] = {}

        for row in all_papers:
            record = {
                "paper_id": str(row["paper_id"]),
                "title": str(row["title"] or ""),
                "year": str(row["year"]) if row["year"] else None,
                "venue": str(row["venue"]) if row["venue"] else None,
                "doi": str(row["doi"]) if row["doi"] else None,
            }
            title_norm = _normalize_title(record["title"])
            record["_title_norm"] = title_norm
            idx = len(paper_index)
            paper_index.append(record)

            if title_norm:
                prefix = title_norm[:16]
                by_prefix.setdefault(prefix, []).append(idx)

            if record["doi"]:
                doi_lookup[record["doi"]] = record

        matched: list[MatchedEntry] = []
        unmatched: list[UnmatchedEntry] = []

        for entry in entries:
            bib_key = entry["key"]
            bib_title = entry["title"]
            bib_year = entry["year"]
            search_query = bib_title or bib_key

            # Level 1: DOI exact match
            if entry["doi_raw"] and has_doi:
                from deepresearch_flow.paper.snapshot.identity import canonicalize_doi
                canonical = canonicalize_doi(entry["doi_raw"])
                if canonical and canonical in doi_lookup:
                    paper = doi_lookup[canonical]
                    authors = _fetch_authors(conn, paper["paper_id"])
                    matched.append(MatchedEntry(
                        bibtex_key=bib_key,
                        paper_id=paper["paper_id"],
                        match_method="doi",
                        title=paper["title"],
                        year=paper["year"],
                        venue=paper["venue"],
                        authors=authors,
                    ))
                    continue

            # Level 2: Title fuzzy match
            if bib_title:
                query_norm = _normalize_title(bib_title)
                if query_norm:
                    # Get candidates via prefix index
                    prefix = query_norm[:16]
                    candidate_indices = by_prefix.get(prefix, [])
                    if not candidate_indices:
                        # Fallback: scan all papers
                        candidate_indices = list(range(len(paper_index)))

                    # Score all candidates
                    scores: list[tuple[int, float]] = []
                    for ci in candidate_indices:
                        score = _title_similarity(query_norm, paper_index[ci]["_title_norm"])
                        if score >= 0.9:
                            scores.append((ci, score))

                    if scores:
                        scores.sort(key=lambda x: x[1], reverse=True)
                        best_idx, best_score = scores[0]

                        # Uniqueness check: best must lead second-best by >= 0.05
                        is_unique = len(scores) == 1 or (best_score - scores[1][1]) >= 0.05

                        if is_unique:
                            paper = paper_index[best_idx]
                            # Year cross-check
                            year_ok = True
                            if bib_year and paper["year"] and bib_year != paper["year"]:
                                year_ok = False

                            if year_ok:
                                authors = _fetch_authors(conn, paper["paper_id"])
                                matched.append(MatchedEntry(
                                    bibtex_key=bib_key,
                                    paper_id=paper["paper_id"],
                                    match_method="title",
                                    title=paper["title"],
                                    year=paper["year"],
                                    venue=paper["venue"],
                                    authors=authors,
                                ))
                                continue

            # No match
            unmatched.append(UnmatchedEntry(
                bibtex_key=bib_key,
                title=bib_title,
                search_query=search_query,
            ))

        return MatchResult(matched=matched, unmatched=unmatched)
    finally:
        conn.close()
