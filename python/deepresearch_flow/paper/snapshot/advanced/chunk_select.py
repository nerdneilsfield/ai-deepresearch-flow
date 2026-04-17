"""Select one representative chunk per fused paper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.errors import VectorStoreUnavailableError
from deepresearch_flow.paper.snapshot.advanced.fusion import FusedPaper
from deepresearch_flow.paper.snapshot.advanced.retrieve_dense import ChunkHit

_TABLE = "paper_chunks"
_PREFERRED_TYPES = ("abstract", "title")


@dataclass(frozen=True)
class SelectedChunk:
    paper_id: str
    chunk_id: str
    chunk_text: str
    field_name: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    lang: str
    vector: tuple[float, ...]
    fused_score: float
    paper_dense_score: float | None
    paper_sparse_score: float | None
    dense_score: float | None


def _preference_key(row: dict[str, Any]) -> tuple[int, int]:
    chunk_type = str(row.get("chunk_type", ""))
    pref = _PREFERRED_TYPES.index(chunk_type) if chunk_type in _PREFERRED_TYPES else len(_PREFERRED_TYPES)
    return pref, int(row.get("chunk_index", 0) or 0)


def _select_from_lance(lance_db: Any, paper_id: str) -> dict[str, Any] | None:
    safe_paper_id = paper_id.replace("'", "''")
    try:
        rows = (
            lance_db.open_table(_TABLE)
            .search()
            .where(f"doc_id = '{safe_paper_id}'")
            .limit(32)
            .to_list()
        )
    except Exception as exc:
        raise VectorStoreUnavailableError(str(exc)) from exc
    if not rows:
        return None
    rows.sort(key=_preference_key)
    return rows[0]


def select_chunks(
    *,
    fused_papers: list[FusedPaper],
    dense_chunks: list[ChunkHit],
    lance_db: Any,
    max_papers: int,
) -> list[SelectedChunk]:
    best_dense: dict[str, ChunkHit] = {}
    for hit in dense_chunks:
        current = best_dense.get(hit.paper_id)
        if current is None or hit.dense_score > current.dense_score:
            best_dense[hit.paper_id] = hit

    selected: list[SelectedChunk] = []
    for fused in fused_papers[:max_papers]:
        dense_hit = best_dense.get(fused.paper_id)
        if dense_hit is not None:
            selected.append(
                SelectedChunk(
                    paper_id=fused.paper_id,
                    chunk_id=dense_hit.chunk_id,
                    chunk_text=dense_hit.chunk_text,
                    field_name=dense_hit.field_name,
                    template_tag=dense_hit.template_tag,
                    chunk_type=dense_hit.chunk_type,
                    chunk_index=dense_hit.chunk_index,
                    lang=dense_hit.lang,
                    vector=dense_hit.vector,
                    fused_score=fused.fused_score,
                    paper_dense_score=fused.paper_dense_score,
                    paper_sparse_score=fused.paper_sparse_score,
                    dense_score=dense_hit.dense_score,
                )
            )
            continue

        row = _select_from_lance(lance_db, fused.paper_id)
        if row is None:
            continue
        selected.append(
            SelectedChunk(
                paper_id=fused.paper_id,
                chunk_id=str(row.get("id", "")),
                chunk_text=str(row.get("text", "")),
                field_name=str(row.get("field_name", "")),
                template_tag=str(row.get("template_tag", "")),
                chunk_type=str(row.get("chunk_type", "")),
                chunk_index=int(row.get("chunk_index", 0) or 0),
                lang=str(row.get("lang", "")),
                vector=tuple(float(v) for v in (row.get("vector") or ())),
                fused_score=fused.fused_score,
                paper_dense_score=fused.paper_dense_score,
                paper_sparse_score=fused.paper_sparse_score,
                dense_score=None,
            )
        )
    return selected
