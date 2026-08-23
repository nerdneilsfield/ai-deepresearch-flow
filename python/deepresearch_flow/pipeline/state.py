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
        "queued", "running", "needs_attention", "review_ready", "failed", "cancelled",
        "rejected", "publish_queued", "publishing", "indexing", "published",
        "published_with_warning",
    }
)
STEP_NAMES = ("ocr", "extract", "translate")
_TERMINAL = {"published", "published_with_warning", "rejected", "cancelled"}
_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "rejected"},
    "running": {"needs_attention", "review_ready", "failed", "cancelled", "publish_queued"},
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

    def __init__(self, db_path: str | Path, *, lease_seconds: int = 300, heartbeat_seconds: int = 30):
        self.db_path = Path(db_path)
        self.lease_seconds = int(lease_seconds)
        self.heartbeat_seconds = int(heartbeat_seconds)
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
                    artifact_size INTEGER, error TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )

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
            db.executemany("INSERT INTO steps(job_id,name,model_key) VALUES(?,?,?)", [(job_id, name, (selected_models or {}).get(name)) for name in STEP_NAMES])
            db.commit()
        return job_id

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
            status = "running" if row["status"] in {"queued", "failed", "needs_attention"} else row["status"]
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
            db.execute("UPDATE jobs SET status=?,terminal_at=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, terminal, _stamp(_utc()), job_id))
            db.commit()
        return status

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
                db.execute("UPDATE jobs SET status='cancelled',terminal_at=?,cancel_requested=1,updated_at=? WHERE id=?", (_stamp(_utc()), _stamp(_utc()), job_id))
            elif row["status"] not in _TERMINAL:
                db.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (_stamp(_utc()), job_id))
            db.commit()
        return True

    def cancel_requested(self, job_id: str) -> bool:
        return bool(self.get_job(job_id)["cancel_requested"])

    def step_boundary(self, job_id: str, lease_token: str) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            if row["cancel_requested"]:
                db.execute("UPDATE jobs SET status='cancelled',terminal_at=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (_stamp(_utc()), _stamp(_utc()), job_id))
                db.commit()
                return "cancelled"
            db.commit()
            return row["status"]

    def record_step_success(self, job_id: str, step: str, lease_token: str, digest: str | None = None, size: int | None = None, *, path: str = "", artifact: object | None = None) -> None:
        if step not in STEP_NAMES:
            raise ValueError(f"unknown step: {step}")
        if not isinstance(artifact, Artifact) or not artifact.path.is_file():
            raise ValueError("step artifact must be a promoted Artifact")
        ArtifactStore.validate_artifact(artifact, job_id, step)
        actual_path = artifact.path.resolve()
        actual_size = actual_path.stat().st_size
        actual_digest = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        if actual_digest != artifact.digest or actual_size != artifact.size or (digest is not None and digest != actual_digest) or (size is not None and size != actual_size):
            raise ValueError("artifact metadata does not match promoted artifact")
        path = str(actual_path)
        digest, size = actual_digest, actual_size
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            attempt = db.execute("SELECT attempt FROM steps WHERE job_id=? AND name=?", (job_id, step)).fetchone()["attempt"] + 1
            db.execute("UPDATE steps SET status='complete',attempt=attempt+1,artifact_digest=?,artifact_size=? WHERE job_id=? AND name=?", (digest, size, job_id, step))
            db.execute("INSERT OR REPLACE INTO artifacts(job_id,kind,path,digest,size,created_at) VALUES(?,?,?,?,?,?)", (job_id, step, path, digest, size, _stamp(_utc())))
            db.execute("INSERT INTO step_attempts(job_id,step,attempt,status,lease_owner,started_at,finished_at,artifact_digest,artifact_size) VALUES(?,?,?,?,?,?,?,?,?)", (job_id, step, attempt, "complete", row["lease_owner"], _stamp(_utc()), _stamp(_utc()), digest, size))
            db.commit()

    def record_step_attempt(self, job_id: str, step: str, lease_token: str, status: str, *, error: str | None = None, artifact: Artifact | None = None) -> int:
        if step not in STEP_NAMES:
            raise ValueError(f"unknown step: {step}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._check_token(db, job_id, lease_token)
            attempt = db.execute("SELECT attempt FROM steps WHERE job_id=? AND name=?", (job_id, step)).fetchone()["attempt"] + 1
            digest = artifact.digest if isinstance(artifact, Artifact) else None
            size = artifact.size if isinstance(artifact, Artifact) else None
            db.execute("UPDATE steps SET attempt=? WHERE job_id=? AND name=?", (attempt, job_id, step))
            db.execute("INSERT INTO step_attempts(job_id,step,attempt,status,lease_owner,started_at,finished_at,artifact_digest,artifact_size,error) VALUES(?,?,?,?,?,?,?,?,?,?)", (job_id, step, attempt, status, row["lease_owner"], _stamp(_utc()), _stamp(_utc()), digest, size, error))
            db.commit()
        return attempt

    def list_attempts(self, job_id: str, step: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if step is None:
                rows = db.execute("SELECT * FROM step_attempts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM step_attempts WHERE job_id=? AND step=? ORDER BY id", (job_id, step)).fetchall()
        return [dict(row) for row in rows]

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
            index = STEP_NAMES.index(step)
            downstream = STEP_NAMES[index:]
            row = self._check_token(db, job_id, lease_token)
            selected = json.loads(row["selected_models"])
            selected[step] = model_key
            db.execute("UPDATE jobs SET selected_models=?,updated_at=?,revision=revision+1 WHERE id=?", (json.dumps(selected, sort_keys=True), _stamp(_utc()), job_id))
            db.executemany("UPDATE steps SET model_key=?,status='missing',artifact_digest=NULL,artifact_size=NULL WHERE job_id=? AND name=?", [(selected.get(name), job_id, name) for name in downstream])
            db.executemany("DELETE FROM artifacts WHERE job_id=? AND kind=?", [(job_id, name) for name in downstream])
            db.commit()

    def recover_expired(self, now: datetime | None = None) -> list[str]:
        stamp = _stamp(_utc(now))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE status IN ('running','publishing','indexing') AND lease_expires_at <= ?", (stamp,)).fetchall()
            ids = [row["id"] for row in rows]
            db.executemany("UPDATE jobs SET lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,status='queued',updated_at=?,revision=revision+1 WHERE id=?", [(_stamp(_utc(now)), job_id) for job_id in ids])
            db.commit()
        return ids
