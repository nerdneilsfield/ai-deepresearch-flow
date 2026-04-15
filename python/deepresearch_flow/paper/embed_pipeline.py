"""Orchestration for embedding pipeline."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

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
    open_store,
    read_group_hashes,
    update_index_meta_stats,
    validate_index_meta,
    write_chunks,
)

logger = logging.getLogger(__name__)
_SHARED_KEY = "_shared"


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
        dimensions=embedding_config.dimensions,
        normalized=embedding_config.normalized,
        provider=provider_config.name,
    )

    db = open_store(vector_dir)
    existing_hashes = read_group_hashes(db)

    rows_to_write: list[ChunkRow] = []
    groups_to_delete: list[tuple[str, str]] = []
    source_group_keys: set[tuple[str, str]] = set()

    for doc in docs:
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
            if existing_hashes.get((doc.doc_id, template_key)) == group_hash:
                continue
            if (doc.doc_id, template_key) in existing_hashes:
                groups_to_delete.append((doc.doc_id, template_key))

            type_counters: dict[str, int] = {}
            for idx, chunk in enumerate(group_chunks):
                chunk_type_label = (
                    f"{chunk.chunk_type}_{chunk.lang}"
                    if chunk.chunk_type == "translated_md" and chunk.lang
                    else chunk.chunk_type
                )
                group_chunk_index = type_counters.get(chunk_type_label, 0)
                type_counters[chunk_type_label] = group_chunk_index + 1
                rows_to_write.append(
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

    orphan_keys = set(existing_hashes) - source_group_keys

    if not rows_to_write:
        logger.info("No new chunks to embed.")
        update_index_meta_stats(vector_dir, db)
        return

    texts = [row.text for row in rows_to_write]
    vectors: list[list[float]] = []
    import httpx

    async with httpx.AsyncClient() as client:
        for start in range(0, len(texts), embedding_config.batch_size):
            batch = texts[start : start + embedding_config.batch_size]
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
            vectors.extend(result.vectors)

    final_rows = [
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
        for row, vector in zip(rows_to_write, vectors, strict=True)
    ]
    delete_groups(db, groups_to_delete + list(orphan_keys))
    write_chunks(db, final_rows, dimensions=embedding_config.dimensions)
    update_index_meta_stats(vector_dir, db)
    logger.info("Embedded %d chunks across %d documents", len(final_rows), len(docs))
