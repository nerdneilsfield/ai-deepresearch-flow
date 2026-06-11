"""WebDAV remote storage implementation."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from deepresearch_flow.storage.base import StorageAuthError

logger = logging.getLogger(__name__)
_LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
        parsed = urlparse(url)
        if parsed.scheme == "http" and (parsed.hostname or "").lower() not in _LOCAL_HTTP_HOSTS:
            raise ValueError("WebDAV Basic Auth requires HTTPS outside localhost")
        self._base_url = url.rstrip("/")
        self._username = username
        kwargs: dict[str, Any] = {
            "auth": (username, password),
            "timeout": timeout,
        }
        if _transport is not None:
            kwargs["transport"] = _transport
        self._client = httpx.Client(**kwargs)

    def _url_for(self, remote_path: str, *, trailing_slash: bool = False) -> str:
        path = str(remote_path or "").replace("\\", "/").strip("/")
        parts = [part for part in path.split("/") if part]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("remote_path must be a relative path without parent traversal")
        encoded = "/".join(quote(part, safe="") for part in parts)
        suffix = "/" if trailing_slash else ""
        return f"{self._base_url}/{encoded}{suffix}"

    def _check_auth(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise StorageAuthError(f"WebDAV authentication failed (username: {self._username})")

    def _is_already_exists_response(self, resp: httpx.Response) -> bool:
        if resp.status_code == 405:
            return True
        if resp.status_code != 500:
            return False
        body = resp.text.lower()
        return "file exists" in body or "already exists" in body

    def exists(self, remote_path: str) -> bool:
        """HEAD -> 200=True, 404=False, 401=StorageAuthError, else raise."""
        resp = self._client.head(self._url_for(remote_path))
        self._check_auth(resp)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return False  # unreachable

    def mkdir(self, remote_path: str) -> None:
        """MKCOL -> 201/405=OK, compat-handle some 500 "file exists" responses."""
        resp = self._client.request("MKCOL", self._url_for(remote_path, trailing_slash=True))
        self._check_auth(resp)
        if resp.status_code == 201 or self._is_already_exists_response(resp):
            return
        resp.raise_for_status()

    def upload(self, remote_path: str, data: bytes) -> None:
        """PUT -> 200/201/204=OK, 401=StorageAuthError, else raise."""
        resp = self._client.put(self._url_for(remote_path), content=data)
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
