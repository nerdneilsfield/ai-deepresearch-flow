"""Advanced search runtime context bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdvancedSearchContext:
    """Immutable bundle of runtime handles the advanced endpoint needs."""

    embed_db_path: Path
    lance_db: Any
    paper_config: Any
    embedding_route_pool: Any
    rerank_route_pool: Any | None
    search_access_token: str
    search_config: Any
