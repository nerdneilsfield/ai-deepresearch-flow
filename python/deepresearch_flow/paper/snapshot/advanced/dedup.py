"""Content-hash and cosine dedup for selected chunks."""

from __future__ import annotations

import hashlib
import math

from deepresearch_flow.paper.snapshot.advanced.chunk_select import SelectedChunk


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    return dot / (norm_a * norm_b)


def dedup(selected: list[SelectedChunk], *, cosine_threshold: float) -> list[SelectedChunk]:
    best_by_id: dict[str, SelectedChunk] = {}
    for chunk in selected:
        current = best_by_id.get(chunk.chunk_id)
        if current is None or chunk.fused_score > current.fused_score:
            best_by_id[chunk.chunk_id] = chunk

    best_by_text: dict[str, SelectedChunk] = {}
    for chunk in best_by_id.values():
        text_hash = _text_hash(chunk.chunk_text)
        current = best_by_text.get(text_hash)
        if current is None or chunk.fused_score > current.fused_score:
            best_by_text[text_hash] = chunk

    unique = list(best_by_text.values())
    unique.sort(key=lambda item: -item.fused_score)

    kept: list[SelectedChunk] = []
    for candidate in unique:
        duplicate_of: SelectedChunk | None = None
        for existing in kept:
            similarity = _cosine(candidate.vector, existing.vector)
            if similarity is not None and similarity >= cosine_threshold:
                duplicate_of = existing
                break
        if duplicate_of is None:
            kept.append(candidate)
            continue
        if candidate.fused_score > duplicate_of.fused_score:
            kept.remove(duplicate_of)
            kept.append(candidate)
    kept.sort(key=lambda item: -item.fused_score)
    return kept
