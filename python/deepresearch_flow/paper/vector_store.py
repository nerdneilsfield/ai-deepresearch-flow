"""LanceDB vector store helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import lancedb
import pyarrow as pa

try:  # pragma: no cover - Windows fallback
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

INDEX_VERSION = 1
_SHARED_KEY = "_shared"
SHARED_TEMPLATE_KEY = _SHARED_KEY
_META_FILE = "index_meta.json"
_WRITE_LOCK_FILE = ".write.lock"
_CHUNKS_TABLE = "paper_chunks"
_WRITE_CHUNK_BATCH_SIZE = 2_000
# Measured with 1024-dim cosine: flat scan p50 is ~120ms at 100k rows, ~225ms at 200k, ~540ms at 500k.
_VECTOR_INDEX_MIN_ROWS = 150_000
# IVF_PQ ranks coarsely; refine_factor=200 kept recall@10 at 1.0 (p50 25ms at 500k rows),
# while 100 dropped to 0.85 on weakly clustered data.
_VECTOR_REFINE_FACTOR = 200
_ADMIN_SCALAR_INDICES = {
    "doc_id": "idx_chunks_doc_id",
    "template_tag": "idx_chunks_template_tag",
}
_ENSURED_ADMIN_SCALAR_INDEX_DIRS: set[str] = set()
_DEFAULT_ADMIN_SCALAR_INDEX_WAIT_TIMEOUT = timedelta(minutes=30)


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


@dataclass(frozen=True)
class AdminIngestState:
    existing_by_id: dict[str, str]
    previous_template_keys: set[str]
    had_template_elsewhere: bool
    doc_had_any_rows: bool


@dataclass(frozen=True)
class ScalarIndexEnsureResult:
    table_exists: bool
    created_names: tuple[str, ...] = field(default_factory=tuple)
    existing_names: tuple[str, ...] = field(default_factory=tuple)
    skipped_cached: bool = False


def build_chunk_id(doc_id: str, template_tag: str, chunk_type: str, chunk_index: int) -> str:
    template_key = template_tag if template_tag else _SHARED_KEY
    return f"{doc_id}_{template_key}_{chunk_type}_{chunk_index}"


def compute_group_hash(content_hashes: list[str]) -> str:
    payload = "\n".join(sorted(content_hashes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_group_fingerprint(pairs: Iterable[tuple[str, str]]) -> str:
    """Fingerprint of a group's ``(chunk id, content_hash)`` pairs, shared by server and client."""
    payload = "\n".join(sorted(f"{chunk_id}\t{content_hash}" for chunk_id, content_hash in pairs))
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


@contextmanager
def embed_write_lock(vector_dir: Path) -> Iterator[None]:
    """Cross-process exclusive lock for writing the LanceDB table and index_meta.json.

    Blocking; call it from a worker thread inside an event loop. Without ``fcntl``
    (Windows) it does not lock.
    """
    vector_dir.mkdir(parents=True, exist_ok=True)
    with open(vector_dir / _WRITE_LOCK_FILE, "a") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield  # closing the file releases the lock


def save_index_meta(vector_dir: Path, meta: dict[str, Any]) -> None:
    vector_dir.mkdir(parents=True, exist_ok=True)
    temp_path = vector_dir / f"{_META_FILE}.{uuid4().hex}.tmp"
    temp_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, vector_dir / _META_FILE)


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


def _resolved_vector_dir_key(vector_dir: Path | None) -> str | None:
    if vector_dir is None:
        return None
    return str(vector_dir.resolve())


def _db_vector_dir(db: lancedb.DBConnection) -> Path | None:
    uri = getattr(db, "uri", None)
    if not uri:
        return None
    return Path(str(uri))


def _list_index_configs(table: Any) -> list[Any]:
    response = table.list_indices()
    return list(response)


def _scalar_index_columns(table: Any) -> tuple[set[str], tuple[str, ...]]:
    indexed_columns: set[str] = set()
    index_names: list[str] = []
    for index in _list_index_configs(table):
        name = str(getattr(index, "name", "") or "")
        if name:
            index_names.append(name)
        columns = getattr(index, "columns", None) or []
        for column in columns:
            indexed_columns.add(str(column))
    return indexed_columns, tuple(index_names)


