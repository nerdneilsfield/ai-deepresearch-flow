"""Orchestration for embedding pipeline."""

from __future__ import annotations

import json
import hashlib
import logging
import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
import signal

from tqdm import tqdm

from deepresearch_flow.paper.chunker import (
    Chunk,
    SearchableField,
    chunk_fields,
    extract_searchable_fields,
)
from deepresearch_flow.paper.config import PaperConfig
from deepresearch_flow.paper.embed_source import EmbedDocument, load_from_json, load_from_snapshot
from deepresearch_flow.paper.embedding import EmbeddingRateLimiter, call_embedding_with_route_pool
from deepresearch_flow.paper.routing import RoutePool
from deepresearch_flow.paper.vector_store import (
    ChunkRow,
    build_chunk_id,
    compute_group_hash,
    delete_groups,
    ensure_admin_scalar_indices,
    open_store,
    read_group_hashes_for_doc,
    read_group_keys,
    update_index_meta_stats,
    validate_index_meta,
    write_chunks,
)

logger = logging.getLogger(__name__)
_SHARED_KEY = "_shared"


@dataclass
class _EmbedGroupState:
    doc_id: str
    template_key: str
    group_hash: str
    should_replace_existing: bool
    pending_batch_count: int
    batch_results: dict[int, list[ChunkRow]] = field(default_factory=dict)
    finalized: bool = False


@dataclass(frozen=True)
class _EmbedBatchJob:
    group: _EmbedGroupState
    start: int
    batch_rows: list[ChunkRow]


def select_orphan_group_keys(
    existing_group_keys: Iterable[tuple[str, str]],
    source_group_keys: Iterable[tuple[str, str]],
    selected_paper_ids: Iterable[str] | None = None,
) -> set[tuple[str, str]]:
    """Return stale vector groups without deleting unrelated papers.

    Full rebuilds compare every existing group with source groups.  Filtered
    rebuilds compare only groups belonging to selected papers, preserving
    vectors for papers outside the current publication bundle.
    """
    existing = {(str(doc_id), str(template)) for doc_id, template in existing_group_keys}
    source = {(str(doc_id), str(template)) for doc_id, template in source_group_keys}
    if selected_paper_ids is None:
        return existing - source
    selected = {str(paper_id) for paper_id in selected_paper_ids}
    return {key for key in existing if key[0] in selected} - source


def _build_searchable_fields(doc: EmbedDocument) -> list[SearchableField]:
    fields: list[SearchableField] = []
    if doc.metadata.title:
        fields.append(SearchableField("title", "title", doc.metadata.title, "", ""))
    if doc.source_md:
        fields.append(SearchableField("source_md", "source_md", doc.source_md, "", ""))
    for lang, text in doc.translations.items():
        fields.append(SearchableField("translated_md", "translated_md", text, "", lang))
    for tag, records in doc.template_records.items():
        for record in records:
            fields.extend(extract_searchable_fields(record, tag))

    deduped: list[SearchableField] = []
    seen_title = False
    for field in fields:
        if field.chunk_type == "title":
            if seen_title:
                continue
            seen_title = True
        deduped.append(field)
    return deduped


