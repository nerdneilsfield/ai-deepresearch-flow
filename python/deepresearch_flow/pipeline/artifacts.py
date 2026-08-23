"""Atomic work-artifact storage with protected path resolution and cleanup."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Artifact:
    job_id: str
    kind: str
    path: Path
    digest: str
    size: int


class PendingArtifact:
    def __init__(self, store: "ArtifactStore", job_id: str, kind: str):
        self.store = store
        self.job_id = job_id
        self.kind = kind
        self.directory = store._job_directory(job_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".artifact-", dir=self.directory)
        self._file = os.fdopen(fd, "wb")
        self._temporary = Path(name)

    def write(self, data: bytes) -> None:
        self._file.write(data)

    def promote(self) -> Artifact:
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        target = self.directory / f"{self.kind}-{uuid.uuid4().hex}.artifact"
        os.replace(self._temporary, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return Artifact(self.job_id, self.kind, target, digest, target.stat().st_size)

    def __del__(self) -> None:
        try:
            if not self._file.closed:
                self._file.close()
            self._temporary.unlink(missing_ok=True)
        except (AttributeError, OSError):
            pass


class ArtifactStore:
    def __init__(self, work_dir: str | Path, formal_root: str | Path, *, retention_days: int = 7):
        self.work_dir = Path(work_dir).resolve()
        self.formal_root = Path(formal_root).resolve()
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

    def begin(self, job_id: str, kind: str) -> PendingArtifact:
        self._validate_kind(kind)
        return PendingArtifact(self, job_id, kind)

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if not kind or "/" in kind or "\\" in kind or kind in {".", ".."}:
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
        try:
            uuid.UUID(relative.parts[0])
        except ValueError as exc:
            raise ValueError("artifact path must use UUID job directory") from exc
        return resolved

    def resolve(self, job_id: str, kind: str) -> Artifact | None:
        self._validate_kind(kind)
        directory = self._contained(self._job_directory(job_id), self.work_dir)
        candidates = sorted(directory.glob(f"{kind}-*.artifact")) if directory.exists() else []
        if not candidates:
            if directory.exists() and any(directory.glob("*.artifact")):
                raise FileNotFoundError(f"artifact not found: {job_id}/{kind}")
            return None
        path = self._contained(candidates[-1], self.work_dir)
        content = path.read_bytes()
        return Artifact(job_id, kind, path, hashlib.sha256(content).hexdigest(), len(content))

    def cleanup(self, jobs: dict[str, str], *, now: datetime | None = None, force: bool = False) -> list[str]:
        """Delete work directories for expired terminal jobs; never formal roots."""
        cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=self.retention_days)
        removed: list[str] = []
        for job_id, status in jobs.items():
            if status not in {"published", "published_with_warning", "rejected", "cancelled"}:
                continue
            directory = self._job_directory(job_id)
            if not directory.exists():
                continue
            if not force:
                modified = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
                if modified > cutoff:
                    continue
            shutil.rmtree(directory)
            removed.append(job_id)
        return removed
