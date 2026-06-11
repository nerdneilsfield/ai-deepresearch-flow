"""Storage configuration types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StorageConfig:
    type: str  # "webdav", future: "s3", "r2"
    url: str
    username: str
    password: str
