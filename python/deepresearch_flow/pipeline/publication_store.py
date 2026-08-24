"""Immutable formal-store adapters used by publication service."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from threading import RLock
from typing import Any

from .publication_models import FormalStore, PublicationConflict, PublicationError


# Shared process-wide boundary for immutable formal writes and Snapshot/receipt
# commits.  GC takes same lock while reading references and deleting orphans.
PUBLICATION_SERIALIZATION_LOCK = RLock()


class LocalFormalStore:
    """Atomic local formal store that never overwrites immutable content."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, relative_path: str, data: bytes) -> None:
        rel = safe_relative_path(relative_path)
        destination = (self.root / rel).resolve()
        if not destination.is_relative_to(self.root):
            raise PublicationError("formal resource path escapes configured root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                existing = destination.read_bytes()
            except OSError as exc:
                raise PublicationError(f"formal resource read failed for {rel}: {exc}") from exc
            if existing == data:
                return
            raise PublicationConflict(
                f"formal resource already exists with different content: {rel}"
            )
        fd, temporary_name = tempfile.mkstemp(prefix=".publication-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Hard-link promotion is exclusive: concurrent publishers
                # cannot replace an existing immutable object between check
                # and promotion.
                os.link(temporary, destination)
            except FileExistsError:
                existing = destination.read_bytes()
                if existing != data:
                    raise PublicationConflict(
                        f"formal resource already exists with different content: {rel}"
                    )
            finally:
                temporary.unlink(missing_ok=True)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def list_content_addressed_files(
        self, *, max_items: int | None = None, after: str | None = None
    ) -> tuple[str, ...]:
        """List immutable digest-named files below this store only."""
        result: list[str] = []
        for candidate in self.root.rglob("*"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve(strict=True)
                relative = resolved.relative_to(self.root).as_posix()
            except (OSError, ValueError):
                continue
            if after is not None and relative <= after:
                continue
            result.append(relative)
            if max_items is not None and len(result) >= max_items:
                break
        return tuple(sorted(set(result)))

    def read(self, relative_path: str) -> bytes:
        rel = safe_relative_path(relative_path)
        path = (self.root / rel).resolve(strict=False)
        if not path.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
            raise PublicationError("formal resource is unavailable")
        return path.read_bytes()

    def modified_at(self, relative_path: str):
        rel = safe_relative_path(relative_path)
        path = (self.root / rel).resolve(strict=False)
        if not path.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
            raise PublicationError("formal resource is unavailable")
        from datetime import datetime, timezone

        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)

    def delete(self, relative_path: str) -> None:
        rel = safe_relative_path(relative_path)
        path = (self.root / rel).resolve(strict=False)
        if not path.is_relative_to(self.root) or path.is_symlink():
            raise PublicationError("formal resource path escapes configured root")
        if path.exists():
            path.unlink()


class WebDavFormalStore:
    """WebDAV formal store with explicit immutable read/GC capabilities.

    The adapter does not issue a HEAD/exists request.  Publication reserves
    paper identity in Snapshot before calling this adapter; content-addressed
    resources make retries idempotent without an extra round trip.
    """

    def __init__(self, storage: Any, *, prefix: str = ""):
        self.storage = storage
        self.prefix = safe_relative_path(prefix) if prefix else ""

    def put(self, relative_path: str, data: bytes) -> None:
        rel = safe_relative_path(relative_path)
        target = f"{self.prefix}/{rel}" if self.prefix else rel
        mkdir = getattr(self.storage, "mkdir", None)
        if callable(mkdir):
            parts = target.split("/")[:-1]
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                mkdir(current)
        self.storage.upload(target, data)

    def list_content_addressed_files(
        self, *, max_items: int | None = None, after: str | None = None
    ) -> tuple[str, ...]:
        listing = getattr(self.storage, "list", None)
        if not callable(listing):
            raise PublicationError("WebDAV formal store does not support safe listing")
        pending = [self.prefix] if self.prefix else [""]
        files: set[str] = set()
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            try:
                raw_values = listing(current, max_items=max_items)
            except TypeError:
                raw_values = listing(current)
            for raw in raw_values:
                value = str(raw).replace("\\", "/").lstrip("/")
                if self.prefix:
                    prefix = self.prefix.rstrip("/") + "/"
                    if value == self.prefix:
                        continue
                    if not value.startswith(prefix):
                        continue
                    relative = value[len(prefix) :]
                else:
                    relative = value
                if value.endswith("/"):
                    if relative:
                        safe_relative_path(relative.rstrip("/"))
                        pending.append(value.rstrip("/"))
                    continue
                if relative:
                    if after is not None and relative <= after:
                        continue
                    files.add(safe_relative_path(relative))
                    if max_items is not None and len(files) >= max_items:
                        return tuple(sorted(files))
        return tuple(sorted(files))

    def read(self, relative_path: str) -> bytes:
        rel = safe_relative_path(relative_path)
        target = f"{self.prefix}/{rel}" if self.prefix else rel
        download = getattr(self.storage, "download", None)
        if not callable(download):
            raise PublicationError("WebDAV formal store does not support safe reads")
        return bytes(download(target))

    def modified_at(self, relative_path: str):
        getter = getattr(self.storage, "modified_at", None)
        if not callable(getter):
            return None
        rel = safe_relative_path(relative_path)
        target = f"{self.prefix}/{rel}" if self.prefix else rel
        return getter(target)

    def delete(self, relative_path: str) -> None:
        rel = safe_relative_path(relative_path)
        target = f"{self.prefix}/{rel}" if self.prefix else rel
        delete = getattr(self.storage, "delete", None)
        if not callable(delete):
            raise PublicationError("WebDAV formal store does not support safe deletion")
        delete(target)


class MirroredFormalStore:
    """Write immutable resources to primary publication and local cache."""

    def __init__(self, primary: FormalStore, cache: FormalStore):
        self.primary = primary
        self.cache = cache

    def put(self, relative_path: str, data: bytes) -> None:
        # Cache first guarantees a WebDAV publication always has enough local
        # content to reconstruct an index-only retry after private cleanup.
        self.cache.put(relative_path, data)
        self.primary.put(relative_path, data)


def safe_relative_path(value: str) -> str:
    """Validate and normalize one formal object path."""
    normalized = str(value).replace("\\", "/")
    if "\x00" in normalized or not normalized or normalized.startswith("/"):
        raise ValueError("publication resource path must be relative and traversal-free")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or re.fullmatch(r"[A-Za-z]:", path.parts[0] if path.parts else "")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("publication resource path must be relative and traversal-free")
    return path.as_posix()


__all__ = [
    "FormalStore",
    "LocalFormalStore",
    "MirroredFormalStore",
    "WebDavFormalStore",
    "safe_relative_path",
    "PUBLICATION_SERIALIZATION_LOCK",
]
