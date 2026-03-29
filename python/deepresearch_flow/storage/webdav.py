"""WebDAV remote storage implementation."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from deepresearch_flow.storage.base import StorageAuthError

logger = logging.getLogger(__name__)


class WebDavStorage:
    """WebDAV-based remote storage using HTTP Basic Auth."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        timeout: float = 60.0,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = url.rstrip("/")
        self._username = username
        kwargs: dict[str, Any] = {
            "auth": (username, password),
            "timeout": timeout,
        }
        if _transport is not None:
            kwargs["transport"] = _transport
        self._client = httpx.Client(**kwargs)

    def _check_auth(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise StorageAuthError(
                f"WebDAV authentication failed (username: {self._username})"
            )

    def exists(self, remote_path: str) -> bool:
        """HEAD -> 200=True, 404=False, 401=StorageAuthError, else raise."""
        url = f"{self._base_url}/{remote_path}"
        resp = self._client.head(url)
        self._check_auth(resp)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return False  # unreachable

    def mkdir(self, remote_path: str) -> None:
        """MKCOL -> 201/405=OK, 401=StorageAuthError, else raise."""
        url = f"{self._base_url}/{remote_path}/"
        resp = self._client.request("MKCOL", url)
        self._check_auth(resp)
        if resp.status_code in (201, 405):
            return
        resp.raise_for_status()

    def upload(self, remote_path: str, data: bytes) -> None:
        """PUT -> 200/201/204=OK, 401=StorageAuthError, else raise."""
        url = f"{self._base_url}/{remote_path}"
        resp = self._client.put(url, content=data)
        self._check_auth(resp)
        if resp.status_code in (200, 201, 204):
            return
        resp.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WebDavStorage:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
