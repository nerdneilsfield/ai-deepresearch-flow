# Remote Storage + Static File Push — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable remote storage abstraction (WebDAV first) and integrate static file push into the existing `paper db api push` CLI.

**Architecture:** New `storage/` package with Protocol-based `RemoteStorage` abstraction + `StorageAuthError`. WebDAV as first backend. `push_static_files()` in `push_static.py` depends only on the protocol. Config via `[remote.storage]` in `remote.toml`. Factory pattern matches OCR backends.

**Tech Stack:** Python 3.12+, httpx (sync, Basic Auth), click, tomllib, rich, typing.Protocol

**Spec:** `docs/superpowers/specs/2026-03-29-webdav-static-push-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/storage/__init__.py` | Create | Package init |
| `python/deepresearch_flow/storage/base.py` | Create | RemoteStorage protocol + StorageAuthError |
| `python/deepresearch_flow/storage/config.py` | Create | StorageConfig dataclass |
| `python/deepresearch_flow/storage/webdav.py` | Create | WebDAV implementation |
| `python/deepresearch_flow/storage/factory.py` | Create | type → instance dispatcher |
| `python/deepresearch_flow/storage/tests/__init__.py` | Create | Tests package |
| `python/deepresearch_flow/storage/tests/test_webdav.py` | Create | WebDAV tests with mocked transport |
| `python/deepresearch_flow/storage/tests/test_factory.py` | Create | Factory tests |
| `python/deepresearch_flow/paper/snapshot/push.py` | Modify | Add StorageConfig to RemoteConfig + load_remote_config() |
| `python/deepresearch_flow/paper/snapshot/push_static.py` | Create | push_static_files() using RemoteStorage protocol |
| `python/deepresearch_flow/paper/snapshot/tests/test_push.py` | Modify | Add storage config loading tests |
| `python/deepresearch_flow/paper/snapshot/tests/test_push_static.py` | Create | push_static_files() tests with fake storage |
| `python/deepresearch_flow/paper/db.py` | Modify | Add --retry-failed, integrate static push into CLI |

---

## Task 1: RemoteStorage Protocol + WebDAV Implementation

**Files:**
- Create: `python/deepresearch_flow/storage/__init__.py`
- Create: `python/deepresearch_flow/storage/base.py`
- Create: `python/deepresearch_flow/storage/config.py`
- Create: `python/deepresearch_flow/storage/webdav.py`
- Create: `python/deepresearch_flow/storage/tests/__init__.py`
- Create: `python/deepresearch_flow/storage/tests/test_webdav.py`

- [ ] **Step 1: Write failing tests for WebDAV storage**

Create `python/deepresearch_flow/storage/tests/test_webdav.py`:

