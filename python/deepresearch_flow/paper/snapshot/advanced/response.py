"""Response payload assembly for advanced search."""

from __future__ import annotations

import sqlite3
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _hydrate_papers(conn: sqlite3.Connection, paper_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not paper_ids:
        return {}
    placeholders = ",".join("?" for _ in paper_ids)
    paper_rows = conn.execute(
        f"SELECT paper_id, title, year, venue, source_hash, doi "
        f"FROM paper WHERE paper_id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    papers: dict[str, dict[str, Any]] = {}
    for row in paper_rows:
        papers[str(row["paper_id"])] = {
            "title": str(row["title"] or ""),
            "year": str(row["year"] or ""),
            "venue": str(row["venue"] or ""),
            "source_hash": str(row["source_hash"] or ""),
            "doi": str(row["doi"] or ""),
            "authors": [],
        }

    author_rows = conn.execute(
        f"SELECT pa.paper_id, a.value "
        f"FROM paper_author pa JOIN author a ON a.author_id = pa.author_id "
        f"WHERE pa.paper_id IN ({placeholders}) "
        f"ORDER BY pa.paper_id, a.author_id",
        paper_ids,
    ).fetchall()
    for row in author_rows:
        paper_id = str(row["paper_id"])
        if paper_id in papers:
            papers[paper_id]["authors"].append(str(row["value"]))
    return papers


def assemble_response(
    *,
    chunks: list[SelectedChunk],
    rerank_scores: list[float],
    conn: sqlite3.Connection,
    rerank_applied: bool,
    mmr_applied: bool,
    mmr_lambda: float,
    fusion_label: str,
    embedding_model: str,
    embedding_dimensions: int,
    reranker_model: str | None,
    query_raw: str,
    query_normalized: str,
    applied_filters: dict,
    counts: dict,
    latency_ms: dict,
    trace_id: str,
    degraded: bool,
    degradation_reason: str | None,
    degradation_message: str | None,
    degradation_details: dict[str, Any] | None,
) -> dict[str, Any]:
    papers = _hydrate_papers(conn, [chunk.paper_id for chunk in chunks])

    results: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        scores: dict[str, Any] = {"fused": chunk.fused_score}
        if chunk.dense_score is not None:
            scores["dense"] = chunk.dense_score
        if chunk.paper_sparse_score is not None:
            scores["sparse"] = chunk.paper_sparse_score
        if rerank_applied and idx < len(rerank_scores):
            scores["reranker"] = rerank_scores[idx]
            scores["final"] = rerank_scores[idx]
        else:
            scores["final"] = chunk.fused_score

        paper_meta = papers.get(chunk.paper_id, {
            "title": "",
            "authors": [],
            "year": "",
            "venue": "",
            "doi": "",
            "source_hash": "",
        })
        results.append(
            {
                "chunk_id": chunk.chunk_id,
                "paper_id": chunk.paper_id,
                "paper": paper_meta,
                "chunk": {
                    "text": chunk.chunk_text,
                    "field_name": chunk.field_name,
                    "template_tag": chunk.template_tag,
                    "chunk_type": chunk.chunk_type,
                    "chunk_index": chunk.chunk_index,
                    "lang": chunk.lang,
                },
                "scores": scores,
            }
        )

    return {
        "success": True,
        "trace_id": trace_id,
        "query": {
            "raw": query_raw,
            "normalized": query_normalized,
            "applied_filters": applied_filters,
        },
        "results": results,
        "metadata": {
            "counts": counts,
            "fusion": fusion_label,
            "reranker": {"applied": rerank_applied, "model": reranker_model},
            "mmr": {"applied": mmr_applied, "lambda": mmr_lambda},
            "embedding": {
                "model": embedding_model,
                "dimensions": embedding_dimensions,
            },
            "latency_ms": latency_ms,
        },
        "degraded": degraded,
        "degradation": {
            "reason": degradation_reason,
            "message": degradation_message,
            "details": degradation_details or {},
        } if degraded else None,
    }