def _group_chunks_by_template_key(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    groups: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        template_key = chunk.template_tag if chunk.template_tag else _SHARED_KEY
        groups.setdefault(template_key, []).append(chunk)
    return groups


def _build_group_rows(
    *,
    doc: EmbedDocument,
    group_chunks: list[Chunk],
    content_hashes: list[str],
) -> list[ChunkRow]:
    type_counters: dict[str, int] = {}
    rows: list[ChunkRow] = []
    for idx, chunk in enumerate(group_chunks):
        chunk_type_label = (
            f"{chunk.chunk_type}_{chunk.lang}"
            if chunk.chunk_type == "translated_md" and chunk.lang
            else chunk.chunk_type
        )
        group_chunk_index = type_counters.get(chunk_type_label, 0)
        type_counters[chunk_type_label] = group_chunk_index + 1
        rows.append(
            ChunkRow(
                id=build_chunk_id(
                    doc.doc_id,
                    chunk.template_tag,
                    chunk_type_label,
                    group_chunk_index,
                ),
                doc_id=doc.doc_id,
                source_path=doc.metadata.source_path,
                template_tag=chunk.template_tag,
                chunk_type=chunk.chunk_type,
                chunk_index=group_chunk_index,
                field_name=chunk.field_name,
                lang=chunk.lang,
                text=chunk.text,
                content_hash=content_hashes[idx],
                vector=[],
                title=doc.metadata.title,
                year=doc.metadata.year,
                authors=doc.metadata.authors,
                venue=doc.metadata.venue,
                tags=doc.metadata.tags,
            )
        )
    return rows


async def run_embed_pipeline(
    *,
    config: PaperConfig,
    input_paths: list[Path] | None = None,
    snapshot_db: Path | None = None,
    static_export_dir: Path | None = None,
    md_roots: list[Path] | None = None,
    md_translated_roots: list[Path] | None = None,
    vector_dir: Path,
    template_tag_override: str | None = None,
    max_concurrency_override: int | None = None,
    document_window_override: int | None = None,
    tpm_limit: int | None = None,
    rpm_limit: int | None = None,
    snapshot_paper_ids: tuple[str, ...] | None = None,
    verbose: bool = False,
) -> None:
    embedding_config = config.embedding
    if not embedding_config:
        raise ValueError("Config missing [embedding] section")

    if input_paths:
        docs = load_from_json(
            input_paths,
            template_tag_override=template_tag_override,
            md_roots=md_roots,
            md_translated_roots=md_translated_roots,
        )
    elif snapshot_db and static_export_dir:
        docs = load_from_snapshot(
            snapshot_db,
            static_export_dir,
            paper_ids=snapshot_paper_ids,
        )
    else:
        raise ValueError("No input source provided")

    provider_config, model_config = embedding_config.resolve_active()
    route_pool = RoutePool.from_embedding_provider(config.embedding, verbose=verbose)
    rate_limiter = EmbeddingRateLimiter(tpm=tpm_limit, rpm=rpm_limit)
    max_concurrency = (
        max_concurrency_override
        if max_concurrency_override is not None
        else embedding_config.max_concurrency
    )
    if max_concurrency <= 0:
        raise ValueError("Embedding max_concurrency must be positive")
    document_window = (
        document_window_override
        if document_window_override is not None
        else (
            embedding_config.document_window
            if embedding_config.document_window is not None
            else max_concurrency
        )
    )
    if document_window <= 0:
        raise ValueError("Embedding document_window must be positive")

    validate_index_meta(
        vector_dir,
        model=model_config.model_name,
        canonical_model=model_config.canonical_name,
        dimensions=embedding_config.dimensions,
        normalized=embedding_config.normalized,
        provider=provider_config.name,
    )

    db = open_store(vector_dir)
    ensure_admin_scalar_indices(db, vector_dir=vector_dir)
    source_group_keys: set[tuple[str, str]] = set()
    import httpx

    written_chunk_count = 0
    embed_progress = tqdm(total=0, desc="embed chunks", unit="chunk")
    stop_requested = False

    previous_sigint = signal.getsignal(signal.SIGINT)

    def _handle_sigint(signum, frame):  # noqa: ANN001
        nonlocal stop_requested
        if stop_requested:
            raise KeyboardInterrupt
        stop_requested = True
        logger.warning("Interrupt requested; finishing current batch and persisting progress.")

    signal.signal(signal.SIGINT, _handle_sigint)
    async with httpx.AsyncClient() as client:
        try:
            with tqdm(total=len(docs), desc="prepare chunks", unit="doc") as progress:
                for wave_doc_start in range(0, len(docs), document_window):
                    wave_docs = docs[wave_doc_start : wave_doc_start + document_window]
                    batch_jobs: list[_EmbedBatchJob] = []

                    for doc in wave_docs:
                        existing_hashes = read_group_hashes_for_doc(db, doc.doc_id)
                        fields = _build_searchable_fields(doc)
                        chunks = chunk_fields(
                            fields,
                            max_tokens=embedding_config.chunk_max_tokens,
                            overlap_tokens=embedding_config.chunk_overlap_tokens,
                        )
                        grouped = _group_chunks_by_template_key(chunks)
                        for template_key, group_chunks in grouped.items():
                            source_group_keys.add((doc.doc_id, template_key))
                            content_hashes = [
                                hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                                for chunk in group_chunks
                            ]
                            group_hash = compute_group_hash(content_hashes)
                            if existing_hashes.get(template_key) == group_hash:
                                continue

                            pending_rows = _build_group_rows(
                                doc=doc,
                                group_chunks=group_chunks,
                                content_hashes=content_hashes,
                            )
                            should_replace_existing = template_key in existing_hashes
                            batch_specs = [
                                (
                                    start,
                                    pending_rows[start : start + embedding_config.batch_size],
                                )
                                for start in range(0, len(pending_rows), embedding_config.batch_size)
                            ]
                            embed_progress.total = (embed_progress.total or 0) + sum(
                                len(batch_rows) for _, batch_rows in batch_specs
                            )
                            embed_progress.refresh()
                            group = _EmbedGroupState(
                                doc_id=doc.doc_id,
                                template_key=template_key,
                                group_hash=group_hash,
                                should_replace_existing=should_replace_existing,
                                pending_batch_count=len(batch_specs),
                            )
                            for start, batch_rows in batch_specs:
                                batch_jobs.append(
                                    _EmbedBatchJob(group=group, start=start, batch_rows=batch_rows)
                                )
                        progress.update(1)

                    semaphore = asyncio.Semaphore(max_concurrency)

                    async def embed_job(job: _EmbedBatchJob) -> None:
                        nonlocal written_chunk_count
                        async with semaphore:
                            batch = [row.text for row in job.batch_rows]
                            result = await call_embedding_with_route_pool(
                                route_pool=route_pool,
                                texts=batch,
                                dimensions=embedding_config.dimensions,
                                client=client,
                                rate_limiter=rate_limiter,
                            )
                        if len(result.vectors) != len(batch):
                            raise ValueError(
                                f"Embedding provider returned {len(result.vectors)} vectors for batch of {len(batch)} texts "
                                f"(starting at offset {job.start})"
                            )
                        embedded_batch_rows = [
                            ChunkRow(
                                id=row.id,
                                doc_id=row.doc_id,
                                source_path=row.source_path,
                                template_tag=row.template_tag,
                                chunk_type=row.chunk_type,
                                chunk_index=row.chunk_index,
                                field_name=row.field_name,
                                lang=row.lang,
                                text=row.text,
                                content_hash=row.content_hash,
                                vector=vector,
                                title=row.title,
                                year=row.year,
                                authors=row.authors,
                                venue=row.venue,
                                tags=row.tags,
                            )
                            for row, vector in zip(job.batch_rows, result.vectors, strict=True)
                        ]
                        group = job.group
                        group.batch_results[job.start] = embedded_batch_rows
                        group.pending_batch_count -= 1
                        if group.pending_batch_count == 0 and not group.finalized:
                            ordered_rows = [
                                row
                                for start in sorted(group.batch_results)
                                for row in group.batch_results[start]
                            ]
                            if group.should_replace_existing:
                                delete_groups(db, [(group.doc_id, group.template_key)])
                            write_chunks(db, ordered_rows, dimensions=embedding_config.dimensions)
                            written_chunk_count += len(ordered_rows)
                            group.finalized = True
                        embed_progress.update(len(job.batch_rows))

                    if batch_jobs:
                        tasks = [asyncio.create_task(embed_job(job)) for job in batch_jobs]
                        try:
                            await asyncio.gather(*tasks)
                        except Exception:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            raise
                    if stop_requested:
                        raise KeyboardInterrupt
        finally:
            embed_progress.close()
            signal.signal(signal.SIGINT, previous_sigint)

    orphan_keys = select_orphan_group_keys(
        read_group_keys(db), source_group_keys, snapshot_paper_ids
    )
    if orphan_keys:
        delete_groups(db, list(orphan_keys))
    update_index_meta_stats(vector_dir, db)
    if written_chunk_count == 0 and not orphan_keys:
        logger.info("No new chunks to embed.")
    else:
        logger.info("Embedded %d chunks across %d documents", written_chunk_count, len(docs))


__all__ = ["run_embed_pipeline", "select_orphan_group_keys"]
