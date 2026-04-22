"""Orchestration for embedding pipeline."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
import signal

from tqdm import tqdm

from deepresearch_flow.paper.chunker import Chunk, SearchableField, chunk_fields, extract_searchable_fields
from deepresearch_flow.paper.config import PaperConfig
from deepresearch_flow.paper.embed_source import EmbedDocument, load_from_json, load_from_snapshot
from deepresearch_flow.paper.embedding import call_embedding_with_route_pool
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
_CHECKPOINT_FILE = "embed_resume_checkpoint.json"


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


def _checkpoint_path(vector_dir: Path) -> Path:
    return vector_dir / _CHECKPOINT_FILE


def _serialize_chunk_row(row: ChunkRow) -> dict[str, object]:
    return {
        "id": row.id,
        "doc_id": row.doc_id,
        "source_path": row.source_path,
        "template_tag": row.template_tag,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "field_name": row.field_name,
        "lang": row.lang,
        "text": row.text,
        "content_hash": row.content_hash,
        "vector": row.vector,
        "title": row.title,
        "year": row.year,
        "authors": row.authors,
        "venue": row.venue,
        "tags": row.tags,
    }


def _deserialize_chunk_row(data: dict[str, object]) -> ChunkRow:
    return ChunkRow(
        id=str(data["id"]),
        doc_id=str(data["doc_id"]),
        source_path=str(data["source_path"]),
        template_tag=str(data["template_tag"]),
        chunk_type=str(data["chunk_type"]),
        chunk_index=int(data["chunk_index"]),
        field_name=str(data["field_name"]),
        lang=str(data["lang"]),
        text=str(data["text"]),
        content_hash=str(data["content_hash"]),
        vector=[float(v) for v in list(data["vector"])],
        title=str(data["title"]),
        year=int(data["year"]),
        authors=str(data["authors"]),
        venue=str(data["venue"]),
        tags=str(data["tags"]),
    )


def _load_checkpoint(vector_dir: Path) -> dict[str, object] | None:
    path = _checkpoint_path(vector_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_checkpoint(
    vector_dir: Path,
    *,
    doc_id: str,
    template_key: str,
    group_hash: str,
    next_offset: int,
    staged_rows: list[ChunkRow],
) -> None:
    vector_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc_id": doc_id,
        "template_key": template_key,
        "group_hash": group_hash,
        "next_offset": next_offset,
        "staged_rows": [_serialize_chunk_row(row) for row in staged_rows],
    }
    _checkpoint_path(vector_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_checkpoint(vector_dir: Path) -> None:
    path = _checkpoint_path(vector_dir)
    if path.exists():
        path.unlink()


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
        docs = load_from_snapshot(snapshot_db, static_export_dir)
    else:
        raise ValueError("No input source provided")

    provider_config, model_config = embedding_config.resolve_active()
    route_pool = RoutePool.from_embedding_provider(config.embedding, verbose=verbose)

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
    checkpoint = _load_checkpoint(vector_dir)
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
                for doc in docs:
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
                        embed_progress.total = (embed_progress.total or 0) + len(pending_rows)
                        embed_progress.refresh()
                        should_replace_existing = template_key in existing_hashes
                        staged_rows: list[ChunkRow] = []
                        start_offset = 0
                        if (
                            should_replace_existing
                            and checkpoint is not None
                            and checkpoint.get("doc_id") == doc.doc_id
                            and checkpoint.get("template_key") == template_key
                            and checkpoint.get("group_hash") == group_hash
                        ):
                            start_offset = int(checkpoint.get("next_offset") or 0)
                            staged_rows = [
                                _deserialize_chunk_row(item)
                                for item in list(checkpoint.get("staged_rows") or [])
                                if isinstance(item, dict)
                            ]
                        for start in range(start_offset, len(pending_rows), embedding_config.batch_size):
                            batch_rows = pending_rows[start : start + embedding_config.batch_size]
                            batch = [row.text for row in batch_rows]
                            result = await call_embedding_with_route_pool(
                                route_pool=route_pool,
                                texts=batch,
                                dimensions=embedding_config.dimensions,
                                client=client,
                            )
                            if len(result.vectors) != len(batch):
                                raise ValueError(
                                    f"Embedding provider returned {len(result.vectors)} vectors for batch of {len(batch)} texts "
                                    f"(starting at offset {start})"
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
                                for row, vector in zip(batch_rows, result.vectors, strict=True)
                            ]
                            if should_replace_existing:
                                staged_rows.extend(embedded_batch_rows)
                                _save_checkpoint(
                                    vector_dir,
                                    doc_id=doc.doc_id,
                                    template_key=template_key,
                                    group_hash=group_hash,
                                    next_offset=start + len(batch_rows),
                                    staged_rows=staged_rows,
                                )
                            else:
                                write_chunks(
                                    db,
                                    embedded_batch_rows,
                                    dimensions=embedding_config.dimensions,
                                )
                                written_chunk_count += len(embedded_batch_rows)
                            embed_progress.update(len(batch_rows))
                            if stop_requested:
                                raise KeyboardInterrupt

                        if should_replace_existing:
                            delete_groups(db, [(doc.doc_id, template_key)])
                            write_chunks(db, staged_rows, dimensions=embedding_config.dimensions)
                            written_chunk_count += len(staged_rows)
                            _clear_checkpoint(vector_dir)
                            checkpoint = None
                    progress.update(1)
        finally:
            embed_progress.close()
            signal.signal(signal.SIGINT, previous_sigint)

    orphan_keys = read_group_keys(db) - source_group_keys
    if orphan_keys:
        delete_groups(db, list(orphan_keys))
    _clear_checkpoint(vector_dir)
    update_index_meta_stats(vector_dir, db)
    if written_chunk_count == 0 and not orphan_keys:
        logger.info("No new chunks to embed.")
    else:
        logger.info("Embedded %d chunks across %d documents", written_chunk_count, len(docs))
