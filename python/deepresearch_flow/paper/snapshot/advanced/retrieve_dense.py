"""Dense retrieval: embed the query, query LanceDB, return chunk hits."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.embedding import call_embedding_with_route_pool
from deepresearch_flow.paper.vector_store import query_vector


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    paper_id: str
    dense_score: float
    chunk_text: str
    field_name: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    lang: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class DenseRetrieveResult:
    hits: list[ChunkHit]
    embed_ms: int
    dense_ms: int


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


async def dense_retrieve(
    *,
    query_text: str,
    lance_db: Any,
    embedding_route_pool: Any,
    client: Any,
    dimensions: int,
    top_k: int,
    lance_where: str,
) -> list[ChunkHit]:
    return (
        await dense_retrieve_with_metrics(
            query_text=query_text,
            lance_db=lance_db,
            embedding_route_pool=embedding_route_pool,
            client=client,
            dimensions=dimensions,
            top_k=top_k,
            lance_where=lance_where,
        )
    ).hits


async def dense_retrieve_with_metrics(
    *,
    query_text: str,
    lance_db: Any,
    embedding_route_pool: Any,
    client: Any,
    dimensions: int,
    top_k: int,
    lance_where: str,
) -> DenseRetrieveResult:
    embed_started = _now_ms()
    embed_result = await call_embedding_with_route_pool(
        route_pool=embedding_route_pool,
        texts=[query_text],
        dimensions=dimensions,
        client=client,
    )
    embed_ms = _now_ms() - embed_started
    query_vec = embed_result.vectors[0]
    dense_started = _now_ms()
    rows = query_vector(lance_db, query_vec, top_k=top_k, where=lance_where or None)
    dense_ms = _now_ms() - dense_started
    hits: list[ChunkHit] = []
    for row in rows:
        distance = float(row.get("_distance", 0.0))
        hits.append(
            ChunkHit(
                chunk_id=str(row.get("id", "")),
                paper_id=str(row.get("doc_id", "")),
                dense_score=1.0 - distance,
                chunk_text=str(row.get("text", "")),
                field_name=str(row.get("field_name", "")),
                template_tag=str(row.get("template_tag", "")),
                chunk_type=str(row.get("chunk_type", "")),
                chunk_index=int(row.get("chunk_index", 0) or 0),
                lang=str(row.get("lang", "")),
                vector=tuple(float(v) for v in (row.get("vector") or ())),
            )
        )
    return DenseRetrieveResult(hits=hits, embed_ms=embed_ms, dense_ms=dense_ms)
