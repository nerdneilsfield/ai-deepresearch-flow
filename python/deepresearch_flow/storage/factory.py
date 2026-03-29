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
