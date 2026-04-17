from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext


def test_context_is_frozen_dataclass(tmp_path: Path) -> None:
    ctx = AdvancedSearchContext(
        embed_db_path=tmp_path,
        lance_db=object(),
        paper_config=object(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token="abc",
        search_config=object(),
    )
    try:
        ctx.search_access_token = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("AdvancedSearchContext should be frozen")
