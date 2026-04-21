"""Client-side semantic chunk push to remote admin API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
import time
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


class SemanticPushError(RuntimeError):
    def __init__(self, failure: dict[str, Any], stats: PushSemanticStats):
        self.failure = failure
        self.stats = stats
        doc_id = str(failure.get("doc_id") or "")
        template_tag = str(failure.get("template_tag") or "")
        chunk_count = int(failure.get("chunk_count") or 0)
        batch_index = int(failure.get("batch_index") or 0) + 1
        total_batches = int(failure.get("total_batches") or 0)
        attempts = int(failure.get("attempts") or 0)
        payload_bytes = int(failure.get("payload_bytes") or 0)
        error = str(failure.get("error") or "unknown error")
        super().__init__(
            "batch "
            f"{batch_index}/{total_batches} "
            f"doc={doc_id} tag={template_tag or '_shared'} "
            f"chunks={chunk_count} payload_bytes={payload_bytes} "
            f"attempts={attempts}: {error}"
        )


def _encode_chunk_for_wire(chunk: dict[str, Any]) -> dict[str, Any]:
    wire = {k: v for k, v in chunk.items() if k != "vector"}
    vector = list(chunk["vector"])
    wire["vector_b64"] = encode_vector_b64(vector)
    wire["vector_dim"] = len(vector)
    return wire


def _estimate_chunk_bytes(chunk: dict[str, Any]) -> int:
    return len(json.dumps(chunk, ensure_ascii=False).encode("utf-8"))


def _estimate_request_payload_bytes(
    index_meta: dict[str, Any],
    group: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> int:
    return len(
        json.dumps(
            {
                "index_meta": index_meta,
                "group": group,
                "chunks": chunks,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return isinstance(exc, httpx.TransportError)


def write_error_report(entries: list[dict[str, Any]], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    requests: list[dict[str, Any]] | None = None,
    max_rows: int | None = None,
    max_payload_bytes: int | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    retry_backoff_seconds: float | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_batch: Callable[[int, int, dict[str, Any]], None] | None = None,
    on_retry: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> PushSemanticStats:
    semantic_config = config.semantic
    effective_max_rows = max_rows if max_rows is not None else semantic_config.max_rows
    effective_max_payload_bytes = (
        max_payload_bytes if max_payload_bytes is not None else semantic_config.max_payload_bytes
    )
    effective_timeout = timeout if timeout is not None else semantic_config.timeout
    effective_retries = retries if retries is not None else semantic_config.retries
    effective_retry_backoff_seconds = (
        retry_backoff_seconds
        if retry_backoff_seconds is not None
        else semantic_config.retry_backoff_seconds
    )
    requests_to_send = requests or group_chunks_for_push(
        chunks,
        max_rows=effective_max_rows,
        max_payload_bytes=effective_max_payload_bytes,
    )
    stats = PushSemanticStats()
    url = f"{config.api_base_url}/api/v1/admin/semantic/chunks/batch"
    headers = {
        "Authorization": f"Bearer {config.admin_token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=effective_timeout) as client:
        for batch_index, request_payload in enumerate(requests_to_send):
            chunk_count = len(request_payload["chunks"])
            payload_bytes = _estimate_request_payload_bytes(
                index_meta,
                request_payload["group"],
                request_payload["chunks"],
            )
            body = {
                "index_meta": index_meta,
                "group": request_payload["group"],
                "chunks": request_payload["chunks"],
            }
            attempts = 0
            while True:
                attempts += 1
                try:
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
                        on_batch(batch_index, chunk_count, data)
                    break
                except Exception as exc:  # noqa: BLE001
                    failure = {
                        "batch_index": batch_index,
                        "total_batches": len(requests_to_send),
                        "doc_id": str(request_payload["group"].get("doc_id") or ""),
                        "template_tag": str(request_payload["group"].get("template_tag") or ""),
                        "part_index": int(request_payload["group"].get("part_index") or 0),
                        "part_count": int(request_payload["group"].get("part_count") or 0),
                        "chunk_count": chunk_count,
                        "payload_bytes": payload_bytes,
                        "attempts": attempts,
                        "error": str(exc),
                        "request": body,
                    }
                    can_retry = attempts <= effective_retries and _is_retryable_exception(exc)
                    if can_retry:
                        if on_retry is not None:
                            on_retry(batch_index, attempts, failure)
                        if effective_retry_backoff_seconds > 0:
                            sleep_fn(effective_retry_backoff_seconds * attempts)
                        continue
                    stats.errors.append(failure)
                    raise SemanticPushError(failure, stats) from exc

    return stats
