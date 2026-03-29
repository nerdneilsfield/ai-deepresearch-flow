"""Remote storage protocol and exceptions."""

from __future__ import annotations

from typing import Protocol


class StorageAuthError(RuntimeError):
    """Raised when remote storage authentication fails."""


class RemoteStorage(Protocol):
    """Protocol for remote file storage backends."""

    def exists(self, remote_path: str) -> bool:
        """Check if a file exists. Raises StorageAuthError on auth failure."""
        ...

    def mkdir(self, remote_path: str) -> None:
        """Ensure a directory exists (idempotent). Raises StorageAuthError on auth failure."""
        ...

    def upload(self, remote_path: str, data: bytes) -> None:
        """Upload bytes. Raises StorageAuthError on auth failure, HTTPStatusError on other failures."""
        ...

    def close(self) -> None:
        """Release underlying resources."""
        ...

    def __enter__(self) -> RemoteStorage: ...
    def __exit__(self, *args: object) -> None: ...