def ensure_admin_scalar_indices(
    db: lancedb.DBConnection,
    *,
    vector_dir: Path | None = None,
    wait_timeout: timedelta = _DEFAULT_ADMIN_SCALAR_INDEX_WAIT_TIMEOUT,
) -> ScalarIndexEnsureResult:
    resolved_vector_dir = vector_dir or _db_vector_dir(db)
    cache_key = _resolved_vector_dir_key(resolved_vector_dir)
    if _CHUNKS_TABLE not in _table_names(db):
        return ScalarIndexEnsureResult(table_exists=False)

    table = db.open_table(_CHUNKS_TABLE)
    indexed_columns, existing_names = _scalar_index_columns(table)
    missing_columns = {column for column in _ADMIN_SCALAR_INDICES if column not in indexed_columns}
    if (
        cache_key is not None
        and cache_key in _ENSURED_ADMIN_SCALAR_INDEX_DIRS
        and not missing_columns
    ):
        return ScalarIndexEnsureResult(
            table_exists=True,
            existing_names=existing_names,
            skipped_cached=True,
        )
    created_names: list[str] = []
    for column, index_name in _ADMIN_SCALAR_INDICES.items():
        if column in indexed_columns:
            continue
        table.create_scalar_index(column, replace=False, name=index_name)
        created_names.append(index_name)
    if created_names:
        table.wait_for_index(created_names, timeout=wait_timeout)
        indexed_columns, existing_names = _scalar_index_columns(table)

    if cache_key is not None:
        _ENSURED_ADMIN_SCALAR_INDEX_DIRS.add(cache_key)
    return ScalarIndexEnsureResult(
        table_exists=True,
        created_names=tuple(created_names),
        existing_names=existing_names,
        skipped_cached=False,
    )


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


def _chunk_row_to_dict(row: ChunkRow) -> dict[str, Any]:
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


def _chunk_row_batches(rows: list[ChunkRow], batch_size: int = _WRITE_CHUNK_BATCH_SIZE):
    for start in range(0, len(rows), batch_size):
        yield [_chunk_row_to_dict(row) for row in rows[start : start + batch_size]]


def write_chunks(db: lancedb.DBConnection, rows: list[ChunkRow], *, dimensions: int) -> None:
    if not rows:
        return
    batches = _chunk_row_batches(rows)
    if _CHUNKS_TABLE in _table_names(db):
        table = db.open_table(_CHUNKS_TABLE)
        for batch in batches:
            table.add(batch)
    else:
        first_batch = next(batches)
        table = db.create_table(_CHUNKS_TABLE, data=first_batch, schema=_chunks_schema(dimensions))
        for batch in batches:
            table.add(batch)
        ensure_admin_scalar_indices(db)


def optimize_store(vector_dir: Path) -> None:
    """Compact the chunks table, drop versions older than 10 minutes, and index vectors once large.

    Once the table has an ANN index, optimize() folds newly added rows into it.
    Runs under ``embed_write_lock``.
    """
    with embed_write_lock(vector_dir):
        db = open_store(vector_dir)
        if _CHUNKS_TABLE not in _table_names(db):
            return
        table = db.open_table(_CHUNKS_TABLE)
        for attempt in range(4):
            try:
                if (
                    table.count_rows() >= _VECTOR_INDEX_MIN_ROWS
                    and "vector" not in _scalar_index_columns(table)[0]
                ):
                    table.create_index(
                        metric="cosine", vector_column_name="vector", index_type="IVF_PQ"
                    )
                table.optimize(cleanup_older_than=timedelta(minutes=10))
                return
            except RuntimeError as exc:
                # Concurrent writes make optimize lose the commit race; observed with lancedb 0.33.
                if "Retryable commit conflict" not in str(exc) or attempt == 3:
                    raise


def read_vector_dimensions(db: lancedb.DBConnection) -> int | None:
    """Vector column length of the chunks table, or None when the table does not exist."""
    if _CHUNKS_TABLE not in _table_names(db):
        return None
    return db.open_table(_CHUNKS_TABLE).schema.field("vector").type.list_size


