"""Hybrid search helpers for vector + keyword recall."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable
from typing import Any

import httpx

from deepresearch_flow.paper.reranker import RerankProvider

_VENUE_FILTER_RE = re.compile(r"^[\w\s.,:&()/+\-]+$", re.UNICODE)


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    chunk_text: str
    score: float
    field_name: str
    template_tag: str
    chunk_type: str
    lang: str


@dataclass(frozen=True)
class SearchResult:
    doc_id: str
    score: float
    score_type: str
    matched_chunk: str
    matched_field: str
    matched_template: str
    matched_chunk_type: str
    matched_lang: str


@dataclass
class SearchProgress:
    vector_candidates: int = 0
    keyword_candidates: int = 0
    fused_candidates: int = 0
    rerank_requested: bool = False
    rerank_applied: bool = False
    rerank_reason: str | None = None


def rank_keyword_rows(
    rows: list[dict[str, Any]],
    query_text: str,
    *,
    limit: int,
) -> list[str]:
    phrase = query_text.strip().lower()
    tokens = [token for token in phrase.split() if token]
    if not tokens:
        return []

    scores: dict[str, float] = {}
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id:
            continue
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("title", "text", "authors", "venue", "tags")
        ).lower()
        score = 0.0
        if phrase and phrase in haystack:
            score += float(len(tokens) + 2)
        score += float(sum(1 for token in tokens if token in haystack))
        if score <= 0:
            continue
        scores[doc_id] = max(scores.get(doc_id, 0.0), score)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [doc_id for doc_id, _ in ranked[:limit]]


def validate_venue_filter(venue: str) -> str:
    cleaned = venue.strip()
    if not cleaned:
        raise ValueError("Venue filter cannot be empty")
    if not _VENUE_FILTER_RE.fullmatch(cleaned):
        raise ValueError("Venue filter contains unsupported characters")
    return cleaned


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def aggregate_by_doc_id(hits: list[SearchHit]) -> list[SearchHit]:
    best: dict[str, SearchHit] = {}
    for hit in hits:
        existing = best.get(hit.doc_id)
        if existing is None or hit.score > existing.score:
            best[hit.doc_id] = hit
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


def vector_hits_to_search_hits(results: list[dict[str, Any]]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for row in results:
        distance = float(row.get("_distance", 0.0))
        cosine_similarity = 1.0 - distance
        hits.append(
            SearchHit(
                doc_id=str(row["doc_id"]),
                chunk_text=str(row.get("text", "")),
                score=cosine_similarity,
                field_name=str(row.get("field_name", "")),
                template_tag=str(row.get("template_tag", "")),
                chunk_type=str(row.get("chunk_type", "")),
                lang=str(row.get("lang", "")),
            )
        )
    return hits


async def hybrid_search(
    *,
    query_vector: list[float],
    query_text: str,
    vector_store_db: Any,
    keyword_search_fn: Callable[[str, int], list[str]] | None,
    reranker: RerankProvider | None,
    vector_top_k: int = 50,
    keyword_top_k: int = 30,
    rerank_top_n: int = 10,
    hybrid: bool = True,
    where: str | None = None,
    document_text_resolver: Callable[[str], str] | None = None,
    client: httpx.AsyncClient | None = None,
    progress: SearchProgress | None = None,
) -> list[SearchResult]:
    from deepresearch_flow.paper.vector_store import query_vector as query_vector_store

    raw_vector_hits = query_vector_store(vector_store_db, query_vector, top_k=vector_top_k, where=where)
    vector_hits = aggregate_by_doc_id(vector_hits_to_search_hits(raw_vector_hits))
    if progress is not None:
        progress.vector_candidates = len(vector_hits)

    if hybrid and keyword_search_fn is not None:
        keyword_ranked = list(keyword_search_fn(query_text, limit=keyword_top_k))
        if progress is not None:
            progress.keyword_candidates = len(keyword_ranked)
        vector_ranked = [hit.doc_id for hit in vector_hits]
        fused_scores = reciprocal_rank_fusion([vector_ranked, keyword_ranked], k=60)
        hit_map = {hit.doc_id: hit for hit in vector_hits}
        candidates: list[tuple[str, float, SearchHit | None]] = [
            (doc_id, score, hit_map.get(doc_id))
            for doc_id, score in fused_scores.items()
        ]
        candidates.sort(key=lambda item: item[1], reverse=True)
        score_type = "rrf"
    else:
        candidates = [(hit.doc_id, hit.score, hit) for hit in vector_hits]
        score_type = "cosine"
        if progress is not None:
            progress.keyword_candidates = 0
    if progress is not None:
        progress.fused_candidates = len(candidates)

    if reranker is not None and client is not None and candidates:
        if progress is not None:
            progress.rerank_requested = True
        docs_for_rerank = [
            (
                hit.chunk_text
                if hit
                else (document_text_resolver(doc_id) if document_text_resolver is not None else doc_id)
            )
            for doc_id, _, hit in candidates
        ]
        doc_ids = [doc_id for doc_id, _, _ in candidates]
        try:
            rerank_result = await reranker.rerank(
                query=query_text,
                documents=docs_for_rerank,
                top_n=rerank_top_n,
                client=client,
            )
        except Exception as exc:
            if progress is not None:
                progress.rerank_applied = False
                progress.rerank_reason = str(exc) or exc.__class__.__name__
            rerank_result = None
        if rerank_result is not None:
            if progress is not None:
                progress.rerank_applied = True
                progress.rerank_reason = None
            hit_map = {hit.doc_id: hit for hit in vector_hits}
            return [
                SearchResult(
                    doc_id=doc_ids[index],
                    score=score,
                    score_type="rerank",
                    matched_chunk=(hit_map.get(doc_ids[index]).chunk_text if hit_map.get(doc_ids[index]) else ""),
                    matched_field=(hit_map.get(doc_ids[index]).field_name if hit_map.get(doc_ids[index]) else ""),
                    matched_template=(hit_map.get(doc_ids[index]).template_tag if hit_map.get(doc_ids[index]) else ""),
                    matched_chunk_type=(hit_map.get(doc_ids[index]).chunk_type if hit_map.get(doc_ids[index]) else ""),
                    matched_lang=(hit_map.get(doc_ids[index]).lang if hit_map.get(doc_ids[index]) else ""),
                )
                for index, score in zip(rerank_result.indices, rerank_result.scores)
            ]

    return [
        SearchResult(
            doc_id=doc_id,
            score=score,
            score_type=score_type,
            matched_chunk=(hit.chunk_text if hit else ""),
            matched_field=(hit.field_name if hit else ""),
            matched_template=(hit.template_tag if hit else ""),
            matched_chunk_type=(hit.chunk_type if hit else ""),
            matched_lang=(hit.lang if hit else ""),
        )
        for doc_id, score, hit in candidates[:rerank_top_n]
    ]
