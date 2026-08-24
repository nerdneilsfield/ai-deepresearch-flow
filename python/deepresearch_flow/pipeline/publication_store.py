"""Immutable formal-store adapters used by publication service."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import tempfile
from threading import RLock
from typing import Any, Protocol

from .publication_models import FormalStore, PublicationConflict, PublicationError


# Shared process-wide boundary for immutable formal writes and Snapshot/receipt
# commits.  GC takes same lock while reading references and deleting orphans.
PUBLICATION_SERIALIZATION_LOCK = RLock()


_CONTENT_DIGEST = re.compile(r"[0-9a-f]{64}")
_LOCAL_CURSOR_PREFIX = "v1l."
_WEBDAV_CURSOR_PREFIX = "v1w."


@dataclass(frozen=True)
class FormalStorePage:
    """One bounded, resumable formal-store listing page."""

    items: tuple[str, ...] = ()
    next_cursor: str | None = None
    inspected: int = 0


class FormalStoreCursorError(PublicationError):
    """A local listing cursor cannot be resumed safely."""


class _DirectoryEntries(Protocol):
    def __next__(self) -> os.DirEntry[str]: ...

    def close(self) -> None: ...


@dataclass
class _LocalPageFrame:
    relative: str
    entries: _DirectoryEntries


@dataclass
class _LocalPageTraversal:
    minimum: str | None
    frames: list[_LocalPageFrame]


def content_addressed_digest(relative_path: str) -> str | None:
    """Return digest for one pipeline-generated immutable resource path."""
    try:
        relative = safe_relative_path(relative_path)
    except ValueError:
        return None
    parts = PurePosixPath(relative).parts
    if not parts:
        return None
    prefix = parts[0]
    if prefix in {"pdf", "md"} and len(parts) == 2:
        suffix = ".pdf" if prefix == "pdf" else ".md"
        name = parts[1]
        digest = name[: -len(suffix)] if name.endswith(suffix) else ""
    elif prefix == "md_translate" and len(parts) == 3:
        name = parts[2]
        digest = name[:-3] if name.endswith(".md") else ""
    elif prefix == "summary" and len(parts) == 4:
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts[1:-1]):
            return None
        name = parts[3]
        digest = name[:-5] if name.endswith(".json") else ""
    elif prefix == "objects" and len(parts) == 2:
        digest = parts[1]
    else:
        return None
    return digest if _CONTENT_DIGEST.fullmatch(digest) else None


class LocalFormalStore:
    """Atomic local formal store that never overwrites immutable content."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._page_scope = secrets.token_urlsafe(18)
        self._page_states: dict[str, _LocalPageTraversal] = {}
        self._page_lock = RLock()

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
        self,
        *,
        max_items: int | None = None,
        after: str | None = None,
        content_addressed_only: bool = False,
    ) -> tuple[str, ...]:
        """List immutable digest-named files below this store only."""
        result: set[str] = set()
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
            if not content_addressed_only or content_addressed_digest(relative) is not None:
                result.add(relative)
        ordered = sorted(result)
        return tuple(ordered if max_items is None else ordered[:max_items])

    def list_content_addressed_page(
        self,
        *,
        max_items: int | None = None,
        after: str | None = None,
        content_addressed_only: bool = False,
        inspection_limit: int | None = None,
    ) -> FormalStorePage:
        """List one bounded DFS page using a resumable live directory stack.

        ``os.scandir`` iterators stay attached to one store instance.  The
        opaque cursor never contains paths supplied by its caller; after a
        process restart, its live iterator is unavailable and GC resets the
        cursor with a warning instead of falling back to an unbounded scan.
        """
        if max_items is not None and max_items < 0:
            raise ValueError("formal listing page size must not be negative")
        if inspection_limit is None:
            inspection_limit = max(1, max_items or 1)
        if inspection_limit < 0:
            raise ValueError("formal listing inspection limit must not be negative")

        with self._page_lock:
            traversal, previous_token = self._take_page_traversal(after)
            try:
                items, inspected = self._consume_page(
                    traversal,
                    max_items=max_items,
                    content_addressed_only=content_addressed_only,
                    inspection_limit=inspection_limit,
                )
                if traversal.frames:
                    token = secrets.token_urlsafe(18)
                    self._page_states[token] = traversal
                    next_cursor = self._encode_page_cursor(token)
                else:
                    self._close_page_traversal(traversal)
                    next_cursor = None
                del previous_token
                return FormalStorePage(tuple(items), next_cursor, inspected)
            except BaseException:
                self._close_page_traversal(traversal)
                raise

    def _take_page_traversal(
        self, cursor: str | None
    ) -> tuple[_LocalPageTraversal, str | None]:
        if cursor is None:
            self._reset_page_states()
            return self._new_page_traversal(None), None
        if not isinstance(cursor, str):
            self._reset_page_states()
            raise FormalStoreCursorError("formal listing cursor cannot be resumed safely")
        if not cursor.startswith(_LOCAL_CURSOR_PREFIX):
            self._reset_page_states()
            try:
                minimum = safe_relative_path(cursor)
            except (TypeError, ValueError) as exc:
                raise FormalStoreCursorError(
                    "formal listing cursor cannot be resumed safely"
                ) from exc
            return self._new_page_traversal(minimum), None
        try:
            token = self._decode_page_cursor(cursor)
        except FormalStoreCursorError:
            self._reset_page_states()
            raise
        traversal = self._page_states.pop(token, None)
        if traversal is None:
            self._reset_page_states()
            raise FormalStoreCursorError("formal listing cursor cannot be resumed safely")
        return traversal, token

    def _reset_page_states(self) -> None:
        states = tuple(self._page_states.values())
        self._page_states.clear()
        for traversal in states:
            self._close_page_traversal(traversal)

    def _new_page_traversal(self, minimum: str | None) -> _LocalPageTraversal:
        frame = self._open_page_frame("")
        return _LocalPageTraversal(minimum, [] if frame is None else [frame])

    def _open_page_frame(self, relative: str) -> _LocalPageFrame | None:
        path = self.root if not relative else self.root / PurePosixPath(relative)
        try:
            if path.is_symlink():
                return None
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root) or not resolved.is_dir():
                return None
            return _LocalPageFrame(relative, os.scandir(resolved))
        except OSError:
            return None

    def _consume_page(
        self,
        traversal: _LocalPageTraversal,
        *,
        max_items: int | None,
        content_addressed_only: bool,
        inspection_limit: int,
    ) -> tuple[list[str], int]:
        items: list[str] = []
        inspected = 0
        while traversal.frames and inspected < inspection_limit and (
            max_items is None or len(items) < max_items
        ):
            frame = traversal.frames[-1]
            try:
                entry = next(frame.entries)
            except (StopIteration, OSError):
                self._close_page_frame(traversal.frames.pop())
                continue
            inspected += 1
            try:
                if entry.is_symlink():
                    continue
                raw_relative = (
                    f"{frame.relative}/{entry.name}" if frame.relative else entry.name
                )
                relative = safe_relative_path(raw_relative)
                if relative != raw_relative:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    child = self._open_page_frame(relative)
                    if child is not None:
                        traversal.frames.append(child)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                candidate = Path(entry.path)
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(self.root):
                    continue
                normalized = resolved.relative_to(self.root).as_posix()
                if normalized != relative:
                    continue
                if traversal.minimum is not None and normalized <= traversal.minimum:
                    continue
                if content_addressed_only and content_addressed_digest(normalized) is None:
                    continue
            except (OSError, TypeError, ValueError):
                continue
            items.append(normalized)
        return items, inspected

    def _close_page_frame(self, frame: _LocalPageFrame) -> None:
        try:
            frame.entries.close()
        except OSError:
            pass

    def _close_page_traversal(self, traversal: _LocalPageTraversal) -> None:
        while traversal.frames:
            self._close_page_frame(traversal.frames.pop())

    def _encode_page_cursor(self, token: str) -> str:
        payload = {"v": 1, "scope": self._page_scope, "token": token}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return _LOCAL_CURSOR_PREFIX + encoded

    def _decode_page_cursor(self, cursor: str) -> str:
        try:
            if len(cursor) > 4096:
                raise ValueError
            encoded = cursor[len(_LOCAL_CURSOR_PREFIX) :]
            encoded += "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError
            if payload.get("scope") != self._page_scope:
                raise ValueError
            token = payload.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError
            return token
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            raise FormalStoreCursorError(
                "formal listing cursor cannot be resumed safely"
            ) from exc

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
        self,
        *,
        max_items: int | None = None,
        after: str | None = None,
        content_addressed_only: bool = False,
    ) -> tuple[str, ...]:
        inspection_limit = max(64, (max_items * 8) if max_items is not None else 64)
        page = self.list_content_addressed_page(
            max_items=max_items,
            after=after,
            content_addressed_only=content_addressed_only,
            inspection_limit=inspection_limit,
        )
        return page.items

    def list_content_addressed_page(
        self,
        *,
        max_items: int | None = None,
        after: str | None = None,
        content_addressed_only: bool = False,
        inspection_limit: int | None = None,
    ) -> FormalStorePage:
        """List one bounded, resumable DFS page of formal resources.

        WebDAV has no portable byte-level pagination for a Depth: 1
        PROPFIND.  This adapter therefore requests one entry at a time,
        keeps only current collection plus ``after`` in its cursor, and
        bounds both collection calls and entries consumed by this page.
        """
        if max_items is not None and max_items < 0:
            raise ValueError("formal listing page size must not be negative")
        if inspection_limit is None:
            inspection_limit = max(1, max_items or 1)
        if inspection_limit < 0:
            raise ValueError("formal listing inspection limit must not be negative")
        collection, entry_after, minimum = self._decode_page_cursor(after)
        items: list[str] = []
        inspected = 0
        while inspected < inspection_limit and (
            max_items is None or len(items) < max_items
        ):
            normalized = self._list_next_entry(collection, entry_after)
            inspected += 1
            if normalized is None:
                if not collection:
                    return FormalStorePage(tuple(items), None, inspected)
                completed = collection
                collection = completed.rpartition("/")[0]
                entry_after = completed
                continue
            entry_after = normalized.rstrip("/")
            if normalized.endswith("/"):
                collection = normalized.rstrip("/")
                entry_after = None
                continue
            if minimum is not None and normalized <= minimum:
                continue
            if content_addressed_only and content_addressed_digest(normalized) is None:
                continue
            items.append(normalized)
        next_cursor = self._encode_page_cursor(collection, entry_after, minimum)
        return FormalStorePage(tuple(items), next_cursor, inspected)

    def _remote_path(self, relative: str) -> str:
        if not relative:
            return self.prefix
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def _normalize_listing_value(self, raw: object, collection: str) -> str | None:
        value = str(raw).replace("\\", "/").lstrip("/")
        if self.prefix:
            prefix = self.prefix.rstrip("/") + "/"
            if value == self.prefix or not value.startswith(prefix):
                return None
            relative = value[len(prefix) :]
        else:
            relative = value
        if not relative:
            return None
        normalized = relative.rstrip("/") + ("/" if value.endswith("/") else "")
        safe_relative_path(normalized.rstrip("/"))
        parent = f"{collection.rstrip('/')}/" if collection else ""
        child = normalized[len(parent) :] if normalized.startswith(parent) else ""
        if not child or (normalized.endswith("/") and "/" in child.rstrip("/")):
            return None
        return normalized

    def _list_next_entry(self, collection: str, after: str | None) -> str | None:
        listing = getattr(self.storage, "list", None)
        if not callable(listing):
            raise PublicationError(
                "WebDAV formal GC requires bounded list(path, max_items, after) capability"
            )
        remote_collection = self._remote_path(collection)
        remote_after = self._remote_path(after) if after else None
        try:
            raw_values = listing(
                remote_collection,
                max_items=1,
                after=remote_after,
            )
        except TypeError as exc:
            raise PublicationError(
                "WebDAV formal GC requires bounded list(path, max_items, after) capability"
            ) from exc
        values = {
            normalized
            for raw in raw_values
            if (normalized := self._normalize_listing_value(raw, collection)) is not None
            and (after is None or normalized.rstrip("/") > after)
        }
        return min(values) if values else None

    @staticmethod
    def _decode_page_cursor(
        cursor: str | None,
    ) -> tuple[str, str | None, str | None]:
        if cursor is None:
            return "", None, None
        if not cursor.startswith(_WEBDAV_CURSOR_PREFIX):
            # Compatibility with the original public ``after`` path API.
            return "", None, safe_relative_path(cursor)
        try:
            encoded = cursor[len(_WEBDAV_CURSOR_PREFIX) :]
            encoded += "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            collection = payload.get("c") or ""
            entry_after = payload.get("a")
            minimum = payload.get("m")
            if not isinstance(collection, str) or not isinstance(entry_after, (str, type(None))) or not isinstance(minimum, (str, type(None))):
                raise ValueError
            if collection:
                collection = safe_relative_path(collection)
            if entry_after is not None:
                entry_after = safe_relative_path(entry_after)
            if minimum is not None:
                minimum = safe_relative_path(minimum)
            return collection, entry_after, minimum
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            raise ValueError("formal WebDAV listing cursor is invalid") from exc

    @staticmethod
    def _encode_page_cursor(
        collection: str,
        entry_after: str | None,
        minimum: str | None,
    ) -> str | None:
        if not collection and entry_after is None and minimum is None:
            return None
        payload = {"c": collection, "a": entry_after, "m": minimum}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return _WEBDAV_CURSOR_PREFIX + encoded

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
    "FormalStorePage",
    "FormalStoreCursorError",
    "LocalFormalStore",
    "MirroredFormalStore",
    "WebDavFormalStore",
    "safe_relative_path",
    "content_addressed_digest",
    "PUBLICATION_SERIALIZATION_LOCK",
]
