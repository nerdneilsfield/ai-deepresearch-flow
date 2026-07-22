"""GitHub OAuth browser sessions for advanced search."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route


_SESSION_COOKIE = "drflow_search_session"
_STATE_COOKIE = "drflow_search_oauth_state"
_SESSION_MAX_AGE = 7 * 24 * 60 * 60
_STATE_MAX_AGE = 10 * 60


@dataclass(frozen=True)
class SearchWebOAuthConfig:
    """Configuration for GitHub-backed browser sessions."""

    public_base_url: str
    client_id: str
    client_secret: str
    allowed_github_user_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = self.public_base_url.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP_PUBLIC_BASE_URL is required for search GitHub OAuth")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError(
                "MCP_PUBLIC_BASE_URL must be an origin without path, query, or fragment"
            )
        if parsed.scheme != "https" and (parsed.hostname or "").lower() not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("MCP_PUBLIC_BASE_URL must use https outside localhost")
        if not self.client_id:
            raise ValueError("GITHUB_OAUTH_CLIENT_ID is required for search GitHub OAuth")
        if not self.client_secret:
            raise ValueError("GITHUB_OAUTH_CLIENT_SECRET is required for search GitHub OAuth")
        allowed_ids = tuple(str(value).strip() for value in self.allowed_github_user_ids)
        if not allowed_ids:
            raise ValueError("MCP_GITHUB_ALLOWED_USER_IDS is required for search GitHub OAuth")
        if any(not value.isdigit() for value in allowed_ids):
            raise ValueError("MCP_GITHUB_ALLOWED_USER_IDS must contain numeric GitHub user IDs")
        object.__setattr__(self, "public_base_url", normalized)
        object.__setattr__(self, "allowed_github_user_ids", allowed_ids)

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url}/auth/callback/web"

    @property
    def secure_cookies(self) -> bool:
        return urlparse(self.public_base_url).scheme == "https"

    @property
    def signing_key(self) -> bytes:
        return hmac.new(
            self.client_secret.encode(), b"deepresearch-flow/search-web-session/v1", hashlib.sha256
        ).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: dict[str, Any], key: bytes) -> str:
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64encode(hmac.new(key, encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def _unsign(value: str | None, key: bytes) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = _b64encode(hmac.new(key, encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_return_to(value: str | None) -> str:
    candidate = str(value or "/").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return "/"
    return candidate


def _with_auth_error(return_to: str, code: str) -> str:
    parsed = urlparse(_safe_return_to(return_to))
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "auth_error"]
    query.append(("auth_error", code))
    return urlunparse(("", "", parsed.path or "/", "", urlencode(query), ""))


def _advanced_context(request: Request):
    return getattr(request.app.state, "advanced", None)


def _oauth_config(request: Request) -> SearchWebOAuthConfig | None:
    ctx = _advanced_context(request)
    return getattr(ctx, "web_oauth", None) if ctx is not None else None


def session_user(request: Request, config: SearchWebOAuthConfig) -> dict[str, str] | None:
    payload = _unsign(request.cookies.get(_SESSION_COOKIE), config.signing_key)
    if payload is None:
        return None
    try:
        expires_at = int(payload["exp"])
        user_id = str(payload["id"])
        login = str(payload["login"])
    except (KeyError, TypeError, ValueError):
        return None
    if expires_at <= int(time.time()) or user_id not in config.allowed_github_user_ids or not login:
        return None
    return {"id": user_id, "login": login}


async def _api_github_login(request: Request) -> Response:
    config = _oauth_config(request)
    if config is None:
        return JSONResponse({"error": "github_oauth_disabled"}, status_code=404)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64encode(hashlib.sha256(verifier.encode()).digest())
    state_cookie = _sign(
        {
            "iat": int(time.time()),
            "nonce": nonce,
            "return_to": _safe_return_to(request.query_params.get("return_to")),
            "verifier": verifier,
        },
        config.signing_key,
    )
    location = "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.callback_url,
            "state": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(
        _STATE_COOKIE,
        state_cookie,
        max_age=_STATE_MAX_AGE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/auth/callback/web",
    )
    return response


def _callback_redirect(config: SearchWebOAuthConfig, target: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(
        _STATE_COOKIE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/auth/callback/web",
    )
    return response


async def _api_github_callback(request: Request) -> Response:
    config = _oauth_config(request)
    if config is None:
        return JSONResponse({"error": "github_oauth_disabled"}, status_code=404)
    state_payload = _unsign(request.cookies.get(_STATE_COOKIE), config.signing_key)
    if state_payload is None:
        return _callback_redirect(config, "/?auth_error=invalid_state")
    return_to = _safe_return_to(str(state_payload.get("return_to", "/")))
    if request.query_params.get("error"):
        return _callback_redirect(config, _with_auth_error(return_to, "denied"))
    try:
        issued_at = int(state_payload["iat"])
        nonce = str(state_payload["nonce"])
        verifier = str(state_payload["verifier"])
    except (KeyError, TypeError, ValueError):
        return _callback_redirect(config, _with_auth_error(return_to, "invalid_state"))
    if (
        int(time.time()) - issued_at > _STATE_MAX_AGE
        or issued_at > int(time.time()) + 30
        or not hmac.compare_digest(request.query_params.get("state", ""), nonce)
    ):
        return _callback_redirect(config, _with_auth_error(return_to, "invalid_state"))
    code = request.query_params.get("code", "")
    if not code:
        return _callback_redirect(config, _with_auth_error(return_to, "invalid_state"))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                    "redirect_uri": config.callback_url,
                    "code_verifier": verifier,
                },
            )
            token_response.raise_for_status()
            token = str(token_response.json().get("access_token", ""))
            if not token:
                raise ValueError("GitHub did not return an access token")
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            user_response.raise_for_status()
            user_data = user_response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return _callback_redirect(config, _with_auth_error(return_to, "upstream"))

    user_id = str(user_data.get("id", ""))
    login = str(user_data.get("login", ""))
    if user_id not in config.allowed_github_user_ids:
        return _callback_redirect(config, _with_auth_error(return_to, "not_allowed"))
    if not login:
        return _callback_redirect(config, _with_auth_error(return_to, "upstream"))

    response = _callback_redirect(config, return_to)
    response.set_cookie(
        _SESSION_COOKIE,
        _sign(
            {"exp": int(time.time()) + _SESSION_MAX_AGE, "id": user_id, "login": login},
            config.signing_key,
        ),
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )
    return response


async def _api_session(request: Request) -> Response:
    config = _oauth_config(request)
    if config is None:
        return JSONResponse({"authenticated": False})
    user = session_user(request, config)
    if user is None:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "user": user})


async def _api_logout(request: Request) -> Response:
    config = _oauth_config(request)
    response = Response(status_code=204)
    response.delete_cookie(
        _SESSION_COOKIE,
        httponly=True,
        secure=config.secure_cookies if config is not None else False,
        samesite="lax",
        path="/",
    )
    return response


def create_web_oauth_routes() -> list[Route]:
    return [
        Route("/api/v1/auth/github/login", _api_github_login, methods=["GET"]),
        Route("/auth/callback/web", _api_github_callback, methods=["GET"]),
        Route("/api/v1/auth/session", _api_session, methods=["GET"]),
        Route("/api/v1/auth/logout", _api_logout, methods=["POST"]),
    ]
