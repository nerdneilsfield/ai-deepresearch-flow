"""Immutable formal-store adapters used by publication service."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from .publication_models import FormalStore, PublicationConflict, PublicationError


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


class WebDavFormalStore:
    """Write-only WebDAV formal store.

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
]