```python
"""Tests for WebDAV remote storage implementation."""

from __future__ import annotations

import httpx
import pytest

from deepresearch_flow.storage.base import StorageAuthError
from deepresearch_flow.storage.webdav import WebDavStorage


def _mock_transport(responses: dict[str, int]) -> httpx.MockTransport:
    """Build transport that returns status codes by method."""

    def handler(request: httpx.Request) -> httpx.Response:
        status = responses.get(request.method, 405)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


class TestExists:
    def test_true_on_200(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        assert storage.exists("pdf/abc.pdf") is True

    def test_false_on_404(self) -> None:
        transport = _mock_transport({"HEAD": 404})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        assert storage.exists("pdf/abc.pdf") is False

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"HEAD": 401})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(StorageAuthError):
            storage.exists("pdf/abc.pdf")

    def test_server_error_raises(self) -> None:
        transport = _mock_transport({"HEAD": 500})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(httpx.HTTPStatusError):
            storage.exists("pdf/abc.pdf")

    def test_forbidden_raises(self) -> None:
        transport = _mock_transport({"HEAD": 403})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(httpx.HTTPStatusError):
            storage.exists("pdf/abc.pdf")


class TestMkdir:
    def test_success_201(self) -> None:
        transport = _mock_transport({"MKCOL": 201})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        storage.mkdir("pdf")  # Should not raise.

    def test_already_exists_405(self) -> None:
        transport = _mock_transport({"MKCOL": 405})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        storage.mkdir("pdf")  # Should not raise.

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"MKCOL": 401})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(StorageAuthError):
            storage.mkdir("pdf")

    def test_server_error_raises(self) -> None:
        transport = _mock_transport({"MKCOL": 500})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(httpx.HTTPStatusError):
            storage.mkdir("pdf")


class TestUpload:
    def test_success_201(self) -> None:
        transport = _mock_transport({"PUT": 201})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        storage.upload("pdf/abc.pdf", b"%PDF-fake")

    def test_failure_500_raises(self) -> None:
        transport = _mock_transport({"PUT": 500})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(httpx.HTTPStatusError):
            storage.upload("pdf/abc.pdf", b"%PDF-fake")

    def test_auth_failure_raises(self) -> None:
        transport = _mock_transport({"PUT": 401})
        storage = WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport)
        with pytest.raises(StorageAuthError):
            storage.upload("pdf/abc.pdf", b"%PDF-fake")


class TestContextManager:
    def test_with_statement(self) -> None:
        transport = _mock_transport({"HEAD": 200})
        with WebDavStorage("https://cdn.example.com/static", "user", "pass", _transport=transport) as storage:
            assert storage.exists("test.txt") is True


class TestUrlConstruction:
    def test_trailing_slash_stripped(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        storage = WebDavStorage("https://cdn.example.com/static/", "user", "pass", _transport=transport)
        storage.exists("pdf/abc.pdf")
        assert str(captured[0].url) == "https://cdn.example.com/static/pdf/abc.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/storage/tests/test_webdav.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create package structure and implement**

`python/deepresearch_flow/storage/__init__.py`:
```python
"""Pluggable remote storage backends."""
```

`python/deepresearch_flow/storage/tests/__init__.py`:
```python
"""Storage module tests."""
```

`python/deepresearch_flow/storage/base.py`:
```python
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
```

`python/deepresearch_flow/storage/config.py`:
```python
"""Storage configuration types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StorageConfig:
    type: str       # "webdav", future: "s3", "r2"
    url: str
    username: str
    password: str
```

`python/deepresearch_flow/storage/webdav.py`:
```python
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
        """HEAD → 200=True, 404=False, 401=StorageAuthError, else raise."""
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
        """MKCOL → 201/405=OK, 401=StorageAuthError, else raise."""
        url = f"{self._base_url}/{remote_path}/"
        resp = self._client.request("MKCOL", url)
        self._check_auth(resp)
        if resp.status_code in (201, 405):
            return
        resp.raise_for_status()

    def upload(self, remote_path: str, data: bytes) -> None:
        """PUT → 200/201/204=OK, 401=StorageAuthError, else raise."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/storage/tests/test_webdav.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/storage/
git commit -m "feat(storage): add RemoteStorage protocol + WebDAV implementation"
```

---

## Task 2: Storage Factory

**Files:**
- Create: `python/deepresearch_flow/storage/factory.py`
- Create: `python/deepresearch_flow/storage/tests/test_factory.py`

- [ ] **Step 1: Write failing tests**

Create `python/deepresearch_flow/storage/tests/test_factory.py`:

```python
"""Tests for storage factory."""

from __future__ import annotations

import pytest

from deepresearch_flow.storage.config import StorageConfig
from deepresearch_flow.storage.factory import create_storage


class TestCreateStorage:
    def test_unknown_type_raises(self) -> None:
        cfg = StorageConfig(type="unknown", url="http://x", username="u", password="p")
        with pytest.raises(ValueError, match="Unknown storage type: unknown"):
            create_storage(cfg)

    def test_webdav_returns_storage(self) -> None:
        cfg = StorageConfig(
            type="webdav",
            url="https://cdn.example.com/static",
            username="deploy",
            password="secret",
        )
        storage = create_storage(cfg)
        assert callable(getattr(storage, "exists", None))
        assert callable(getattr(storage, "mkdir", None))
        assert callable(getattr(storage, "upload", None))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/storage/tests/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement factory**

`python/deepresearch_flow/storage/factory.py`:
```python
"""Factory for creating remote storage instances from config."""

from __future__ import annotations

from deepresearch_flow.storage.base import RemoteStorage
from deepresearch_flow.storage.config import StorageConfig


def create_storage(config: StorageConfig) -> RemoteStorage:
    """Create a remote storage instance based on the config type."""
    if config.type == "webdav":
        from deepresearch_flow.storage.webdav import WebDavStorage

        return WebDavStorage(
            url=config.url,
            username=config.username,
            password=config.password,
        )

    raise ValueError(f"Unknown storage type: {config.type}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/storage/tests/ -v`
