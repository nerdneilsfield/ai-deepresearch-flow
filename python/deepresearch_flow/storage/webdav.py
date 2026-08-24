"""WebDAV remote storage implementation."""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

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

    def _url_for(
        self,
        remote_path: str,
        *,
        trailing_slash: bool = False,
        allow_empty: bool = False,
    ) -> str:
        path = str(remote_path or "").replace("\\", "/").strip("/")
        parts = [part for part in path.split("/") if part]
        if (not parts and not allow_empty) or any(part == ".." for part in parts):
            raise ValueError("remote_path must be a relative path without parent traversal")
        if not parts:
            return f"{self._base_url}/"
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

    def download(self, remote_path: str) -> bytes:
        """GET one remote object for digest verification during safe GC."""
        resp = self._client.get(self._url_for(remote_path))
        self._check_auth(resp)
        if resp.status_code == 200:
            return bytes(resp.content)
        resp.raise_for_status()
        return b""  # pragma: no cover - raise_for_status always raises here

    def list(self, remote_path: str = "") -> tuple[str, ...]:
        """List one WebDAV collection using bounded ``Depth: 1`` PROPFIND.

        Returned paths are relative to configured WebDAV endpoint and include
        a trailing slash for collections.  Callers can recurse explicitly.
        """
        response = self._client.request(
            "PROPFIND",
            self._url_for(remote_path, trailing_slash=True, allow_empty=True),
            headers={"Depth": "1", "Content-Type": "application/xml"},
            content=(
                b"<?xml version='1.0' encoding='utf-8' ?>"
                b"<d:propfind xmlns:d='DAV:'><d:resourcetype/></d:propfind>"
            ),
        )
        self._check_auth(response)
        if response.status_code not in (200, 207):
            response.raise_for_status()
        try:
            document = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise RuntimeError("WebDAV listing response is invalid") from exc
        base_path = urlparse(self._base_url).path.rstrip("/")
        entries: list[str] = []
        for item in document.iter():
            if item.tag.rsplit("}", 1)[-1] != "response":
                continue
            href = next(
                (
                    child.text
                    for child in item
                    if child.tag.rsplit("}", 1)[-1] == "href" and child.text
                ),
                None,
            )
            if not href:
                continue
            parsed = urlparse(href)
            path = unquote(parsed.path)
            if base_path and path.startswith(base_path + "/"):
                relative = path[len(base_path) + 1 :]
            else:
                relative = path.strip("/")
            if not relative:
                continue
            is_collection = any(
                child.tag.rsplit("}", 1)[-1] == "collection"
                for parent in item.iter()
                for child in parent
            )
            entries.append(relative.rstrip("/") + ("/" if is_collection else ""))
        return tuple(sorted(set(entries)))

    def delete(self, remote_path: str) -> None:
        """DELETE one remote object; missing object is already collected."""
        response = self._client.delete(self._url_for(remote_path))
        self._check_auth(response)
        if response.status_code in (200, 202, 204, 404):
            return
        response.raise_for_status()

    def modified_at(self, remote_path: str):
        """Read WebDAV ``getlastmodified`` for crash-safe GC grace windows."""
        response = self._client.request(
            "PROPFIND",
            self._url_for(remote_path),
            headers={"Depth": "0", "Content-Type": "application/xml"},
            content=(
                b"<?xml version='1.0' encoding='utf-8' ?>"
                b"<d:propfind xmlns:d='DAV:'><d:getlastmodified/></d:propfind>"
            ),
        )
        self._check_auth(response)
        if response.status_code not in (200, 207):
            response.raise_for_status()
        try:
            document = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise RuntimeError("WebDAV metadata response is invalid") from exc
        value = next(
            (
                child.text
                for child in document.iter()
                if child.tag.rsplit("}", 1)[-1] == "getlastmodified" and child.text
            ),
            None,
        )
        if not value:
            return None
        return parsedate_to_datetime(value).astimezone()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WebDavStorage:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
