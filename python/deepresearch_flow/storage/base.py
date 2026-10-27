"""Remote storage protocol and exceptions."""

from __future__ import annotations

from typing import Protocol
from typing import Callable
from pathlib import Path


class StorageAuthError(RuntimeError):
    """Raised when remote storage authentication fails."""


class RemoteStorage(Protocol):
    """Protocol for remote file storage backends."""

    def exists(self, remote_path: str) -> bool:
        """Check if a file exists. Raises StorageAuthError on auth failure."""
        ...

    def list(
        self,
        remote_path: str = "",
        *,
        max_items: int | None = None,
        after: str | None = None,
    ) -> tuple[str, ...]:
        """List one directory (depth 1). Paths are relative to the storage root; directories end with '/'.

        Raises StorageAuthError on auth failure, HTTPStatusError (404) if the directory is missing.
        """
        ...

    def mkdir(self, remote_path: str) -> None:
        """Ensure a directory exists (idempotent). Raises StorageAuthError on auth failure."""
        ...

    def upload(self, remote_path: str, data: bytes) -> None:
        """Upload bytes. Raises StorageAuthError on auth failure, HTTPStatusError on other failures."""
        ...

    def upload_file(
        self, remote_path: str, path: Path, *, on_bytes: Callable[[int], None]
    ) -> None:
        """Stream a local file, reporting bytes consumed by the HTTP client."""
        ...

    def close(self) -> None:
        """Release underlying resources."""
        ...

    def __enter__(self) -> RemoteStorage: ...
    def __exit__(self, *args: object) -> None: ...
