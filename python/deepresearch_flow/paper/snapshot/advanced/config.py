"""Advanced search runtime context bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepresearch_flow.paper.snapshot.advanced.web_oauth import SearchWebOAuthConfig


@dataclass(frozen=True)
class AdvancedSearchContext:
    """Immutable bundle of runtime handles the advanced endpoint needs."""

    embed_db_path: Path
    lance_db: Any
    paper_config: Any
    embedding_route_pool: Any
    rerank_route_pool: Any | None
    search_access_token: str | None
    search_config: Any
    auth_mode: str = "static"
    web_oauth: SearchWebOAuthConfig | None = None