Expected: All 17 tests PASS (15 webdav + 2 factory)

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/storage/factory.py python/deepresearch_flow/storage/tests/test_factory.py
git commit -m "feat(storage): add factory dispatcher for storage backends"
```

---

## Task 3: Extend RemoteConfig for Storage

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/push.py:20-74`
- Modify: `python/deepresearch_flow/paper/snapshot/tests/test_push.py`

- [ ] **Step 1: Write failing tests**

Add to `python/deepresearch_flow/paper/snapshot/tests/test_push.py`:

```python
from deepresearch_flow.storage.config import StorageConfig


class TestStorageConfigLoading:
    def test_storage_config_parsed(self, tmp_path: Path) -> None:
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n\n'
            '[remote.storage]\n'
            'type = "webdav"\n'
            'url = "https://cdn.example.com/static"\n'
            'username = "deploy"\n'
            'password = "secret"\n'
        )
        cfg = load_remote_config(f)
        assert cfg.storage is not None
        assert cfg.storage.type == "webdav"
        assert cfg.storage.url == "https://cdn.example.com/static"
        assert cfg.storage.username == "deploy"
        assert cfg.storage.password == "secret"

    def test_storage_config_optional(self, tmp_path: Path) -> None:
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n'
        )
        cfg = load_remote_config(f)
        assert cfg.storage is None

    def test_storage_env_password(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_STORAGE_PW", "env-resolved")
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n\n'
            '[remote.storage]\n'
            'type = "webdav"\n'
            'url = "https://cdn.example.com/static"\n'
            'username = "deploy"\n'
            'password = "env:TEST_STORAGE_PW"\n'
        )
        cfg = load_remote_config(f)
        assert cfg.storage is not None
        assert cfg.storage.password == "env-resolved"

    def test_storage_missing_type_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n\n'
            '[remote.storage]\n'
            'url = "https://cdn.example.com/static"\n'
            'username = "deploy"\n'
            'password = "secret"\n'
        )
        with pytest.raises(ValueError, match="type"):
            load_remote_config(f)

    def test_storage_missing_password_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n\n'
            '[remote.storage]\n'
            'type = "webdav"\n'
            'url = "https://cdn.example.com/static"\n'
            'username = "deploy"\n'
        )
        with pytest.raises(ValueError, match="password"):
            load_remote_config(f)

    def test_storage_env_missing_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_PW", raising=False)
        f = tmp_path / "remote.toml"
        f.write_text(
            '[remote]\n'
            'api_base_url = "https://api.example.com"\n'
            'admin_token = "my-token"\n\n'
            '[remote.storage]\n'
            'type = "webdav"\n'
            'url = "https://cdn.example.com/static"\n'
            'username = "deploy"\n'
            'password = "env:NONEXISTENT_PW"\n'
        )
        with pytest.raises(ValueError, match="NONEXISTENT_PW"):
            load_remote_config(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push.py::TestStorageConfigLoading -v`
Expected: FAIL

- [ ] **Step 3: Extend push.py**

In `python/deepresearch_flow/paper/snapshot/push.py`:

1. Add import:
```python
from deepresearch_flow.storage.config import StorageConfig
```

2. Extend `RemoteConfig`:
```python
@dataclass(frozen=True)
class RemoteConfig:
    api_base_url: str
    admin_token: str
    batch_size: int = DEFAULT_BATCH_SIZE
    storage: StorageConfig | None = None
```

3. Add `_resolve_env` helper before `load_remote_config`:
```python
def _resolve_env(value: str, field_name: str, config_path: Path) -> str:
    """Resolve env: prefix for a config value."""
    if not value.startswith("env:"):
        return value
    env_name = value.split(":", 1)[1]
    resolved = os.environ.get(env_name, "")
    if not resolved:
        raise ValueError(
            f"Environment variable '{env_name}' is not set "
            f"(referenced as 'env:{env_name}' for {field_name} in {config_path})"
        )
    return resolved
```

