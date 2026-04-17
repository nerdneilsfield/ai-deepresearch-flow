"""Advanced search endpoint on snapshot API (token-gated hybrid retrieval)."""

from starlette.routing import Route

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext
from deepresearch_flow.paper.snapshot.advanced.handler import (
    _api_search_advanced,
    _api_verify_token,
)

__all__ = ["AdvancedSearchContext", "create_advanced_routes"]


def create_advanced_routes(_ctx: AdvancedSearchContext) -> list[Route]:
    return [
        Route("/api/v1/search/advanced", _api_search_advanced, methods=["GET"]),
        Route("/api/v1/search/advanced/verify-token", _api_verify_token, methods=["POST"]),
    ]
