from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.advanced import (
    AdvancedSearchContext,
    SearchWebOAuthConfig,
    create_advanced_routes,
    create_web_oauth_routes,
)
from deepresearch_flow.paper.snapshot.api import create_app


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class _SearchConfig:
    advanced_rrf_k = 60
    advanced_dense_top_k = 50
    advanced_sparse_top_k = 30
    advanced_post_fusion_top_k = 50
    advanced_dedup_cosine_threshold = 0.95
    advanced_rerank_top_n = 20
    advanced_mmr_lambda_default = 0.6
    advanced_rerank_timeout_ms = 1500
    advanced_top_n_max = 50
    advanced_max_query_length = 500


def _build_app(*, allowed_ids: tuple[str, ...] = ("42",)) -> Starlette:
    oauth = SearchWebOAuthConfig(
        public_base_url="http://localhost",
        client_id="github-client",
        client_secret="github-secret",
        allowed_github_user_ids=allowed_ids,
    )
    ctx = AdvancedSearchContext(
        embed_db_path=Path("unused"),
        lance_db=object(),
        paper_config=object(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token="static-secret",
        search_config=_SearchConfig(),
        auth_mode="both",
        web_oauth=oauth,
    )
    app = Starlette(routes=[*create_advanced_routes(ctx), *create_web_oauth_routes()])
    app.state.advanced = ctx
    app.state.cfg = SimpleNamespace(snapshot_db=Path("unused"))
    return app


def _advanced_context(*, token: str | None = "static-secret") -> AdvancedSearchContext:
    return AdvancedSearchContext(
        embed_db_path=Path("unused"),
        lance_db=object(),
        paper_config=object(),
        embedding_route_pool=object(),
        rerank_route_pool=None,
        search_access_token=token,
        search_config=_SearchConfig(),
    )


def _github_transport(*, user_id: int = 42, login: str = "octocat") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "github-token"}, request=request)
        if request.url == "https://api.github.com/user":
            return httpx.Response(
                200,
                json={"id": user_id, "login": login},
                request=request,
            )
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def _complete_login(client: TestClient, monkeypatch, *, user_id: int = 42) -> httpx.Response:
    from deepresearch_flow.paper.snapshot.advanced import web_oauth

    transport = _github_transport(user_id=user_id)
    monkeypatch.setattr(
        web_oauth.httpx,
        "AsyncClient",
        lambda **_kwargs: _REAL_ASYNC_CLIENT(transport=transport),
    )
    login = client.get(
        "/api/v1/auth/github/login?return_to=%2F%3Fq%3Dvision",
        follow_redirects=False,
    )
    assert login.status_code == 302
    authorize_url = urlparse(login.headers["location"])
    authorize_query = parse_qs(authorize_url.query)
    callback = client.get(
        "/auth/callback/web",
        params={"code": "github-code", "state": authorize_query["state"][0]},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    return callback


def test_login_redirects_to_github_and_rejects_external_return_url() -> None:
    client = TestClient(_build_app())
    response = client.get(
        "/api/v1/auth/github/login?return_to=https%3A%2F%2Fevil.example",
        follow_redirects=False,
    )
    assert response.status_code == 302
    parsed = urlparse(response.headers["location"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "github.com"
    assert query["client_id"] == ["github-client"]
    assert query["redirect_uri"] == ["http://localhost/auth/callback/web"]
    assert query["code_challenge_method"] == ["S256"]


@pytest.mark.parametrize(
    ("mode", "token", "expected_methods"),
    [
        ("static", "static-secret", ["bearer"]),
        ("github-oauth", None, ["github-oauth"]),
        ("both", "static-secret", ["github-oauth", "bearer"]),
    ],
)
def test_runtime_config_advertises_each_search_auth_mode(
    tmp_path: Path,
    mode: str,
    token: str | None,
    expected_methods: list[str],
) -> None:
    app = create_app(
        snapshot_db=tmp_path / "snapshot.db",
        static_base_url="",
        mcp_access_token="mcp-private-token",
        search_auth_mode=mode,
        mcp_public_base_url="http://localhost",
        github_oauth_client_id="github-client",
        github_oauth_client_secret="github-secret",
        mcp_github_allowed_user_ids=["42"],
        advanced_config=_advanced_context(token=token),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/config")
    assert response.status_code == 200
    assert response.json()["advanced_search"] == {
        "enabled": True,
        "auth_methods": expected_methods,
        "github_login_url": (
            "/api/v1/auth/github/login" if "github-oauth" in expected_methods else None
        ),
    }


def test_callback_creates_seven_day_session_and_logout_clears_it(monkeypatch) -> None:
    client = TestClient(_build_app())
    callback = _complete_login(client, monkeypatch)
    session_cookie = callback.headers["set-cookie"]
    assert "Max-Age=604800" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": True,
        "user": {"id": "42", "login": "octocat"},
    }

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_callback_rejects_tampered_state() -> None:
    client = TestClient(_build_app())
    client.get("/api/v1/auth/github/login", follow_redirects=False)
    callback = client.get(
        "/auth/callback/web?code=github-code&state=tampered",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/?auth_error=invalid_state"
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_callback_reports_denied_authorization() -> None:
    client = TestClient(_build_app())
    login = client.get(
        "/api/v1/auth/github/login?return_to=%2F%3Fq%3Dvision",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(
        "/auth/callback/web",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/?q=vision&auth_error=denied"
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_callback_reports_github_upstream_failure(monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import web_oauth

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    transport = httpx.MockTransport(unavailable)
    monkeypatch.setattr(
        web_oauth.httpx,
        "AsyncClient",
        lambda **_kwargs: _REAL_ASYNC_CLIENT(transport=transport),
    )
    client = TestClient(_build_app())
    login = client.get("/api/v1/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(
        "/auth/callback/web",
        params={"code": "github-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/?auth_error=upstream"
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_callback_rejects_user_outside_allowlist(monkeypatch) -> None:
    client = TestClient(_build_app())
    _complete_login(client, monkeypatch, user_id=99)
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_advanced_search_accepts_session_or_static_bearer(monkeypatch) -> None:
    from deepresearch_flow.paper.snapshot.advanced import handler

    class _Connection:
        def close(self) -> None:
            pass

    async def successful_search(**_kwargs):
        return {"success": True, "results": []}

    monkeypatch.setattr(handler, "_open_ro_conn", lambda _path: _Connection())
    monkeypatch.setattr(handler, "run_advanced_search", successful_search)
    client = TestClient(_build_app())

    bearer = client.get(
        "/api/v1/search/advanced?q=vision",
        headers={"Authorization": "Bearer static-secret"},
    )
    assert bearer.status_code == 200

    _complete_login(client, monkeypatch)
    session = client.get("/api/v1/search/advanced?q=vision")
    assert session.status_code == 200

    client.post("/api/v1/auth/logout")
    unauthenticated = client.get("/api/v1/search/advanced?q=vision")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHORIZED"
