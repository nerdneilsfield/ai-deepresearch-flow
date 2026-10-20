"""Import a local snapshot DB and embed DB into the databases a running server uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepresearch_flow.paper.snapshot.admin import (
    MAX_BATCH_SIZE,
    AdminConfig,
    _apply_semantic_chunk_batch,
    _read_paper_manifest,
    apply_paper_batch,
)
from deepresearch_flow.paper.snapshot.push import (
    extract_papers_from_db,
    read_local_paper_keys,
    select_papers_to_push,
)
from deepresearch_flow.paper.snapshot.push_semantic import _encode_chunk_for_wire
from deepresearch_flow.paper.snapshot.transaction import open_snapshot_connection
from deepresearch_flow.paper.vector_store import (
    SHARED_TEMPLATE_KEY,
    load_index_meta,
    open_store,
    read_chunks_for_group,
    read_group_fingerprints,
    read_vector_dimensions,
    validate_index_meta,
)


@dataclass
class PaperImportStats:
    source_total: int = 0
    added: int = 0
    updated: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    duplicates: dict[str, set[str]] = field(default_factory=dict)


def import_papers(source_db: Path, target_db: Path, *, overwrite: bool) -> PaperImportStats:
    """Apply papers missing from the target, in one transaction per batch of 200."""
    open_snapshot_connection(target_db).close()  # create or upgrade the target schema
    papers = extract_papers_from_db(source_db)
    target_papers, target_aliases = _read_paper_manifest(target_db)
    selected, duplicates = select_papers_to_push(
        papers, target_papers, target_aliases, read_local_paper_keys(source_db), overwrite
    )
    stats = PaperImportStats(source_total=len(papers), duplicates=duplicates)
    for start in range(0, len(selected), MAX_BATCH_SIZE):
        batch_stats = apply_paper_batch(
            target_db, selected[start : start + MAX_BATCH_SIZE], overwrite
        )
        stats.added += batch_stats.added
        stats.updated += batch_stats.updated
        stats.errors.extend(
            {"index": start + error["index"], "error": error["error"]}
            for error in batch_stats.errors
        )
    return stats


def check_embed_compatible(source_embed: Path, target_embed: Path) -> dict[str, Any]:
    """Return the source index_meta; raise ValueError if the target's model or dimensions differ."""
    try:
        meta = load_index_meta(source_embed)
        dimensions = int(meta["dimensions"])
    except FileNotFoundError as exc:
        raise ValueError(f"source embed db has no index_meta.json: {source_embed}") from exc
    except KeyError as exc:
        raise ValueError(f"source index_meta.json is missing field {exc}") from exc
    if (target_embed / "index_meta.json").exists():
        validate_index_meta(
            target_embed,
            model=str(meta.get("model") or ""),
            canonical_model=str(meta.get("canonical_model") or "") or None,
            dimensions=dimensions,
            normalized=bool(meta.get("normalized")),
            provider=str(meta.get("provider") or ""),
        )
    elif target_embed.exists():
        target_dimensions = read_vector_dimensions(open_store(target_embed))
        if target_dimensions is not None and target_dimensions != dimensions:
            raise ValueError(
                f"Index dimensions mismatch: target table has {target_dimensions}, "
                f"source has {dimensions}"
            )
    return meta


def import_semantic(
    source_embed: Path, target_embed: Path, index_meta: dict[str, Any], *, overwrite: bool
) -> tuple[int, int]:
    """Write groups missing from the target (with ``overwrite``, also those whose fingerprint differs).

    Returns ``(source groups, written)``.
    """
    source = open_store(source_embed)
    target = open_store(target_embed)
    source_fingerprints = read_group_fingerprints(source)
    target_fingerprints = read_group_fingerprints(target)
    pending = sorted(
        key
        for key, fingerprint in source_fingerprints.items()
        if key not in target_fingerprints or (overwrite and target_fingerprints[key] != fingerprint)
    )
    cfg = AdminConfig(snapshot_db=Path(), admin_token="", embed_db=target_embed)
    written = 0
    for doc_id, template_key in pending:
        template_tag = "" if template_key == SHARED_TEMPLATE_KEY else template_key
        chunks = [
            _encode_chunk_for_wire(chunk)
            for chunk in read_chunks_for_group(source, doc_id, template_tag)
        ]
        result = _apply_semantic_chunk_batch(
            cfg,
            index_meta=index_meta,
            doc_id=doc_id,
            template_tag=template_tag,
            dimensions=int(index_meta["dimensions"]),
            all_chunks=chunks,
        )
        if result["inserted"] or result["updated"] or result["deleted"]:
            written += 1
    return len(source_fingerprints), written
