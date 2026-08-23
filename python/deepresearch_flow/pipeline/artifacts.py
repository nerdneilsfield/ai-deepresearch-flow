"""Atomic work-artifact storage with protected path resolution and cleanup."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Artifact:
    job_id: str
    kind: str
    path: Path
    digest: str
    size: int
    root: Path | None = None
    job_directory: Path | None = None


class PendingArtifact:
    def __init__(self, store: "ArtifactStore", job_id: str, kind: str):
        self.store = store
        self.job_id = job_id
        self.kind = kind
        self.directory = store._job_directory(job_id)
        store._assert_job_directory(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".artifact-", dir=self.directory)
        self._file = os.fdopen(fd, "wb")
        self._temporary = Path(name)
        self._hasher = hashlib.sha256()
        self._size = 0
        self._aborted = False
        self._promoted = False

    def write(self, data: bytes) -> None:
        if self._aborted or self._promoted:
            raise ValueError("pending artifact is no longer writable")
        self._file.write(data)
        self._hasher.update(data)
        self._size += len(data)

    def promote(self) -> Artifact:
        if self._aborted:
            raise ValueError("pending artifact was aborted")
        if self._promoted:
            raise ValueError("pending artifact was already promoted")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        target = self.directory / f"{self.kind}-{uuid.uuid4().hex}.artifact"
        os.replace(self._temporary, target)
        self._promoted = True
        digest = self._hasher.hexdigest()
        return Artifact(self.job_id, self.kind, target, digest, self._size, self.store.work_dir, self.directory)

    def abort(self) -> None:
        """Close and remove incomplete temporary output; safe to call repeatedly."""
        if self._aborted or self._promoted:
            return
        self._aborted = True
        if not self._file.closed:
            self._file.close()
        self._temporary.unlink(missing_ok=True)

    def __enter__(self) -> "PendingArtifact":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self.abort()

    def __del__(self) -> None:
        try:
            self.abort()
        except (AttributeError, OSError):
            pass


class ArtifactStore:
    def __init__(self, work_dir: str | Path, formal_root: str | Path, *, retention_days: int = 7):
        self.work_dir = Path(work_dir).resolve()
        self.formal_root = Path(formal_root).resolve()
        if self.work_dir == self.formal_root or self.work_dir.is_relative_to(self.formal_root) or self.formal_root.is_relative_to(self.work_dir):
            raise ValueError("work and formal artifact roots must be physically separate")
        self.retention_days = int(retention_days)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.formal_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _job_key(job_id: str) -> str:
        try:
            return str(uuid.UUID(job_id))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"deepresearch-flow:{job_id}"))

    def _job_directory(self, job_id: str) -> Path:
        return self.work_dir / self._job_key(job_id)

    def _protected_job_directory(self, job_id: str) -> Path:
        return self.formal_root / self._job_key(job_id)

    def _assert_job_directory(self, directory: Path) -> None:
        if directory.exists() and directory.is_symlink():
            raise ValueError("job artifact directory must not be a symlink")
        if directory.exists() and directory.resolve().parent != self.work_dir:
            raise ValueError("job artifact directory escapes work directory")
        self._contained(directory, self.work_dir)

    def validate_artifact(self, artifact: Artifact, job_id: str, kind: str) -> None:
        """Validate artifact ownership, exact kind, and canonical work containment."""
        if artifact.job_id != job_id or artifact.kind != kind:
            raise ValueError("artifact ownership or kind mismatch")
        directory = self._job_directory(job_id)
        self._assert_job_directory(directory)
        directory = directory.resolve()
        path = artifact.path.resolve()
        if artifact.path.is_symlink() or path.parent != directory or not path.is_file():
            raise ValueError("artifact is outside its canonical work directory")
        try:
            uuid.UUID(directory.name)
        except ValueError as exc:
            raise ValueError("artifact job directory is not UUID-backed") from exc
        stem = path.name.removesuffix(".artifact")
        candidate_kind, separator, identifier = stem.rpartition("-")
        if not separator or candidate_kind != kind or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("artifact path does not match exact kind")

    def validate_protected_artifact(self, artifact: Artifact, job_id: str, kind: str) -> None:
        """Validate protected output ownership and exact formal-root containment."""
        if artifact.job_id != job_id or artifact.kind != kind:
            raise ValueError("artifact ownership or kind mismatch")
        directory = self._protected_job_directory(job_id)
        if directory.exists() and directory.is_symlink():
            raise ValueError("protected artifact directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != self.formal_root:
            raise ValueError("protected artifact directory escapes formal root")
        path = artifact.path.resolve()
        if artifact.path.is_symlink() or path.parent != directory.resolve() or not path.is_file():
            raise ValueError("protected artifact is outside canonical formal directory")
        stem = path.name.removesuffix(".artifact")
        candidate_kind, separator, identifier = stem.rpartition("-")
        if not separator or candidate_kind != kind or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("protected artifact path does not match exact kind")

    def protect(self, job_id: str, kind: str, content: bytes) -> Artifact:
        """Atomically place immutable preview content below formal root."""
        self._validate_kind(kind)
        directory = self._protected_job_directory(job_id)
        if directory.exists() and directory.is_symlink():
            raise ValueError("protected artifact directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != self.formal_root:
            raise ValueError("protected artifact directory escapes formal root")
        fd, name = tempfile.mkstemp(prefix=".artifact-", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            target = directory / f"{kind}-{uuid.uuid4().hex}.artifact"
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        digest = hashlib.sha256(content).hexdigest()
        return Artifact(job_id, kind, target, digest, len(content), self.formal_root, directory)

    def begin(self, job_id: str, kind: str) -> PendingArtifact:
        self._validate_kind(kind)
        return PendingArtifact(self, job_id, kind)

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", kind or ""):
            raise ValueError("artifact kind must be a simple name")

    def _contained(self, path: Path, root: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("artifact path escapes work directory") from exc
        return resolved

    def resolve_path(self, path: str | Path) -> Path:
        resolved = self._contained(Path(path), self.work_dir)
        relative = resolved.relative_to(self.work_dir)
        if len(relative.parts) != 2 or not relative.parts[1].endswith(".artifact"):
            raise ValueError("artifact path must identify a work artifact")
        self._assert_job_directory(resolved.parent)
        try:
            uuid.UUID(relative.parts[0])
        except ValueError as exc:
            raise ValueError("artifact path must use UUID job directory") from exc
        return resolved

    def resolve(self, job_id: str, kind: str) -> Artifact | None:
        self._validate_kind(kind)
        directory = self._contained(self._job_directory(job_id), self.work_dir)
        self._assert_job_directory(directory)
        candidates = []
        if directory.exists():
            for candidate in directory.iterdir():
                if not candidate.is_file() or not candidate.name.endswith(".artifact"):
                    continue
                stem = candidate.name.removesuffix(".artifact")
                candidate_kind, separator, identifier = stem.rpartition("-")
                if candidate_kind == kind and separator and re.fullmatch(r"[0-9a-f]{32}", identifier):
                    candidates.append(candidate)
            candidates.sort()
        if not candidates:
            if directory.exists() and any(candidate.is_file() and candidate.name.endswith(".artifact") for candidate in directory.iterdir()):
                raise FileNotFoundError(f"artifact not found: {job_id}/{kind}")
            return None
        path = self._contained(candidates[-1], self.work_dir)
        content = path.read_bytes()
        return Artifact(job_id, kind, path, hashlib.sha256(content).hexdigest(), len(content), self.work_dir, directory)

    def cleanup(self, jobs: Mapping[str, str | Mapping[str, str]], *, now: datetime | None = None, force: bool = False) -> list[str]:
        """Delete work directories for expired terminal jobs; never formal roots."""
        cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=self.retention_days)
        removed: list[str] = []
        for job_id, info in jobs.items():
            status = info if isinstance(info, str) else info.get("status", "")
            if status not in {"published", "published_with_warning", "rejected", "cancelled"}:
                continue
            directory = self._job_directory(job_id)
            self._assert_job_directory(directory)
            if not directory.exists():
                continue
            if not force:
                terminal_raw = info.get("terminal_at") if isinstance(info, Mapping) else None
                if not isinstance(terminal_raw, str) or not terminal_raw:
                    continue
                terminal_at = datetime.fromisoformat(terminal_raw).astimezone(timezone.utc)
                if terminal_at > cutoff:
                    continue
            shutil.rmtree(directory)
            removed.append(job_id)
        return removed

    def discard_job(self, job_id: str) -> None:
        """Remove one incomplete job's work directory after failed ingestion."""
        directory = self._job_directory(job_id)
        self._assert_job_directory(directory)
        if directory.exists():
            shutil.rmtree(directory)

    def discard_protected(self, job_id: str) -> None:
        """Remove protected outputs for an incomplete/cancelled job only."""
        directory = self._protected_job_directory(job_id)
        if directory.exists() and directory.is_symlink():
            raise ValueError("protected artifact directory must not be a symlink")
        if directory.exists():
            shutil.rmtree(directory)
