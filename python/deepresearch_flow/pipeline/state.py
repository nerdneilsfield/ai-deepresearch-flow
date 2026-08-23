"""SQLite-backed state machine for administrative pipeline jobs."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .artifacts import Artifact, ArtifactStore

JOB_STATUSES = frozenset(
    {
        "queued", "running", "batch_waiting", "needs_attention", "review_ready", "failed", "cancelled",
        "rejected", "publish_queued", "publishing", "indexing", "published",
        "published_with_warning",
    }
)
STEP_NAMES = ("ocr", "extract", "translate")
# Legacy model seam remains public; worker uses expanded immutable sequence.
PROCESSING_STEP_NAMES = (
    "ocr",
    "source_repair",
    "math_repair",
    "organize",
    "extract",
    "validation",
    "summary_repair",
    "translate",
    "translation_repair",
    "preview",
)
_MATCHING_STEP_NAMES = PROCESSING_STEP_NAMES[: PROCESSING_STEP_NAMES.index("summary_repair") + 1]
ALL_STEP_NAMES = tuple(dict.fromkeys((*STEP_NAMES, *PROCESSING_STEP_NAMES)))
_TERMINAL = {"published", "published_with_warning", "rejected", "cancelled"}
_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "rejected"},
    "running": {"batch_waiting", "needs_attention", "review_ready", "failed", "cancelled", "publish_queued"},
    "batch_waiting": {"running", "cancelled"},
    "needs_attention": {"running", "cancelled", "rejected"},
    "review_ready": {"publish_queued", "running", "cancelled", "rejected"},
    "failed": {"queued", "running", "cancelled"},
    "publish_queued": {"publishing", "cancelled"},
    "publishing": {"indexing", "failed", "cancelled"},
    "indexing": {"published", "published_with_warning", "failed", "cancelled"},
    "published": set(), "published_with_warning": set(), "rejected": set(), "cancelled": set(),
}


class LeaseError(RuntimeError):
    """Worker attempted a mutation without the currently held lease."""


class BatchMatchConflict(RuntimeError):
    """Batch inputs changed while a BibTeX match was being computed."""


@dataclass(frozen=True)
class Lease:
    job_id: str
    owner: str
    token: str
    expires_at: datetime


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat()


class PipelineState:
    """Persistent jobs, steps, leases, and artifact metadata in SQLite."""

    def __init__(self, db_path: str | Path, *, lease_seconds: int = 300, heartbeat_seconds: int = 30, artifact_store: ArtifactStore | None = None):
        self.db_path = Path(db_path)
        self.lease_seconds = int(lease_seconds)
        self.heartbeat_seconds = int(heartbeat_seconds)
        self.artifact_store = artifact_store
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, batch_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, terminal_at TEXT, lease_owner TEXT, lease_token TEXT, lease_expires_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
                    preview_digest TEXT, bundle_digest TEXT, selected_models TEXT NOT NULL DEFAULT '{}',
                    config_fingerprint TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(batch_id) REFERENCES batches(id)
                );
                CREATE TABLE IF NOT EXISTS steps (
                    job_id TEXT NOT NULL, name TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'missing', artifact_digest TEXT, artifact_size INTEGER,
                    model_key TEXT, PRIMARY KEY(job_id, name), FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                    digest TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, kind)
                );
                CREATE TABLE IF NOT EXISTS heartbeats (
                    job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS step_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, step TEXT NOT NULL,
                    attempt INTEGER NOT NULL, status TEXT NOT NULL, lease_owner TEXT,
                    started_at TEXT NOT NULL, finished_at TEXT, artifact_digest TEXT,
                    artifact_size INTEGER, error TEXT, duration_ms INTEGER,
                    error_type TEXT, retryable INTEGER,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS job_inputs (
                    job_id TEXT PRIMARY KEY, filename TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
                    doi TEXT, title TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS job_summaries (
                    job_id TEXT PRIMARY KEY, summary_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS bibtex_entries (
                    batch_id TEXT NOT NULL, entry_key TEXT NOT NULL, entry_json TEXT NOT NULL,
                    PRIMARY KEY(batch_id, entry_key), FOREIGN KEY(batch_id) REFERENCES batches(id)
                );
                CREATE TABLE IF NOT EXISTS batch_match_results (
                    batch_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES batches(id)
                );
                CREATE TABLE IF NOT EXISTS job_bibtex (
                    job_id TEXT PRIMARY KEY, entry_key TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(step_attempts)").fetchall()}
            if "duration_ms" not in columns:
                db.execute("ALTER TABLE step_attempts ADD COLUMN duration_ms INTEGER")
            if "error_type" not in columns:
                db.execute("ALTER TABLE step_attempts ADD COLUMN error_type TEXT")
            if "retryable" not in columns:
                db.execute("ALTER TABLE step_attempts ADD COLUMN retryable INTEGER")
            result_columns = {row["name"] for row in db.execute("PRAGMA table_info(batch_match_results)").fetchall()}
            if "revision" not in result_columns:
                db.execute("ALTER TABLE batch_match_results ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _invalidate_batch_matching(
        db: sqlite3.Connection,
        batch_id: str | None,
        *,
        clear_summary_job_id: str | None = None,
    ) -> None:
        if batch_id:
            db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (batch_id,))
            db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (batch_id,))
        if clear_summary_job_id is not None:
            db.execute("DELETE FROM job_summaries WHERE job_id=?", (clear_summary_job_id,))

    @staticmethod
    def _matching_retry_invalid(db: sqlite3.Connection, job_id: str) -> bool:
        """Return whether retry may change persisted matching inputs."""
        rows = db.execute(
            "SELECT name,status FROM steps WHERE job_id=?", (job_id,)
        ).fetchall()
        statuses = {str(row["name"]): str(row["status"]) for row in rows}
        failed = [name for name in PROCESSING_STEP_NAMES if statuses.get(name) == "failed"]
        if failed:
            return PROCESSING_STEP_NAMES.index(failed[0]) <= PROCESSING_STEP_NAMES.index("summary_repair")
        return any(statuses.get(name) != "complete" for name in _MATCHING_STEP_NAMES)

    @staticmethod
    def _failed_step_is_matching(db: sqlite3.Connection, job_id: str) -> bool:
        """Use recorded failed-step boundary when deciding retry eligibility."""
        rows = db.execute(
            "SELECT name,status FROM steps WHERE job_id=?", (job_id,)
        ).fetchall()
        statuses = {str(row["name"]): str(row["status"]) for row in rows}
        failed = [name for name in PROCESSING_STEP_NAMES if statuses.get(name) == "failed"]
        if not failed:
            return True
        return PROCESSING_STEP_NAMES.index(failed[0]) <= PROCESSING_STEP_NAMES.index("summary_repair")

    def create_batch(self, batch_id: str | None = None) -> str:
        batch_id = batch_id or str(uuid.uuid4())
        now = _stamp(_utc())
        with self._connect() as db:
            db.execute("INSERT INTO batches(id, created_at) VALUES (?, ?)", (batch_id, now))
        return batch_id

    def create_job(
        self,
        batch_id: str | None = None,
        *,
        job_id: str | None = None,
        selected_models: dict[str, str] | None = None,
        config_fingerprint: str = "",
    ) -> str:
        job_id = job_id or str(uuid.uuid4())
        now = _stamp(_utc())
        models = json.dumps(selected_models or {}, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if batch_id is not None:
                db.execute("INSERT OR IGNORE INTO batches(id,created_at) VALUES(?,?)", (batch_id, now))
            db.execute(
                "INSERT INTO jobs(id,batch_id,status,created_at,updated_at,selected_models,config_fingerprint) VALUES(?,?,?,?,?,?,?)",
                (job_id, batch_id, "queued", now, now, models, config_fingerprint),
            )
            if batch_id is not None:
                db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (batch_id,))
            db.executemany(
                "INSERT INTO steps(job_id,name,model_key) VALUES(?,?,?)",
                [(job_id, name, (selected_models or {}).get(name)) for name in ALL_STEP_NAMES],
            )
            db.commit()
        return job_id

    def list_batches(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT id FROM batches ORDER BY created_at, id").fetchall()
        return [row["id"] for row in rows]

    def list_job_ids(self, statuses: set[str] | frozenset[str] | None = None) -> list[str]:
        """List jobs for Supervisor scheduling in stable creation order."""
        with self._connect() as db:
            if statuses:
                values = sorted(statuses)
                placeholders = ",".join("?" for _ in values)
                rows = db.execute(
                    f"SELECT id FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at,id",
                    values,
                ).fetchall()
            else:
                rows = db.execute("SELECT id FROM jobs ORDER BY created_at,id").fetchall()
        return [str(row["id"]) for row in rows]

    def set_job_input(
        self, job_id: str, filename: str, digest: str, size: int, *, doi: str | None = None, title: str | None = None
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT batch_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                db.rollback()
                raise KeyError(job_id)
            db.execute(
                "INSERT OR REPLACE INTO job_inputs(job_id,filename,digest,size,doi,title) VALUES(?,?,?,?,?,?)",
                (job_id, filename, digest, int(size), doi, title),
            )
            if job["batch_id"]:
                db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (job["batch_id"],))
                db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (job["batch_id"],))
            db.commit()

    def get_job_input(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT filename,digest,size,doi,title FROM job_inputs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def list_batch_job_inputs(self, batch_id: str) -> list[dict[str, Any]]:
        """Return all persisted inputs in batch for one deterministic match."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT jobs.id AS job_id, job_inputs.filename, job_inputs.digest,
                       job_inputs.size, job_inputs.doi, job_inputs.title
                FROM jobs LEFT JOIN job_inputs ON job_inputs.job_id = jobs.id
                WHERE jobs.batch_id=? ORDER BY jobs.created_at, jobs.id
                """,
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows if row["filename"] is not None]

    def record_job_summary(
        self, job_id: str, summary: dict[str, Any], lease_token: str
    ) -> None:
        """Persist one completed summary while its worker lease is current."""
        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            db.execute(
                "INSERT OR REPLACE INTO job_summaries(job_id,summary_json,updated_at) VALUES(?,?,?)",
                (job_id, payload, _stamp(_utc())),
            )
            if row["batch_id"]:
                db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (row["batch_id"],))
                db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (row["batch_id"],))
            db.commit()

    def list_batch_summaries(self, batch_id: str) -> list[dict[str, Any]]:
        """Return persisted summaries in deterministic batch job order."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT jobs.id AS job_id, job_summaries.summary_json
                FROM jobs JOIN job_summaries ON job_summaries.job_id = jobs.id
                WHERE jobs.batch_id=? AND jobs.status NOT IN ('failed','cancelled','rejected')
                ORDER BY jobs.created_at, jobs.id
                """,
                (batch_id,),
            ).fetchall()
        return [
            {"job_id": str(row["job_id"]), "summary": json.loads(row["summary_json"])}
            for row in rows
        ]

    def batch_summary_ready(self, batch_id: str) -> bool:
        """Report whether every eligible job in batch has a persisted summary."""
        return bool(self.get_batch_matching_snapshot(batch_id)["ready"])

    def get_batch_matching_snapshot(self, batch_id: str) -> dict[str, Any]:
        """Read matching inputs, readiness, bindings, and revision coherently.

        A single SQLite read transaction gives a matcher one durable view.  A
        later CAS in :meth:`store_batch_match_result` decides whether that view
        is still current before any result becomes visible.
        """
        with self._connect() as db:
            db.execute("BEGIN")
            batch = db.execute("SELECT revision FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                db.rollback()
                raise KeyError(batch_id)
            revision = int(batch["revision"])
            entry_rows = db.execute(
                "SELECT entry_json FROM bibtex_entries WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
            job_rows = db.execute(
                """
                SELECT jobs.id AS job_id, jobs.status, job_inputs.filename,
                       job_inputs.digest, job_inputs.size, job_inputs.doi,
                       job_inputs.title, job_summaries.summary_json
                FROM jobs
                LEFT JOIN job_inputs ON job_inputs.job_id=jobs.id
                LEFT JOIN job_summaries ON job_summaries.job_id=jobs.id
                WHERE jobs.batch_id=?
                ORDER BY jobs.created_at, jobs.id
                """,
                (batch_id,),
            ).fetchall()
            binding_rows = db.execute(
                """
                SELECT jobs.id AS job_id, job_bibtex.entry_key
                FROM jobs JOIN job_bibtex ON job_bibtex.job_id=jobs.id
                WHERE jobs.batch_id=?
                ORDER BY jobs.created_at, jobs.id
                """,
                (batch_id,),
            ).fetchall()
            match = db.execute(
                "SELECT revision,result_json FROM batch_match_results WHERE batch_id=?", (batch_id,)
            ).fetchone()
            db.commit()

        terminal = {"failed", "cancelled", "rejected"}
        eligible_rows = [row for row in job_rows if row["status"] not in terminal]
        summaries = [
            {"job_id": str(row["job_id"]), "summary": json.loads(row["summary_json"])}
            for row in eligible_rows
            if row["summary_json"] is not None
        ]
        inputs = [
            {
                "job_id": str(row["job_id"]),
                "filename": row["filename"],
                "digest": row["digest"],
                "size": row["size"],
                "doi": row["doi"],
                "title": row["title"],
            }
            for row in job_rows
            if row["filename"] is not None
        ]
        result = None
        if match is not None and int(match["revision"]) == revision:
            result = json.loads(match["result_json"])
        return {
            "batch_id": batch_id,
            "revision": revision,
            "entries": [json.loads(row["entry_json"]) for row in entry_rows],
            "jobs": [
                {"job_id": str(row["job_id"]), "status": str(row["status"])}
                for row in eligible_rows
            ],
            "inputs": inputs,
            "summaries": summaries,
            "bindings": [dict(row) for row in binding_rows],
            "ready": bool(eligible_rows) and len(summaries) == len(eligible_rows),
            "result": result,
        }

    def list_batch_bibtex_bindings(self, batch_id: str) -> list[dict[str, Any]]:
        """Return existing bindings so new match generations preserve them."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT jobs.id AS job_id, job_bibtex.entry_key
                FROM jobs JOIN job_bibtex ON job_bibtex.job_id = jobs.id
                WHERE jobs.batch_id=?
                ORDER BY jobs.created_at, jobs.id
                """,
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_batch_match_result(self, batch_id: str) -> dict[str, Any] | None:
        """Read durable batch-wide matching result, if already computed."""
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT revision FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(batch_id)
            result = db.execute(
                "SELECT revision,result_json FROM batch_match_results WHERE batch_id=?", (batch_id,)
            ).fetchone()
            db.commit()
        if result is None or int(result["revision"]) != int(row["revision"]):
            return None
        return json.loads(result["result_json"])

    def store_batch_match_result(
        self,
        batch_id: str,
        job_id: str,
        lease_token: str,
        *,
        expected_revision: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically publish one result only for its matching revision."""
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if row["batch_id"] != batch_id:
                db.rollback()
                raise ValueError("job does not belong to batch")
            batch = db.execute("SELECT revision FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                db.rollback()
                raise KeyError(batch_id)
            current_revision = int(batch["revision"])
            if current_revision != int(expected_revision):
                db.rollback()
                raise BatchMatchConflict("batch matching inputs changed")
            stored = db.execute(
                "SELECT revision,result_json FROM batch_match_results WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if stored is None:
                db.execute(
                    "INSERT INTO batch_match_results(batch_id,revision,result_json,created_at) VALUES(?,?,?,?)",
                    (batch_id, current_revision, payload, _stamp(_utc())),
                )
                stored_payload = payload
            else:
                if int(stored["revision"]) != current_revision:
                    db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (batch_id,))
                    db.execute(
                        "INSERT INTO batch_match_results(batch_id,revision,result_json,created_at) VALUES(?,?,?,?)",
                        (batch_id, current_revision, payload, _stamp(_utc())),
                    )
                    stored_payload = payload
                else:
                    stored_payload = str(stored["result_json"])
            db.commit()
        return json.loads(stored_payload)

    def persist_bibtex_entries(self, batch_id: str, entries: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone() is None:
                db.rollback()
                raise KeyError(batch_id)
            db.execute("DELETE FROM bibtex_entries WHERE batch_id=?", (batch_id,))
            db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (batch_id,))
            db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (batch_id,))
            db.executemany(
                "INSERT INTO bibtex_entries(batch_id,entry_key,entry_json) VALUES(?,?,?)",
                [(batch_id, str(entry["key"]), json.dumps(entry, sort_keys=True)) for entry in entries],
            )
            db.commit()

    def list_bibtex_entries(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT entry_json FROM bibtex_entries WHERE batch_id=? ORDER BY rowid", (batch_id,)).fetchall()
        return [json.loads(row["entry_json"]) for row in rows]

    def bind_job_bibtex(self, job_id: str, entry_key: str | None, *, status: str = "review_ready") -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT batch_id,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                db.rollback()
                raise KeyError(job_id)
            if job["status"] not in {"needs_attention", "review_ready"}:
                db.rollback()
                raise ValueError("manual binding only allowed for needs_attention or review_ready jobs")
            lease = db.execute("SELECT lease_token FROM jobs WHERE id=?", (job_id,)).fetchone()
            if lease["lease_token"] is not None:
                db.rollback()
                raise ValueError("manual binding requires no active lease")
            if entry_key is not None and db.execute(
                "SELECT 1 FROM bibtex_entries WHERE batch_id=? AND entry_key=?", (job["batch_id"], entry_key)
            ).fetchone() is None:
                db.rollback()
                raise KeyError(entry_key)
            if status not in JOB_STATUSES:
                db.rollback()
                raise ValueError(f"unknown job status: {status}")
            db.execute("INSERT OR REPLACE INTO job_bibtex(job_id,entry_key) VALUES(?,?)", (job_id, entry_key))
            db.execute("UPDATE jobs SET status=?,revision=revision+1,updated_at=? WHERE id=?", (status, _stamp(_utc()), job_id))
            if job["batch_id"]:
                db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (job["batch_id"],))
                db.execute("UPDATE batches SET revision=revision+1 WHERE id=?", (job["batch_id"],))
            db.commit()
        return {"job_id": job_id, "entry_key": entry_key, "status": status}

    def get_job_bibtex_key(self, job_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT entry_key FROM job_bibtex WHERE job_id=?", (job_id,)).fetchone()
        return None if row is None else row["entry_key"]

    def bind_worker_bibtex(
        self,
        job_id: str,
        entry_key: str,
        lease_token: str,
        *,
        expected_batch_revision: int | None = None,
    ) -> None:
        """Persist unique automatic match while worker still owns lease."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if expected_batch_revision is not None and row["batch_id"] is not None:
                batch = db.execute("SELECT revision FROM batches WHERE id=?", (row["batch_id"],)).fetchone()
                if batch is None or int(batch["revision"]) != int(expected_batch_revision):
                    db.rollback()
                    raise BatchMatchConflict("batch matching inputs changed")
            if row["batch_id"] is None or db.execute(
                "SELECT 1 FROM bibtex_entries WHERE batch_id=? AND entry_key=?",
                (row["batch_id"], entry_key),
            ).fetchone() is None:
                db.rollback()
                raise KeyError(entry_key)
            db.execute("INSERT OR REPLACE INTO job_bibtex(job_id,entry_key) VALUES(?,?)", (job_id, entry_key))
            db.execute("UPDATE jobs SET updated_at=?,revision=revision+1 WHERE id=?", (_stamp(_utc()), job_id))
            db.commit()

    def discard_batch(self, batch_id: str) -> list[str]:
        """Delete one incomplete batch and its jobs, preserving all other batches."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = [row["id"] for row in db.execute("SELECT id FROM jobs WHERE batch_id=?", (batch_id,)).fetchall()]
            if db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone() is None:
                db.rollback()
                return []
            for table, column in (("job_inputs", "job_id"), ("job_summaries", "job_id"), ("job_bibtex", "job_id"), ("steps", "job_id"), ("artifacts", "job_id"), ("heartbeats", "job_id"), ("step_attempts", "job_id"), ("jobs", "id")):
                db.executemany(f"DELETE FROM {table} WHERE {column}=?", [(job_id,) for job_id in jobs])
            db.execute("DELETE FROM bibtex_entries WHERE batch_id=?", (batch_id,))
            db.execute("DELETE FROM batch_match_results WHERE batch_id=?", (batch_id,))
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            db.commit()
        return jobs

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["selected_models"] = json.loads(result["selected_models"])
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def _check_token(self, db: sqlite3.Connection, job_id: str, token: str | None) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if token is None or row["lease_token"] != token:
            raise LeaseError(f"lease token rejected for job {job_id}")
        if row["status"] in {"running", "publishing", "indexing"}:
            if token is None or not row["lease_expires_at"] or row["lease_expires_at"] <= _stamp(_utc()):
                raise LeaseError(f"expired lease for job {job_id}")
        return row

    def acquire_lease(self, job_id: str, owner: str, now: datetime | None = None) -> Lease | None:
        now_value = _utc(now)
        expiry = now_value + timedelta(seconds=self.lease_seconds)
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(job_id)
            old_expiry = row["lease_expires_at"]
            if old_expiry and old_expiry > _stamp(now_value):
                db.rollback()
                return None
            if row["status"] in _TERMINAL:
                db.rollback()
                return None
            if row["batch_id"]:
                takeover = bool(
                    old_expiry
                    and old_expiry <= _stamp(now_value)
                    and row["status"] in {"running", "publishing", "indexing"}
                )
                clear_summary = False
                invalidate = False
                if takeover:
                    invalidate = True
                    clear_summary = self._matching_retry_invalid(db, job_id)
                elif row["status"] == "failed":
                    invalidate = True if self._failed_step_is_matching(db, job_id) else False
                    clear_summary = invalidate
                elif row["status"] in {"needs_attention", "batch_waiting"}:
                    invalidate = True
                if invalidate:
                    self._invalidate_batch_matching(
                        db,
                        str(row["batch_id"]),
                        clear_summary_job_id=job_id if clear_summary else None,
                    )
            status = "running" if row["status"] in {"queued", "failed", "needs_attention", "batch_waiting"} else row["status"]
            db.execute(
                "UPDATE jobs SET status=?,lease_owner=?,lease_token=?,lease_expires_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                (status, owner, token, _stamp(expiry), _stamp(now_value), job_id),
            )
            db.execute("INSERT OR REPLACE INTO heartbeats(job_id,owner,at) VALUES(?,?,?)", (job_id, owner, _stamp(now_value)))
            db.commit()
        return Lease(job_id, owner, token, expiry)

    def heartbeat(self, job_id: str, lease_token: str, now: datetime | None = None) -> Lease:
        now_value = _utc(now)
        expiry = now_value + timedelta(seconds=self.lease_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if row["lease_expires_at"] <= _stamp(now_value):
                db.rollback()
                raise LeaseError(f"expired lease for job {job_id}")
            db.execute("UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE id=?", (_stamp(expiry), _stamp(now_value), job_id))
            db.execute("INSERT OR REPLACE INTO heartbeats(job_id,owner,at) VALUES(?,?,?)", (job_id, row["lease_owner"], _stamp(now_value)))
            db.commit()
        return Lease(job_id, row["lease_owner"], lease_token, expiry)

    def lease_valid(self, job_id: str, lease_token: str) -> bool:
        """Check current lease without mutating queue state."""
        try:
            with self._connect() as db:
                self._check_token(db, job_id, lease_token)
        except LeaseError:
            return False
        return True

    def release_lease(self, job_id: str, lease_token: str) -> None:
        """Release current worker lease without changing job status."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            db.execute(
                "UPDATE jobs SET lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?",
                (_stamp(_utc()), job_id),
            )
            db.commit()

    def heartbeat_metadata(self, job_id: str) -> dict[str, str] | None:
        """Return last heartbeat for black-box Supervisor observability."""
        with self._connect() as db:
            row = db.execute("SELECT owner,at FROM heartbeats WHERE job_id=?", (job_id,)).fetchone()
        return None if row is None else {"owner": str(row["owner"]), "at": str(row["at"])}

    def transition(self, job_id: str, status: str, lease_token: str | None) -> str:
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown job status: {status}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if status not in _TRANSITIONS[row["status"]]:
                db.rollback()
                raise ValueError(f"invalid transition {row['status']} -> {status}")
            terminal = _stamp(_utc()) if status in _TERMINAL else None
            clear = status not in {"running", "publishing", "indexing"}
            if row["batch_id"] and (
                row["status"] in {"failed", "cancelled", "rejected"}
                or status in {"failed", "cancelled", "rejected"}
            ) and row["status"] != status:
                self._invalidate_batch_matching(db, str(row["batch_id"]))
            db.execute("UPDATE jobs SET status=?,terminal_at=?,lease_owner=CASE WHEN ? THEN NULL ELSE lease_owner END,lease_token=CASE WHEN ? THEN NULL ELSE lease_token END,lease_expires_at=CASE WHEN ? THEN NULL ELSE lease_expires_at END,updated_at=?,revision=revision+1 WHERE id=?", (status, terminal, clear, clear, clear, _stamp(_utc()), job_id))
            db.commit()
        return status

    def admin_transition(self, job_id: str, status: str) -> str:
        """Perform an explicit non-worker state transition."""
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown job status: {status}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(job_id)
            if status not in _TRANSITIONS[row["status"]]:
                db.rollback()
                raise ValueError(f"invalid transition {row['status']} -> {status}")
            terminal = _stamp(_utc()) if status in _TERMINAL else None
            matching_requeue_handled = False
            if row["batch_id"] and status in {"queued", "running"}:
                if row["status"] == "failed":
                    if self._failed_step_is_matching(db, job_id):
                        self._invalidate_batch_matching(
                            db, str(row["batch_id"]), clear_summary_job_id=job_id
                        )
                        matching_requeue_handled = True
                elif row["status"] in {"needs_attention", "batch_waiting", "review_ready"}:
                    self._invalidate_batch_matching(db, str(row["batch_id"]))
                    matching_requeue_handled = True
            if row["batch_id"] and (
                row["status"] in {"failed", "cancelled", "rejected"}
                or status in {"failed", "cancelled", "rejected"}
            ) and row["status"] != status and not matching_requeue_handled:
                self._invalidate_batch_matching(db, str(row["batch_id"]))
            db.execute("UPDATE jobs SET status=?,terminal_at=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, terminal, _stamp(_utc()), job_id))
            db.commit()
        return status

    def queue_publication(self, job_id: str, expected_revision: int) -> dict[str, Any]:
        """CAS a reviewed Job into the durable publication queue.

        Admin callers must send the revision they displayed.  This keeps a
        stale review page from publishing a newer BibTeX binding or preview.
        The method intentionally does not acquire a worker lease; publication
        workers claim ``publish_queued`` with the normal lease protocol.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,revision FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(job_id)
            if int(row["revision"]) != int(expected_revision):
                db.rollback()
                raise ValueError("publication revision is stale")
            if str(row["status"]) != "review_ready":
                db.rollback()
                raise ValueError("publication requires review_ready job")
            now = _stamp(_utc())
            next_revision = int(row["revision"]) + 1
            db.execute(
                "UPDATE jobs SET status='publish_queued',revision=?,updated_at=? WHERE id=?",
                (next_revision, now, job_id),
            )
            db.commit()
        return {"job_id": job_id, "status": "publish_queued", "revision": next_revision}

    def request_cancel(self, job_id: str, lease_token: str | None = None) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Cancellation is an admin request. Worker-originated requests may
            # provide a lease, while a queued job has no worker lease yet.
            row = self._check_token(db, job_id, lease_token) if lease_token else db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(job_id)
            if row["status"] == "queued":
                self._invalidate_batch_matching(db, str(row["batch_id"]) if row["batch_id"] else None)
                db.execute("UPDATE jobs SET status='cancelled',terminal_at=?,cancel_requested=1,updated_at=? WHERE id=?", (_stamp(_utc()), _stamp(_utc()), job_id))
            elif row["status"] not in _TERMINAL:
                db.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (_stamp(_utc()), job_id))
            db.commit()
        return True

    def retry_indexing(self, job_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        """Requeue only vector indexing after a published warning.

        Snapshot/static receipt remains authoritative; this operation never
        invalidates processing artifacts or asks publication to rewrite them.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,revision FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(job_id)
            if expected_revision is not None and int(row["revision"]) != int(expected_revision):
                db.rollback()
                raise ValueError("indexing revision is stale")
            if str(row["status"]) != "published_with_warning":
                db.rollback()
                raise ValueError("indexing retry requires published_with_warning job")
            next_revision = int(row["revision"]) + 1
            db.execute(
                "UPDATE jobs SET status='indexing',terminal_at=NULL,revision=?,updated_at=? WHERE id=?",
                (next_revision, _stamp(_utc()), job_id),
            )
            db.commit()
        return {"job_id": job_id, "status": "indexing", "revision": next_revision}

    def cancel_requested(self, job_id: str) -> bool:
        return bool(self.get_job(job_id)["cancel_requested"])

    def step_boundary(self, job_id: str, lease_token: str) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if row["cancel_requested"]:
                self._invalidate_batch_matching(db, str(row["batch_id"]) if row["batch_id"] else None)
                db.execute("UPDATE jobs SET status='cancelled',terminal_at=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (_stamp(_utc()), _stamp(_utc()), job_id))
                db.commit()
                return "cancelled"
            db.commit()
            return row["status"]

    def record_step_success(
        self,
        job_id: str,
        step: str,
        lease_token: str,
        digest: str | None = None,
        size: int | None = None,
        *,
        path: str = "",
        artifact: object | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.record_step_success_if_active(
            job_id,
            step,
            lease_token,
            digest=digest,
            size=size,
            path=path,
            artifact=artifact,
            duration_ms=duration_ms,
        )

    def record_step_success_if_active(
        self,
        job_id: str,
        step: str,
        lease_token: str,
        digest: str | None = None,
        size: int | None = None,
        *,
        path: str = "",
        artifact: object | None = None,
        duration_ms: int | None = None,
        protected_artifacts: tuple[Artifact, ...] = (),
    ) -> bool:
        """CAS step success with lease and cancellation in one transaction.

        Returns ``False`` when cancellation won the transaction.  In that
        case no step or protected-artifact metadata is registered.
        """
        if step not in ALL_STEP_NAMES:
            raise ValueError(f"unknown step: {step}")
        if not isinstance(artifact, Artifact) or not artifact.path.is_file():
            raise ValueError("step artifact must be a promoted Artifact")
        if self.artifact_store is None:
            raise ValueError("PipelineState requires bound ArtifactStore for artifact success")
        self.artifact_store.validate_artifact(artifact, job_id, step)
        actual_path = artifact.path.resolve()
        actual_size = actual_path.stat().st_size
        actual_digest = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        if actual_digest != artifact.digest or actual_size != artifact.size or (digest is not None and digest != actual_digest) or (size is not None and size != actual_size):
            raise ValueError("artifact metadata does not match promoted artifact")
        path = str(actual_path)
        digest, size = actual_digest, actual_size
        protected: list[tuple[str, str, str, int]] = []
        for protected_artifact in protected_artifacts:
            self.artifact_store.validate_protected_artifact(
                protected_artifact, job_id, protected_artifact.kind
            )
            protected_path = protected_artifact.path.resolve()
            protected_content = protected_path.read_bytes()
            protected_digest = hashlib.sha256(protected_content).hexdigest()
            protected_size = len(protected_content)
            if protected_digest != protected_artifact.digest or protected_size != protected_artifact.size:
                raise ValueError("protected artifact metadata does not match file")
            protected.append(
                (protected_artifact.kind, str(protected_path), protected_digest, protected_size)
            )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if row["cancel_requested"]:
                db.rollback()
                return False
            attempt = db.execute("SELECT attempt FROM steps WHERE job_id=? AND name=?", (job_id, step)).fetchone()["attempt"] + 1
            db.execute("UPDATE steps SET status='complete',attempt=attempt+1,artifact_digest=?,artifact_size=? WHERE job_id=? AND name=?", (digest, size, job_id, step))
            db.execute("INSERT OR REPLACE INTO artifacts(job_id,kind,path,digest,size,created_at) VALUES(?,?,?,?,?,?)", (job_id, step, path, digest, size, _stamp(_utc())))
            db.execute("INSERT INTO step_attempts(job_id,step,attempt,status,lease_owner,started_at,finished_at,artifact_digest,artifact_size,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?)", (job_id, step, attempt, "complete", row["lease_owner"], _stamp(_utc()), _stamp(_utc()), digest, size, duration_ms if duration_ms is not None else 0))
            db.executemany(
                "INSERT OR REPLACE INTO artifacts(job_id,kind,path,digest,size,created_at) VALUES(?,?,?,?,?,?)",
                [
                    (job_id, kind, protected_path, protected_digest, protected_size, _stamp(_utc()))
                    for kind, protected_path, protected_digest, protected_size in protected
                ],
            )
            db.commit()
        return True

    def record_step_attempt(
        self,
        job_id: str,
        step: str,
        lease_token: str,
        status: str,
        *,
        error: str | None = None,
        artifact: Artifact | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        retryable: bool | None = None,
    ) -> int:
        if step not in ALL_STEP_NAMES:
            raise ValueError(f"unknown step: {step}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            attempt = db.execute("SELECT attempt FROM steps WHERE job_id=? AND name=?", (job_id, step)).fetchone()["attempt"] + 1
            digest = artifact.digest if isinstance(artifact, Artifact) else None
            size = artifact.size if isinstance(artifact, Artifact) else None
            step_status = "failed" if status == "failed" else ("cancelled" if status == "cancelled" else "missing")
            db.execute(
                "UPDATE steps SET attempt=?,status=?,artifact_digest=CASE WHEN ? IS NULL THEN artifact_digest ELSE ? END,artifact_size=CASE WHEN ? IS NULL THEN artifact_size ELSE ? END WHERE job_id=? AND name=?",
                (attempt, step_status, digest, digest, size, size, job_id, step),
            )
            db.execute("INSERT INTO step_attempts(job_id,step,attempt,status,lease_owner,started_at,finished_at,artifact_digest,artifact_size,error,duration_ms,error_type,retryable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, step, attempt, status, row["lease_owner"], _stamp(_utc()), _stamp(_utc()), digest, size, error, duration_ms, error_type, None if retryable is None else int(retryable)))
            db.commit()
        return attempt

    def list_attempts(self, job_id: str, step: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if step is None:
                rows = db.execute("SELECT * FROM step_attempts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM step_attempts WHERE job_id=? AND step=? ORDER BY id", (job_id, step)).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            if item.get("retryable") is not None:
                item["retryable"] = bool(item["retryable"])
        return result

    def step_artifact(self, job_id: str, step: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT artifact_digest,artifact_size,status FROM steps WHERE job_id=? AND name=?", (job_id, step)).fetchone()
        if row is None or row["status"] != "complete":
            return None
        return dict(row)

    def artifact_metadata(self, job_id: str, kind: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT path,digest,size FROM artifacts WHERE job_id=? AND kind=?", (job_id, kind)).fetchone()
        return dict(row) if row is not None else None

    def ensure_processing_steps(self, job_id: str, lease_token: str) -> None:
        """Add expanded worker rows to queues created by older releases."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            db.executemany(
                "INSERT OR IGNORE INTO steps(job_id,name,model_key) VALUES(?,?,?)",
                [(job_id, name, None) for name in PROCESSING_STEP_NAMES],
            )
            db.commit()

    def resume_step(self, job_id: str, lease_token: str) -> str | None:
        """Validate checkpoints and clear earliest invalid suffix atomically."""
        if self.artifact_store is None:
            raise ValueError("PipelineState requires bound ArtifactStore for resume")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            rows = {
                row["name"]: dict(row)
                for row in db.execute(
                    "SELECT name,status,artifact_digest,artifact_size FROM steps WHERE job_id=?",
                    (job_id,),
                ).fetchall()
            }
            earliest: str | None = None
            for name in PROCESSING_STEP_NAMES:
                row = rows.get(name)
                valid = bool(row and row["status"] == "complete")
                if valid:
                    try:
                        artifact = self.artifact_store.resolve(job_id, name)
                        valid = bool(
                            artifact
                            and artifact.digest == row["artifact_digest"]
                            and artifact.size == row["artifact_size"]
                        )
                    except (FileNotFoundError, ValueError, OSError):
                        valid = False
                if not valid and earliest is None:
                    earliest = name
            if earliest is not None:
                suffix = PROCESSING_STEP_NAMES[PROCESSING_STEP_NAMES.index(earliest) :]
                if PROCESSING_STEP_NAMES.index(earliest) <= PROCESSING_STEP_NAMES.index("summary_repair"):
                    has_summary = db.execute(
                        "SELECT 1 FROM job_summaries WHERE job_id=?", (job_id,)
                    ).fetchone()
                    if has_summary is not None:
                        job = db.execute("SELECT batch_id FROM jobs WHERE id=?", (job_id,)).fetchone()
                        self._invalidate_batch_matching(
                            db,
                            str(job["batch_id"]) if job and job["batch_id"] else None,
                            clear_summary_job_id=job_id,
                        )
                db.executemany(
                    "UPDATE steps SET status='missing',artifact_digest=NULL,artifact_size=NULL WHERE job_id=? AND name=?",
                    [(job_id, name) for name in suffix],
                )
                db.executemany(
                    "DELETE FROM artifacts WHERE job_id=? AND kind=?",
                    [(job_id, name) for name in suffix],
                )
            db.commit()
        return earliest

    def next_step(self, job_id: str) -> str | None:
        """Return earliest step without a complete artifact for ordinary retry."""
        with self._connect() as db:
            rows = db.execute("SELECT name,status FROM steps WHERE job_id=?", (job_id,)).fetchall()
        statuses = {row["name"]: row["status"] for row in rows}
        return next((name for name in STEP_NAMES if statuses.get(name) != "complete"), None)

    def invalidate_downstream(self, job_id: str, step: str, lease_token: str) -> None:
        current = self.get_job(job_id)["selected_models"].get(step, "")
        self.change_model(job_id, step, current, lease_token)

    def set_digests(self, job_id: str, *, preview_digest: str | None = None, bundle_digest: str | None = None, lease_token: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            db.execute("UPDATE jobs SET preview_digest=COALESCE(?,preview_digest),bundle_digest=COALESCE(?,bundle_digest),updated_at=? WHERE id=?", (preview_digest, bundle_digest, _stamp(_utc()), job_id))
            db.commit()

    def change_model(self, job_id: str, step: str, model_key: str, lease_token: str) -> None:
        if step not in STEP_NAMES:
            raise ValueError(f"unknown step: {step}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            index = PROCESSING_STEP_NAMES.index(step)
            downstream = PROCESSING_STEP_NAMES[index:]
            row = self._check_token(db, job_id, lease_token)
            selected = json.loads(row["selected_models"])
            selected[step] = model_key
            db.execute("UPDATE jobs SET selected_models=?,updated_at=?,revision=revision+1 WHERE id=?", (json.dumps(selected, sort_keys=True), _stamp(_utc()), job_id))
            db.executemany("UPDATE steps SET model_key=?,status='missing',artifact_digest=NULL,artifact_size=NULL WHERE job_id=? AND name=?", [(selected.get(name), job_id, name) for name in downstream])
            db.executemany("DELETE FROM artifacts WHERE job_id=? AND kind=?", [(job_id, name) for name in downstream])
            if step in {"ocr", "extract"} and row["batch_id"]:
                self._invalidate_batch_matching(
                    db, str(row["batch_id"]), clear_summary_job_id=job_id
                )
            db.commit()

    def register_protected_artifact(
        self,
        job_id: str,
        kind: str,
        artifact: Artifact,
        lease_token: str,
    ) -> None:
        """Record formal-root output only while current lease is held."""
        if self.artifact_store is None:
            raise ValueError("PipelineState requires bound ArtifactStore for artifact success")
        self.artifact_store.validate_protected_artifact(artifact, job_id, kind)
        actual = artifact.path.resolve()
        content = actual.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        if digest != artifact.digest or size != artifact.size:
            raise ValueError("protected artifact metadata does not match file")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_token(db, job_id, lease_token)
            db.execute(
                "INSERT OR REPLACE INTO artifacts(job_id,kind,path,digest,size,created_at) VALUES(?,?,?,?,?,?)",
                (job_id, kind, str(actual), digest, size, _stamp(_utc())),
            )
            db.commit()

    def discard_artifact(self, artifact: Artifact) -> None:
        """Remove one exact protected artifact after an uncommitted result."""
        if self.artifact_store is None:
            raise ValueError("PipelineState requires bound ArtifactStore for artifact cleanup")
        self.artifact_store.validate_protected_artifact(artifact, artifact.job_id, artifact.kind)
        artifact.path.unlink(missing_ok=True)

    def recover_expired(self, now: datetime | None = None) -> list[str]:
        stamp = _stamp(_utc(now))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,batch_id,status FROM jobs WHERE status IN ('running','publishing','indexing') AND lease_expires_at <= ?", (stamp,)).fetchall()
            ids = [row["id"] for row in rows]
            for row in rows:
                if row["batch_id"]:
                    self._invalidate_batch_matching(
                        db,
                        str(row["batch_id"]),
                        clear_summary_job_id=row["id"]
                        if self._matching_retry_invalid(db, str(row["id"]))
                        else None,
                    )
            db.executemany(
                "UPDATE jobs SET lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,status=CASE WHEN status IN ('publishing','indexing') THEN 'publish_queued' ELSE 'queued' END,updated_at=?,revision=revision+1 WHERE id=?",
                [(_stamp(_utc(now)), job_id) for job_id in ids],
            )
            db.commit()
        return ids
