from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.auth import BearerAuthError, bearer_auth_app, verify_bearer


def _echo_app() -> Starlette:
    async def echo(request):
        return PlainTextResponse(f"{request.method} {request.url.path}")

    return Starlette(routes=[Route("/", echo, methods=["GET", "POST", "PUT", "OPTIONS"])])


def test_verify_bearer_accepts_matching_token() -> None:
    assert verify_bearer("Bearer secret", "secret") is None


def test_verify_bearer_missing_header_rejects_missing() -> None:
    with pytest.raises(BearerAuthError) as exc:
        verify_bearer(None, "secret")
    assert exc.value.reason == "missing"


def test_verify_bearer_wrong_token_rejects_invalid() -> None:
    with pytest.raises(BearerAuthError) as exc:
        verify_bearer("Bearer wrong", "secret")
    assert exc.value.reason == "invalid"


def test_bearer_auth_app_passes_through_when_token_is_not_configured() -> None:
    client = TestClient(bearer_auth_app(_echo_app(), None))

    response = client.get("/")

    assert response.status_code == 200
    assert response.text == "GET /"


def test_bearer_auth_app_bypasses_options_and_enforces_other_methods() -> None:
    client = TestClient(bearer_auth_app(_echo_app(), "secret"))

    options_response = client.options("/")
    denied_response = client.put("/")
    allowed_response = client.put("/", headers={"Authorization": "Bearer secret"})

    assert options_response.status_code == 200
    assert options_response.text == "OPTIONS /"
    assert denied_response.status_code == 401
    assert denied_response.headers["www-authenticate"] == "Bearer"
    assert allowed_response.status_code == 200
    assert allowed_response.text == "PUT /"
