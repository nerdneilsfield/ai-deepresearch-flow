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
        assert callable(getattr(storage, "close", None))
