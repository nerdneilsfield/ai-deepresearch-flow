"""LanceDB vector store helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import lancedb
import pyarrow as pa

INDEX_VERSION = 1
_SHARED_KEY = "_shared"
_META_FILE = "index_meta.json"
_CHUNKS_TABLE = "paper_chunks"


def _quote_filter_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _group_filter(doc_id: str, template_tag: str) -> str:
    return (
        f"doc_id = {_quote_filter_literal(doc_id)} AND "
        f"template_tag = {_quote_filter_literal(template_tag)}"
    )


def _table_names(db: lancedb.DBConnection) -> list[str]:
    response = db.list_tables()
    if hasattr(response, "tables"):
        return list(response.tables)
    return list(response)


@dataclass(frozen=True)
class ChunkRow:
    id: str
    doc_id: str
    source_path: str
    template_tag: str
    chunk_type: str
    chunk_index: int
    field_name: str
    lang: str
    text: str
    content_hash: str
    vector: list[float]
    title: str
    year: int
    authors: str
    venue: str
    tags: str


def build_chunk_id(doc_id: str, template_tag: str, chunk_type: str, chunk_index: int) -> str:
    template_key = template_tag if template_tag else _SHARED_KEY
    return f"{doc_id}_{template_key}_{chunk_type}_{chunk_index}"


def compute_group_hash(content_hashes: list[str]) -> str:
    payload = "\n".join(sorted(content_hashes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def encode_vector_b64(vector: list[float]) -> str:
    """Encode a vector to base64 little-endian float32 bytes."""
    packed = struct.pack(f"<{len(vector)}f", *vector)
    return base64.b64encode(packed).decode("ascii")


def decode_vector_b64(b64: str, dimensions: int) -> list[float]:
    """Decode base64 little-endian float32 bytes into a vector."""
    raw = base64.b64decode(b64)
    expected_bytes = dimensions * 4
    if len(raw) != expected_bytes:
        raise ValueError(
            f"Vector dimension mismatch: expected {dimensions} floats "
            f"({expected_bytes} bytes), got {len(raw)} bytes"
        )
    return list(struct.unpack(f"<{dimensions}f", raw))


def save_index_meta(vector_dir: Path, meta: dict[str, Any]) -> None:
    vector_dir.mkdir(parents=True, exist_ok=True)
    (vector_dir / _META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_index_meta(vector_dir: Path) -> dict[str, Any]:
    return json.loads((vector_dir / _META_FILE).read_text(encoding="utf-8"))


def validate_index_meta(
    vector_dir: Path,
    *,
    model: str,
    canonical_model: str | None = None,
    dimensions: int,
    normalized: bool,
    provider: str = "",
) -> None:
    meta_path = vector_dir / _META_FILE
    if not meta_path.exists():
        save_index_meta(
            vector_dir,
            {
                "model": model,
                "canonical_model": canonical_model or model,
                "dimensions": dimensions,
                "normalized": normalized,
                "provider": provider,
                "index_version": INDEX_VERSION,
                "doc_count": 0,
                "template_count": 0,
                "chunk_count": 0,
                "last_updated": None,
            },
        )
        return

    meta = load_index_meta(vector_dir)
    expected_canonical_model = canonical_model or model
    actual_canonical_model = meta.get("canonical_model") or meta.get("model")
    if actual_canonical_model != expected_canonical_model:
        raise ValueError(
            f"Index model mismatch: index has '{actual_canonical_model}', config has '{expected_canonical_model}'. Use --force to rebuild."
        )
    if meta.get("dimensions") != dimensions:
        raise ValueError(
            f"Index dimensions mismatch: index has {meta.get('dimensions')}, config has {dimensions}. Use --force to rebuild."
        )
    if meta.get("normalized") != normalized:
        raise ValueError(
            f"Index normalized mismatch: index has {meta.get('normalized')}, config has {normalized}. Use --force to rebuild."
        )
    if meta.get("index_version") != INDEX_VERSION:
        raise ValueError(
            f"Index version mismatch: index has {meta.get('index_version')}, current is {INDEX_VERSION}. Use --force to rebuild."
        )


def open_store(vector_dir: Path) -> lancedb.DBConnection:
    vector_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(vector_dir))


def preflight_vector_store(vector_dir: Path, *, dimensions: int) -> None:
    parent = vector_dir.parent if vector_dir.parent != Path("") else Path(".")
    temp_dir = parent / f".lance-preflight-{uuid4().hex}"
    try:
        db = open_store(temp_dir)
        write_chunks(
            db,
            [
                ChunkRow(
                    id="preflight__shared_title_0",
                    doc_id="preflight",
                    source_path="",
                    template_tag="",
                    chunk_type="title",
                    chunk_index=0,
                    field_name="title",
                    lang="",
                    text="preflight",
                    content_hash="preflight",
                    vector=[0.0] * dimensions,
                    title="preflight",
                    year=0,
                    authors="",
                    venue="",
                    tags="",
                )
            ],
            dimensions=dimensions,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Vector store preflight failed for {vector_dir}. "
            "The target filesystem may not support LanceDB commit/rename operations."
        ) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _chunks_schema(dimensions: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("doc_id", pa.string()),
            pa.field("source_path", pa.string()),
            pa.field("template_tag", pa.string()),
            pa.field("chunk_type", pa.string()),
            pa.field("chunk_index", pa.int32()),
            pa.field("field_name", pa.string()),
            pa.field("lang", pa.string()),
            pa.field("text", pa.string()),
            pa.field("content_hash", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dimensions)),
            pa.field("title", pa.string()),
            pa.field("year", pa.int32()),
            pa.field("authors", pa.string()),
            pa.field("venue", pa.string()),
            pa.field("tags", pa.string()),
        ]
    )


def write_chunks(db: lancedb.DBConnection, rows: list[ChunkRow], *, dimensions: int) -> None:
    if not rows:
        return
    data = [
        {
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
        for row in rows
    ]
    if _CHUNKS_TABLE in _table_names(db):
        db.open_table(_CHUNKS_TABLE).add(data)
    else:
        db.create_table(_CHUNKS_TABLE, data=data, schema=_chunks_schema(dimensions))


def delete_groups(db: lancedb.DBConnection, groups: list[tuple[str, str]]) -> None:
    if not groups or _CHUNKS_TABLE not in _table_names(db):
        return
    table = db.open_table(_CHUNKS_TABLE)
    for doc_id, template_key in groups:
        tag = "" if template_key == _SHARED_KEY else template_key
        table.delete(_group_filter(doc_id, tag))


def read_group_hashes(db: lancedb.DBConnection) -> dict[tuple[str, str], str]:
    if _CHUNKS_TABLE not in _table_names(db):
        return {}
    table = db.open_table(_CHUNKS_TABLE)
    rows = table.to_arrow().to_pylist()
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        template_key = row.get("template_tag") or _SHARED_KEY
        grouped.setdefault((row["doc_id"], template_key), []).append(row["content_hash"])
    return {key: compute_group_hash(values) for key, values in grouped.items()}


def read_group_hashes_for_doc(db: lancedb.DBConnection, doc_id: str) -> dict[str, str]:
    if _CHUNKS_TABLE not in _table_names(db):
        return {}
    table = db.open_table(_CHUNKS_TABLE)
    rows = (
        table.search()
        .where(f"doc_id = {_quote_filter_literal(doc_id)}")
        .select(["template_tag", "content_hash"])
        .to_list()
    )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        template_key = row.get("template_tag") or _SHARED_KEY
        grouped.setdefault(str(template_key), []).append(str(row["content_hash"]))
    return {key: compute_group_hash(values) for key, values in grouped.items()}


def read_group_keys(db: lancedb.DBConnection) -> set[tuple[str, str]]:
    if _CHUNKS_TABLE not in _table_names(db):
        return set()
    table = db.open_table(_CHUNKS_TABLE)
    rows = table.search().select(["doc_id", "template_tag"]).to_list()
    return {
        (str(row["doc_id"]), str(row.get("template_tag") or _SHARED_KEY))
        for row in rows
        if str(row.get("doc_id") or "").strip()
    }


def read_all_chunks(db: lancedb.DBConnection) -> list[dict[str, Any]]:
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    return table.to_arrow().to_pylist()


def read_chunks_for_group(db: lancedb.DBConnection, doc_id: str, template_tag: str) -> list[dict[str, Any]]:
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    return table.search().where(_group_filter(doc_id, template_tag)).to_list()


def has_rows_for_template(
    db: lancedb.DBConnection,
    template_tag: str,
    *,
    exclude_doc_id: str | None = None,
) -> bool:
    if not template_tag or _CHUNKS_TABLE not in _table_names(db):
        return False
    table = db.open_table(_CHUNKS_TABLE)
    filter_expr = f"template_tag = {_quote_filter_literal(template_tag)}"
    if exclude_doc_id is not None:
        filter_expr += f" AND doc_id != {_quote_filter_literal(exclude_doc_id)}"
    rows = table.search().where(filter_expr).limit(1).select(["id"]).to_list()
    return bool(rows)


def scan_rows(db: lancedb.DBConnection) -> list[dict[str, Any]]:
    return read_all_chunks(db)


def update_index_meta_stats(vector_dir: Path, db: lancedb.DBConnection) -> None:
    meta = load_index_meta(vector_dir)
    rows = scan_rows(db)
    meta["doc_count"] = len({str(row.get("doc_id") or "") for row in rows if row.get("doc_id")})
    meta["template_count"] = len(
        {
            str(row.get("template_tag") or "")
            for row in rows
            if str(row.get("template_tag") or "").strip()
        }
    )
    meta["chunk_count"] = len(rows)
    meta["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_index_meta(vector_dir, meta)


def query_vector(
    db: lancedb.DBConnection,
    query_vector: list[float],
    top_k: int = 50,
    where: str | None = None,
) -> list[dict[str, Any]]:
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    q = table.search(query_vector).metric("cosine").limit(top_k)
    if where:
        q = q.where(where)
    return q.to_list()