4. At the end of `load_remote_config()`, before `return`, add:
```python
    storage_raw = remote.get("storage")
    storage: StorageConfig | None = None
    if storage_raw:
        s_type = str(storage_raw.get("type") or "")
        if not s_type:
            raise ValueError(f"remote.storage.type is required in {config_path}")
        s_url = str(storage_raw.get("url") or "").rstrip("/")
        if not s_url:
            raise ValueError(f"remote.storage.url is required in {config_path}")
        s_user = str(storage_raw.get("username") or "")
        if not s_user:
            raise ValueError(f"remote.storage.username is required in {config_path}")
        s_pass = str(storage_raw.get("password") or "")
        if not s_pass:
            raise ValueError(f"remote.storage.password is required in {config_path}")
        s_pass = _resolve_env(s_pass, "remote.storage.password", config_path)
        storage = StorageConfig(type=s_type, url=s_url, username=s_user, password=s_pass)

    return RemoteConfig(
        api_base_url=api_base_url,
        admin_token=admin_token,
        batch_size=batch_size,
        storage=storage,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/push.py python/deepresearch_flow/paper/snapshot/tests/test_push.py
git commit -m "feat(push): add StorageConfig to remote config loading"
```

---

## Task 4: push_static_files() Function

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/push_static.py`
- Create: `python/deepresearch_flow/paper/snapshot/tests/test_push_static.py`

- [ ] **Step 1: Write failing tests with fake in-memory storage**

Create `python/deepresearch_flow/paper/snapshot/tests/test_push_static.py`:

```python
"""Tests for static file push — backend-agnostic using fake storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepresearch_flow.storage.base import StorageAuthError
from deepresearch_flow.paper.snapshot.push_static import (
    PushStaticStats,
    load_retry_files,
    push_static_files,
    write_error_report,
)


class FakeStorage:
    """In-memory RemoteStorage for testing."""

    def __init__(
        self,
        *,
        existing: set[str] | None = None,
        fail_uploads: bool = False,
        auth_fail: bool = False,
    ) -> None:
        self._existing = existing or set()
        self._fail_uploads = fail_uploads
        self._auth_fail = auth_fail
        self.uploaded: dict[str, bytes] = {}
        self.mkdirs: list[str] = []

    def exists(self, remote_path: str) -> bool:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        return remote_path in self._existing

    def mkdir(self, remote_path: str) -> None:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        self.mkdirs.append(remote_path)

    def upload(self, remote_path: str, data: bytes) -> None:
        if self._auth_fail:
            raise StorageAuthError("Authentication failed")
        if self._fail_uploads:
            raise RuntimeError("Upload failed: HTTP 500")
        self.uploaded[remote_path] = data


def _setup_static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "static_export"
    (root / "pdf").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "md").mkdir()
    (root / "summary" / "paper1").mkdir(parents=True)

    (root / "pdf" / "abc123.pdf").write_bytes(b"%PDF-fake")
    (root / "images" / "def456.png").write_bytes(b"\x89PNG-fake")
    (root / "md" / "ghi789.md").write_text("# Hello")
    (root / "summary" / "paper1" / "default.json").write_text('{"k":"v"}')
    return root


class TestPushStaticFiles:
    def test_upload_new_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert stats.uploaded == 4
        assert stats.skipped == 0
        assert stats.failed == 0
        assert len(storage.uploaded) == 4

    def test_skip_existing_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(existing={
            "pdf/abc123.pdf", "images/def456.png",
            "md/ghi789.md", "summary/paper1/default.json",
        })
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.skipped == 4
        assert stats.failed == 0

    def test_record_failed_files(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(fail_uploads=True)
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.failed == 4
        assert len(stats.failed_files) == 4

    def test_auth_failure_aborts(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage(auth_fail=True)
        with pytest.raises(StorageAuthError):
            push_static_files(root, storage)

    def test_only_files_filter(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage, only_files=["pdf/abc123.pdf"])

        assert stats.uploaded == 1
        assert len(storage.uploaded) == 1
        assert "pdf/abc123.pdf" in storage.uploaded

    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert stats.uploaded == 0
        assert stats.skipped == 0
        assert stats.failed == 0

    def test_per_directory_stats(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        stats = push_static_files(root, storage)

        assert "pdf/" in stats.per_directory
        assert stats.per_directory["pdf/"]["uploaded"] == 1
        assert "summary/" in stats.per_directory
        assert stats.per_directory["summary/"]["uploaded"] == 1

    def test_mkdir_called_for_parents(self, tmp_path: Path) -> None:
        root = _setup_static_dir(tmp_path)
        storage = FakeStorage()
        push_static_files(root, storage)

        assert "summary" in storage.mkdirs
        assert "summary/paper1" in storage.mkdirs


class TestErrorReport:
    def test_write_and_load(self, tmp_path: Path) -> None:
        report_path = tmp_path / "errors.json"
        failed = [
            {"path": "pdf/abc.pdf", "error": "timeout"},
            {"path": "images/def.png", "error": "500"},
        ]
        write_error_report(failed, report_path)
        loaded = load_retry_files(report_path)
        assert loaded == ["pdf/abc.pdf", "images/def.png"]

    def test_load_empty_report(self, tmp_path: Path) -> None:
        report_path = tmp_path / "errors.json"
        report_path.write_text("[]")
        loaded = load_retry_files(report_path)
        assert loaded == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push_static.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement push_static.py**

Create `python/deepresearch_flow/paper/snapshot/push_static.py`:

```python
"""Push static export files to a remote storage backend."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from deepresearch_flow.storage.base import RemoteStorage, StorageAuthError