def delete_groups(db: lancedb.DBConnection, groups: list[tuple[str, str]]) -> None:
    if not groups or _CHUNKS_TABLE not in _table_names(db):
        return
    table = db.open_table(_CHUNKS_TABLE)
    for doc_id, template_key in groups:
        tag = "" if template_key == _SHARED_KEY else template_key
        table.delete(_group_filter(doc_id, tag))


def read_group_fingerprints(db: lancedb.DBConnection) -> dict[tuple[str, str], str]:
    if _CHUNKS_TABLE not in _table_names(db):
        return {}
    table = db.open_table(_CHUNKS_TABLE)
    rows = table.search().select(["id", "doc_id", "template_tag", "content_hash"]).to_list()
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in rows:
        template_key = row.get("template_tag") or _SHARED_KEY
        grouped.setdefault((row["doc_id"], template_key), []).append(
            (str(row["id"]), str(row["content_hash"]))
        )
    return {key: compute_group_fingerprint(pairs) for key, pairs in grouped.items()}


def read_chunk_metadata(db: lancedb.DBConnection) -> list[dict[str, Any]]:
    """All chunks without vectors and text, enough to sort, window and hash groups."""
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    return (
        table.search()
        .select(["id", "doc_id", "template_tag", "chunk_type", "chunk_index", "content_hash"])
        .to_list()
    )


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


def read_chunks_for_group(
    db: lancedb.DBConnection, doc_id: str, template_tag: str
) -> list[dict[str, Any]]:
    if _CHUNKS_TABLE not in _table_names(db):
        return []
    table = db.open_table(_CHUNKS_TABLE)
    return table.search().where(_group_filter(doc_id, template_tag)).to_list()


def read_chunks_for_groups(
    db: lancedb.DBConnection, groups: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Read chunks of ``(doc_id, template_key)`` groups, where ``_shared`` means an empty tag."""
    rows: list[dict[str, Any]] = []
    for doc_id, template_key in groups:
        tag = "" if template_key == _SHARED_KEY else template_key
        rows.extend(read_chunks_for_group(db, doc_id, tag))
    return rows


def read_admin_ingest_state(
    db: lancedb.DBConnection,
    doc_id: str,
    template_tag: str,
) -> AdminIngestState:
    if _CHUNKS_TABLE not in _table_names(db):
        return AdminIngestState(
            existing_by_id={},
            previous_template_keys=set(),
            had_template_elsewhere=False,
            doc_had_any_rows=False,
        )

    table = db.open_table(_CHUNKS_TABLE)
    doc_filter = f"doc_id = {_quote_filter_literal(doc_id)}"
    current_template_key = template_tag or SHARED_TEMPLATE_KEY
    doc_rows = (
        table.search().where(doc_filter).select(["id", "template_tag", "content_hash"]).to_list()
    )

    existing_by_id: dict[str, str] = {}
    previous_template_keys: set[str] = set()
    doc_had_any_rows = bool(doc_rows)
    had_template_elsewhere = False
    for row in doc_rows:
        row_template_key = str(row.get("template_tag") or SHARED_TEMPLATE_KEY)
        previous_template_keys.add(row_template_key)
        if row_template_key == current_template_key:
            existing_by_id[str(row["id"])] = str(row.get("content_hash") or "")

    if template_tag:
        had_template_elsewhere = bool(
            table.search()
            .where(
                "template_tag = "
                f"{_quote_filter_literal(template_tag)} AND doc_id != {_quote_filter_literal(doc_id)}"
            )
            .limit(1)
            .select(["id"])
            .to_list()
        )

    return AdminIngestState(
        existing_by_id=existing_by_id,
        previous_template_keys=previous_template_keys,
        had_template_elsewhere=had_template_elsewhere,
        doc_had_any_rows=doc_had_any_rows,
    )


def _reset_ensured_scalar_index_cache() -> None:
    _ENSURED_ADMIN_SCALAR_INDEX_DIRS.clear()


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
    q = (
        table.search(query_vector)
        .metric("cosine")
        .limit(top_k)
        .refine_factor(_VECTOR_REFINE_FACTOR)
    )
    if where:
        # Explicit: a post-filter returns fewer than top_k rows when matches lie in far partitions.
        q = q.where(where, prefilter=True)
    return q.to_list()
