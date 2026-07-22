"""Advanced search endpoint on snapshot API (token-gated hybrid retrieval)."""

from starlette.routing import Route

from deepresearch_flow.paper.snapshot.advanced.config import AdvancedSearchContext
from deepresearch_flow.paper.snapshot.advanced.handler import (
    _api_search_advanced,
    _api_verify_token,
)
from deepresearch_flow.paper.snapshot.advanced.web_oauth import (
    SearchWebOAuthConfig,
    create_web_oauth_routes,
)

__all__ = [
    "AdvancedSearchContext",
    "SearchWebOAuthConfig",
    "create_advanced_routes",
    "create_web_oauth_routes",
]


def create_advanced_routes(_ctx: AdvancedSearchContext) -> list[Route]:
    return [
        Route("/api/v1/search/advanced", _api_search_advanced, methods=["GET"]),
        Route("/api/v1/search/advanced/verify-token", _api_verify_token, methods=["POST"]),
    ]