logger = logging.getLogger(__name__)


@dataclass
class PushStaticStats:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    failed_files: list[dict[str, str]] = field(default_factory=list)
    per_directory: dict[str, dict[str, int]] = field(default_factory=dict)


def _discover_files(root: Path) -> list[str]:
    """Recursively discover all files under root, returning sorted relative paths."""
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
    )


def _top_dir(rel_path: str) -> str:
    """Extract the top-level directory (e.g. 'pdf/abc.pdf' -> 'pdf/')."""
    parts = rel_path.split("/", 1)
    return f"{parts[0]}/" if len(parts) > 1 else "(root)"


def _ensure_parents(storage: RemoteStorage, rel_path: str) -> None:
    """Call storage.mkdir() for each parent directory component."""
    parts = rel_path.split("/")[:-1]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        storage.mkdir(current)


def _record(stats: PushStaticStats, rel_path: str, kind: str, error: str = "") -> None:
    """Record a result for stats and per-directory breakdown."""
    top = _top_dir(rel_path)
    stats.per_directory.setdefault(top, {"uploaded": 0, "skipped": 0, "failed": 0})
    if kind == "uploaded":
        stats.uploaded += 1
        stats.per_directory[top]["uploaded"] += 1
    elif kind == "skipped":
        stats.skipped += 1
        stats.per_directory[top]["skipped"] += 1
    elif kind == "failed":
        stats.failed += 1
        stats.failed_files.append({"path": rel_path, "error": error})
        stats.per_directory[top]["failed"] += 1
        logger.warning("Failed to push %s: %s", rel_path, error)


def push_static_files(
    static_export_dir: Path,
    storage: RemoteStorage,
    *,
    only_files: list[str] | None = None,
) -> PushStaticStats:
    """Push static files to a remote storage backend.

    StorageAuthError propagates immediately (abort all).
    Other exceptions are caught per-file and recorded as failures.
    """
    stats = PushStaticStats()

    all_files = only_files if only_files is not None else _discover_files(static_export_dir)
    if not all_files:
        return stats

    for rel_path in all_files:
        # Check existence — StorageAuthError propagates immediately.
        if storage.exists(rel_path):
            _record(stats, rel_path, "skipped")
            continue

        # Ensure parent directories.
        try:
            _ensure_parents(storage, rel_path)
        except StorageAuthError:
            raise
        except Exception as exc:
            _record(stats, rel_path, "failed", str(exc))
            continue

        # Upload file bytes.
        file_path = static_export_dir / rel_path
        try:
            data = file_path.read_bytes()
            storage.upload(rel_path, data)
            _record(stats, rel_path, "uploaded")
        except StorageAuthError:
            raise
        except Exception as exc:
            _record(stats, rel_path, "failed", str(exc))

    return stats


# ---------------------------------------------------------------------------
# Error report I/O
# ---------------------------------------------------------------------------

def write_error_report(failed_files: list[dict[str, str]], path: Path) -> None:
    """Write failed file list to JSON."""
    path.write_text(json.dumps(failed_files, indent=2, ensure_ascii=False), encoding="utf-8")


