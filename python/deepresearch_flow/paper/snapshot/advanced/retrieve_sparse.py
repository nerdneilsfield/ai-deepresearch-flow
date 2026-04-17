"""Sparse retrieval: paper_fts MATCH + BM25 ranking + filter pushdown."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from deepresearch_flow.paper.snapshot.advanced.filters import ParsedFilters

_BM25 = "bm25(paper_fts, 5.0, 3.0, 1.0, 1.0, 2.0)"


@dataclass(frozen=True)
class PaperHit:
    paper_id: str
    sparse_score: float


def _append_relational_filters(
    *,
    where_parts: list[str],
    params: list[object],
    filters: ParsedFilters,
) -> None:
    if filters.sql_where:
        where_parts.append(filters.sql_where)

    if filters.authors:
        placeholders = ",".join("?" for _ in filters.authors)
        where_parts.append(
            "EXISTS ("
            "SELECT 1 FROM paper_author pa "
            "JOIN author a ON a.author_id = pa.author_id "
            "WHERE pa.paper_id = p.paper_id "
            f"AND LOWER(a.value) IN ({placeholders})"
            ")"
        )
        params.extend(filters.authors)

    if filters.keywords:
        placeholders = ",".join("?" for _ in filters.keywords)
        where_parts.append(
            "EXISTS ("
            "SELECT 1 FROM paper_keyword pk "
            "JOIN keyword kw ON kw.keyword_id = pk.keyword_id "
            "WHERE pk.paper_id = p.paper_id "
            f"AND LOWER(kw.value) IN ({placeholders})"
            ")"
        )
        params.extend(filters.keywords)

    if filters.tags:
        placeholders = ",".join("?" for _ in filters.tags)
        where_parts.append(
            "EXISTS ("
            "SELECT 1 FROM paper_tag pt "
            "JOIN tag t ON t.tag_id = pt.tag_id "
            "WHERE pt.paper_id = p.paper_id "
            f"AND LOWER(t.value) IN ({placeholders})"
            ")"
        )
        params.extend(filters.tags)


def sparse_retrieve(
    *,
    conn: sqlite3.Connection,
    fts_expr: str,
    filters: ParsedFilters,
    top_k: int,
    lang: str,
) -> list[PaperHit]:
    if not fts_expr:
        return []

    joins: list[str] = ["JOIN paper p ON p.paper_id = paper_fts.paper_id"]
    where_parts: list[str] = ["paper_fts MATCH ?"]
    params: list[object] = [fts_expr]
    _append_relational_filters(where_parts=where_parts, params=params, filters=filters)

    sql = (
        f"SELECT paper_fts.paper_id AS paper_id, {_BM25} AS rank "
        f"FROM paper_fts "
        + " ".join(joins)
        + " WHERE "
        + " AND ".join(where_parts)
        + " ORDER BY rank ASC LIMIT ?"
    )
    params.append(top_k)

    hits: dict[str, float] = {}
    for row in conn.execute(sql, params):
        hits[str(row["paper_id"])] = float(row["rank"])

    if lang == "zh":
        try:
            trigram_where_parts = ["paper_fts_trigram MATCH ?"]
            trigram_params: list[object] = [fts_expr]
            _append_relational_filters(
                where_parts=trigram_where_parts,
                params=trigram_params,
                filters=filters,
            )
            trigram_sql = (
                "SELECT paper_fts_trigram.paper_id AS paper_id, "
                "bm25(paper_fts_trigram) AS rank "
                "FROM paper_fts_trigram "
                "JOIN paper p ON p.paper_id = paper_fts_trigram.paper_id "
                "WHERE "
                + " AND ".join(trigram_where_parts)
                + " ORDER BY rank ASC LIMIT ?"
            )
            trigram_params.append(top_k)
            trigram_rows = conn.execute(trigram_sql, trigram_params).fetchall()
        except sqlite3.Error:
            trigram_rows = []
        for row in trigram_rows:
            paper_id = str(row["paper_id"])
            trigram_rank = float(row["rank"])
            current_rank = hits.get(paper_id)
            hits[paper_id] = trigram_rank if current_rank is None else min(current_rank, trigram_rank)

    ranked = sorted(hits.items(), key=lambda item: (item[1], item[0]))
    max_rank = ranked[-1][1] if ranked else 0.0
    output = [
        PaperHit(paper_id=paper_id, sparse_score=max_rank - rank)
        for paper_id, rank in ranked
    ]
    output.sort(key=lambda item: (-item.sparse_score, item.paper_id))
    return output[:top_k]
