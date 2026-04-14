"""Client-side semantic chunk push to remote admin API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from deepresearch_flow.paper.snapshot.push import RemoteConfig
from deepresearch_flow.paper.vector_store import compute_group_hash, encode_vector_b64

logger = logging.getLogger(__name__)

_PAYLOAD_OVERHEAD_BYTES = 256 * 1024


@dataclass
class PushSemanticStats:
    received: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    batches_sent: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def _encode_chunk_for_wire(chunk: dict[str, Any]) -> dict[str, Any]:
    wire = {k: v for k, v in chunk.items() if k != "vector"}
    vector = list(chunk["vector"])
    wire["vector_b64"] = encode_vector_b64(vector)
    wire["vector_dim"] = len(vector)
    return wire


def _estimate_chunk_bytes(chunk: dict[str, Any]) -> int:
    return len(json.dumps(chunk, ensure_ascii=False).encode("utf-8"))


def group_chunks_for_push(
    chunks: list[dict[str, Any]],
    *,
    max_rows: int = 100,
    max_payload_bytes: int = 16_000_000,
) -> list[dict[str, Any]]:
    effective_limit = max(1, max_payload_bytes - _PAYLOAD_OVERHEAD_BYTES)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in chunks:
        key = (str(chunk["doc_id"]), str(chunk.get("template_tag", "")))
        groups.setdefault(key, []).append(chunk)

    requests: list[dict[str, Any]] = []
    for (doc_id, template_tag), group_chunks in groups.items():
        encoded = [_encode_chunk_for_wire(chunk) for chunk in group_chunks]
        group_hash = compute_group_hash([str(chunk["content_hash"]) for chunk in group_chunks])

        parts: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_bytes = 0
        for chunk in encoded:
            chunk_bytes = _estimate_chunk_bytes(chunk)
            if chunk_bytes > effective_limit and not current:
                parts.append([chunk])
                continue
            would_exceed_rows = len(current) >= max_rows
            would_exceed_bytes = bool(current) and (current_bytes + chunk_bytes > effective_limit)
            if would_exceed_rows or would_exceed_bytes:
                parts.append(current)
                current = []
                current_bytes = 0
            current.append(chunk)
            current_bytes += chunk_bytes
        if current:
            parts.append(current)

        part_count = len(parts)
        for part_index, part_chunks in enumerate(parts):
            requests.append(
                {
                    "group": {
                        "doc_id": doc_id,
                        "template_tag": template_tag,
                        "group_hash": group_hash,
                        "part_index": part_index,
                        "part_count": part_count,
                        "is_final_part": part_index == part_count - 1,
                    },
                    "chunks": part_chunks,
                }
            )
    return requests


def push_semantic_chunks(
    chunks: list[dict[str, Any]],
    index_meta: dict[str, Any],
    config: RemoteConfig,
    *,
    max_rows: int = 100,
    max_payload_bytes: int = 16_000_000,
    timeout: float = 120.0,
    on_batch: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> PushSemanticStats:
    requests = group_chunks_for_push(chunks, max_rows=max_rows, max_payload_bytes=max_payload_bytes)
    stats = PushSemanticStats()
    url = f"{config.api_base_url}/api/v1/admin/semantic/chunks/batch"
    headers = {
        "Authorization": f"Bearer {config.admin_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        for batch_index, request_payload in enumerate(requests):
            body = {
                "index_meta": index_meta,
                "group": request_payload["group"],
                "chunks": request_payload["chunks"],
            }
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            stats.received += int(data.get("received", 0))
            stats.inserted += int(data.get("inserted", 0))
            stats.updated += int(data.get("updated", 0))
            stats.skipped += int(data.get("skipped", 0))
            stats.deleted += int(data.get("deleted", 0))
            stats.batches_sent += 1
            if on_batch is not None:
                on_batch(batch_index, len(request_payload["chunks"]), data)

    return stats