def load_retry_files(path: Path) -> list[str]:
    """Load relative file paths from a push-static-errors.json report."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [entry["path"] for entry in data]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_push_static.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/push_static.py python/deepresearch_flow/paper/snapshot/tests/test_push_static.py
git commit -m "feat(push): add push_static_files() with RemoteStorage protocol"
```

---

## Task 5: CLI Integration

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py:933-1047`

- [ ] **Step 1: Add `--retry-failed` option and static push logic**

In `python/deepresearch_flow/paper/db.py`, modify the `api_push` command:

1. Add click option after `--dry-run`:
```python
    @click.option(
        "--retry-failed",
        "retry_failed_path",
        default=None,
        type=click.Path(exists=True),
        help="Path to push-static-errors.json to retry only failed static files",
    )
```

2. Add `retry_failed_path: str | None` to function signature.

3. Update `--static-export-dir` help:
```python
        help="Path to local static export dir (for summary JSONs + remote storage push)",
```

4. Add validation in two locations:

   Before config loading (near top of function body):
```python
        if retry_failed_path and not static_export_dir:
            raise click.ClickException("--retry-failed requires --static-export-dir")
```

   After `config = load_remote_config(config_file)`:
```python
        if retry_failed_path and not config.storage:
            raise click.ClickException("--retry-failed requires [remote.storage] in config")
```

5. Wrap existing metadata push block with `if not retry_failed_path:`.

6. After metadata push (or skip), add static push using context manager:
```python
        # --- Static file push via remote storage ---
        if static_dir and config.storage:
            from deepresearch_flow.paper.snapshot.push_static import (
                load_retry_files,
                push_static_files,
                write_error_report,
            )
            from deepresearch_flow.storage.base import StorageAuthError
            from deepresearch_flow.storage.factory import create_storage

            if retry_failed_path:
                only_files = load_retry_files(Path(retry_failed_path))
                console.print(f"[cyan]Retrying {len(only_files)} failed static files...[/cyan]")
            else:
                only_files = None
                console.print("[cyan]Pushing static files...[/cyan]")

            console.print(f"[cyan]Storage:[/cyan] {config.storage.type} → {config.storage.url}")

            try:
                with create_storage(config.storage) as storage:
                    static_stats = push_static_files(
                        static_dir, storage, only_files=only_files,
                    )
            except StorageAuthError as exc:
                raise click.ClickException(str(exc)) from exc

            static_table = Table(title="Static Files Push")
            static_table.add_column("Directory")
            static_table.add_column("Uploaded", justify="right")
            static_table.add_column("Skipped", justify="right")
            static_table.add_column("Failed", justify="right")

            for dirname in sorted(static_stats.per_directory):
                d = static_stats.per_directory[dirname]
                static_table.add_row(
                    dirname,
                    f"[green]{d['uploaded']}[/green]",
                    f"[dim]{d['skipped']}[/dim]",
                    f"[red]{d['failed']}[/red]" if d["failed"] else "0",
                )
            static_table.add_section()
            static_table.add_row(
                "[bold]Total[/bold]",
                f"[bold green]{static_stats.uploaded}[/bold green]",
                f"[bold dim]{static_stats.skipped}[/bold dim]",
                f"[bold red]{static_stats.failed}[/bold red]" if static_stats.failed else "[bold]0[/bold]",
            )
            console.print(static_table)

            if static_stats.failed_files:
                error_path = Path("push-static-errors.json")
                write_error_report(static_stats.failed_files, error_path)
                console.print(f"\n[red]Failed files:[/red]")
                for entry in static_stats.failed_files[:10]:
                    console.print(f"  {entry['path']}: {entry['error']}")
                if len(static_stats.failed_files) > 10:
                    console.print(f"  ... and {len(static_stats.failed_files) - 10} more")
                console.print(f"\nFull error list saved to [bold]{error_path}[/bold]")
                console.print("Retry with: --retry-failed push-static-errors.json")
```

- [ ] **Step 2: Run the full test suite**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/snapshot/tests/ python/deepresearch_flow/storage/tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Verify CLI help**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run deepresearch-flow paper db api push --help`
Expected: Shows `--retry-failed` option

- [ ] **Step 4: Commit**

```bash
git add python/deepresearch_flow/paper/db.py
git commit -m "feat(push): integrate remote storage push into api push CLI"
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run complete project test suite**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/storage/tests/ python/deepresearch_flow/paper/snapshot/tests/ python/deepresearch_flow/ocr/tests/ -q`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Commit docs**

```bash
git add docs/superpowers/specs/2026-03-29-webdav-static-push-design.md docs/superpowers/plans/2026-03-29-webdav-static-push.md
git commit -m "docs: add remote storage + static push spec and plan"
```
