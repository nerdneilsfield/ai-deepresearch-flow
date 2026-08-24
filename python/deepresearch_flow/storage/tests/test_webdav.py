"""Tests for WebDAV remote storage implementation."""

from __future__ import annotations

import httpx
import pytest

from deepresearch_flow.storage.base import StorageAuthError
from deepresearch_flow.storage.webdav import WebDavStorage


def _mock_transport(responses: dict[str, int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        status = responses.get(request.method, 405)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


class TestExists:
    def test_true_on_200(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        assert storage.exists("pdf/abc.pdf") is True

    def test_false_on_404(self) -> None:
        transport = _mock_transport({"HEAD": 404})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        assert storage.exists("pdf/abc.pdf") is False

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"HEAD": 401})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(StorageAuthError):
            storage.exists("pdf/abc.pdf")

    def test_server_error_raises(self) -> None:
        transport = _mock_transport({"HEAD": 500})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(httpx.HTTPStatusError):
            storage.exists("pdf/abc.pdf")

    def test_forbidden_raises(self) -> None:
        transport = _mock_transport({"HEAD": 403})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(httpx.HTTPStatusError):
            storage.exists("pdf/abc.pdf")


class TestMkdir:
    def test_success_201(self) -> None:
        transport = _mock_transport({"MKCOL": 201})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        storage.mkdir("pdf")

    def test_already_exists_405(self) -> None:
        transport = _mock_transport({"MKCOL": 405})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        storage.mkdir("pdf")

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"MKCOL": 401})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(StorageAuthError):
            storage.mkdir("pdf")

    def test_server_error_raises(self) -> None:
        transport = _mock_transport({"MKCOL": 500})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(httpx.HTTPStatusError):
            storage.mkdir("pdf")

    def test_file_exists_500_is_treated_as_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="mkdir /srv/images: file exists")

        transport = httpx.MockTransport(handler)
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        storage.mkdir("images")


class TestUpload:
    def test_success_201(self) -> None:
        transport = _mock_transport({"PUT": 201})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        storage.upload("pdf/abc.pdf", b"%PDF-fake")

    def test_failure_500_raises(self) -> None:
        transport = _mock_transport({"PUT": 500})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(httpx.HTTPStatusError):
            storage.upload("pdf/abc.pdf", b"%PDF-fake")

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"PUT": 401})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(StorageAuthError):
            storage.upload("pdf/abc.pdf", b"%PDF-fake")


def test_list_download_and_delete_support_reference_gc() -> None:
    xml = b"""
    <d:multistatus xmlns:d='DAV:'>
      <d:response><d:href>/static/pdf/abc.pdf</d:href>
        <d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat>
      </d:response>
      <d:response><d:href>/static/summary/</d:href>
        <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
      </d:response>
    </d:multistatus>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, content=xml)
        if request.method == "GET":
            return httpx.Response(200, content=b"payload")
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(405)

    storage = WebDavStorage(
        "https://cdn.example.com/static",
        "user",
        "pass",
        _transport=httpx.MockTransport(handler),
    )
    assert storage.list() == ("pdf/abc.pdf", "summary/")
    assert storage.download("pdf/abc.pdf") == b"payload"
    storage.delete("pdf/abc.pdf")


class TestContextManager:
    def test_with_statement(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        with WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        ) as storage:
            assert storage.exists("test.txt") is True


class TestUrlConstruction:
    def test_trailing_slash_stripped(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        storage = WebDavStorage(
            "https://cdn.example.com/static/", "user", "pass", _transport=transport
        )
        storage.exists("pdf/abc.pdf")
        assert str(captured[0].url) == "https://cdn.example.com/static/pdf/abc.pdf"

    def test_path_segments_are_url_encoded(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        storage.exists("pdf/a b.pdf")
        assert str(captured[0].url) == "https://cdn.example.com/static/pdf/a%20b.pdf"

    def test_parent_path_traversal_is_rejected(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        storage = WebDavStorage(
            "https://cdn.example.com/static", "user", "pass", _transport=transport
        )
        with pytest.raises(ValueError):
            storage.exists("../secret.txt")

    def test_non_local_http_basic_auth_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            WebDavStorage("http://cdn.example.com/static", "user", "pass")

    def test_localhost_http_basic_auth_is_allowed_for_tests(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        storage = WebDavStorage("http://localhost/static", "user", "pass", _transport=transport)
        assert storage.exists("pdf/abc.pdf") is True
