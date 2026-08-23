"""Public immutable models shared by publication pipeline components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class PublicationError(RuntimeError):
    """Publication failed before a formally published result was established."""


class PublicationConflict(PublicationError):
    """A job or paper already has a different durable publication identity."""


class PublicationCancelled(PublicationError):
    """Cancellation won before the Snapshot receipt became durable."""


@runtime_checkable
class FormalStore(Protocol):
    """Minimal write-only interface shared by local and WebDAV stores."""

    def put(self, relative_path: str, data: bytes) -> None:
        """Write one relative formal object idempotently."""


@dataclass(frozen=True)
class PublicationResource:
    """One immutable content-addressed formal object."""

    relative_path: str
    content: bytes
    digest: str
    size: int
    media_type: str

    @property
    def path(self) -> str:
        """Compatibility alias used by callers that call paths ``path``."""
        return self.relative_path


@dataclass(frozen=True)
class PublicationBundle:
    """Immutable publication input and deterministic bundle identity."""

    job_id: str
    paper_id: str
    paper: Mapping[str, Any]
    bibtex: Mapping[str, Any]
    resource_map: Mapping[str, PublicationResource]
    references: Mapping[str, str]
    bundle_digest: str
    work_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper", _freeze(self.paper))
        object.__setattr__(self, "bibtex", _freeze(self.bibtex))
        object.__setattr__(self, "resource_map", MappingProxyType(dict(self.resource_map)))
        object.__setattr__(self, "references", MappingProxyType(dict(self.references)))

    @property
    def resources(self) -> tuple[PublicationResource, ...]:
        """Return resources in deterministic path order."""
        return tuple(self.resource_map[key] for key in sorted(self.resource_map))


@dataclass(frozen=True)
class PublicationResult:
    """Result of Snapshot receipt commit and optional indexing."""

    paper_id: str
    bundle_digest: str
    already_published: bool = False
    indexed: bool = False
    index_warning: str | None = None


@dataclass(frozen=True)
class PublicationWorkerResult:
    """Public result for one publication-worker attempt."""

    job_id: str
    status: str
    publication: PublicationResult | None = None
    error: str | None = None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def plain(value: Any) -> Any:
    """Convert immutable model values to JSON/SQLite-friendly containers."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


__all__ = [
    "FormalStore",
    "PublicationBundle",
    "PublicationCancelled",
    "PublicationConflict",
    "PublicationError",
    "PublicationResource",
    "PublicationResult",
    "PublicationWorkerResult",
    "plain",
]
