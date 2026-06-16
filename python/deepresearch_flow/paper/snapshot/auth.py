"""Shared bearer-token auth helpers for snapshot HTTP surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import inspect
import json
import hmac
import logging
import os
from pathlib import Path
import tempfile
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from typing import SupportsFloat
from urllib.parse import urlparse

try:
    from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier
    from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
    from fastmcp.server.auth.providers.github import GitHubProvider
    from pydantic import AnyUrl
except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - version guard
    try:
        from importlib.metadata import version

        fastmcp_version = version("fastmcp")
    except Exception:
        fastmcp_version = "unknown"
    raise ImportError(
        "deepresearch-flow MCP auth requires FastMCP with server auth support; "
        f"installed fastmcp={fastmcp_version}. Rebuild the Docker base image with "
        "requirements.txt / pyproject.toml dependencies in sync."
    ) from exc
from starlette.responses import JSONResponse

try:  # pragma: no cover - Windows fallback
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_BEARER_SCHEME = "bearer"
_MCP_PUBLIC_UNSAFE_ENV = "MCP_PUBLIC_UNSAFE"
_PLACEHOLDER_STATIC_TOKENS = {"your-mcp-token"}
_OAUTH_CLIENT_COLLECTION = "mcp-oauth-proxy-clients"
_LOGGER = logging.getLogger(__name__)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync after atomic replace."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def is_mcp_public_unsafe_allowed() -> bool:
    """Return whether MCP may intentionally run without static bearer auth."""
    return os.environ.get(_MCP_PUBLIC_UNSAFE_ENV) == "1"


def validate_mcp_static_access_token(access_token: str | None, *, context: str = "MCP") -> None:
    """Fail closed for exposed MCP static bearer protection unless explicitly unsafe."""
    if is_mcp_public_unsafe_allowed():
        return
    token = (access_token or "").strip()
    if not token or token in _PLACEHOLDER_STATIC_TOKENS:
        raise ValueError(
            f"MCP_ACCESS_TOKEN is required for {context}; "
            "set MCP_PUBLIC_UNSAFE=1 only for isolated local testing"
        )


class BearerAuthError(Exception):
    """Bearer auth failure with a machine-readable reason."""

    def __init__(self, reason: Literal["missing", "invalid"]) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_bearer(header_value: str | None, expected: str) -> None:
    """Validate ``Authorization: Bearer <token>`` using constant-time compare."""
    if not header_value:
        raise BearerAuthError("missing")
    scheme, sep, candidate = header_value.partition(" ")
    if sep != " " or scheme.lower() != _BEARER_SCHEME:
        raise BearerAuthError("missing")
    if not hmac.compare_digest(candidate, expected):
        raise BearerAuthError("invalid")


def _safe_client_log_id(client_id: str) -> str:
    if len(client_id) <= 12:
        return "<set>"
    return f"{client_id[:8]}…{client_id[-4:]}"


def _is_recoverable_dynamic_client_id(client_id: str) -> bool:
    if not client_id or len(client_id) > 128:
        return False
    return all(ch.isalnum() or ch in {"-", "_", ".", "~", ":"} for ch in client_id)


def bearer_auth_app(app, access_token: str | None):
    """Wrap an ASGI app with optional bearer-token protection.

    Passing ``None`` or ``""`` leaves the app public.
    """
    if access_token in (None, ""):
        return app

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        try:
            verify_bearer(headers.get("authorization"), access_token)
        except BearerAuthError:
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return

        await app(scope, receive, send)

    # Preserve common ASGI app metadata for route inspection and transport helpers.
    wrapped.routes = getattr(app, "routes", None)
    wrapped.router = getattr(app, "router", None)
    return wrapped


@dataclass(frozen=True)
class McpGitHubOAuthConfig:
    """Configuration for GitHub-backed MCP OAuth."""

    public_base_url: str
    client_id: str
    client_secret: str
    allowed_github_user_ids: tuple[str, ...] = ()
    required_scopes: tuple[str, ...] = ("user",)
    jwt_signing_key: str | None = None
    client_cache_path: Path | None = None

    def __post_init__(self) -> None:
        normalized = self.public_base_url.rstrip("/")
        if not normalized:
            raise ValueError("MCP_PUBLIC_BASE_URL is required for GitHub OAuth")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP_PUBLIC_BASE_URL must be an absolute http(s) URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError(
                "MCP_PUBLIC_BASE_URL must be an origin without path, query, or fragment"
            )
        host = (parsed.hostname or "").lower()
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and host not in local_hosts:
            raise ValueError("MCP_PUBLIC_BASE_URL must use https outside localhost")
        if not self.client_id:
            raise ValueError("GITHUB_OAUTH_CLIENT_ID is required for GitHub OAuth")
        if not self.client_secret:
            raise ValueError("GITHUB_OAUTH_CLIENT_SECRET is required for GitHub OAuth")
        allowed_ids = tuple(
            str(value).strip() for value in self.allowed_github_user_ids if str(value).strip()
        )
        if not allowed_ids:
            raise ValueError("MCP_GITHUB_ALLOWED_USER_IDS is required for GitHub OAuth")
        if any(not value.isdigit() for value in allowed_ids):
            raise ValueError("MCP_GITHUB_ALLOWED_USER_IDS must contain numeric GitHub user IDs")
        object.__setattr__(self, "public_base_url", normalized)
        object.__setattr__(self, "allowed_github_user_ids", allowed_ids)


class StaticBearerTokenVerifier(TokenVerifier):
    """FastMCP token verifier for the existing static MCP bearer token."""

    def __init__(
        self,
        expected_token: str,
        *,
        base_url: str | None = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="static-bearer",
            scopes=self.required_scopes or ["mcp"],
            resource=str(self._resource_url) if self._resource_url is not None else None,
            claims={"sub": "static-bearer", "auth_source": "static-bearer"},
        )


class _AllowedGitHubProvider(GitHubProvider):
    def __init__(self, *, allowed_github_user_ids: tuple[str, ...], **kwargs) -> None:
        super().__init__(**kwargs)
        self._allowed_github_user_ids = set(allowed_github_user_ids)

    async def get_client(self, client_id: str):
        client = await super().get_client(client_id)
        if client is not None:
            return client

        normalized = str(client_id or "").strip()
        if not _is_recoverable_dynamic_client_id(normalized):
            return None

        recovered = ProxyDCRClient(
            client_id=normalized,
            client_secret=None,
            redirect_uris=[AnyUrl("http://localhost")],
            grant_types=["authorization_code", "refresh_token"],
            scope=getattr(self, "_default_scope_str", "user") or "user",
            token_endpoint_auth_method="none",
            allowed_redirect_uri_patterns=getattr(self, "_allowed_client_redirect_uris", None),
            client_name="Recovered dynamic MCP OAuth client",
        )
        _LOGGER.warning(
            "Recovering missing dynamic OAuth client registration client_id=%s; "
            "check that MCP_OAUTH_CLIENT_CACHE is mounted persistently",
            _safe_client_log_id(normalized),
        )
        try:
            await self._client_store.put(key=normalized, value=recovered)
        except Exception:
            _LOGGER.exception(
                "Failed to persist recovered dynamic OAuth client client_id=%s; "
                "continuing for this authorization attempt",
                _safe_client_log_id(normalized),
            )
        return recovered

    async def verify_token(self, token: str) -> AccessToken | None:
        access_token = await super().verify_token(token)
        if access_token is None or not self._allowed_github_user_ids:
            return access_token
        github_id = str(access_token.client_id or access_token.claims.get("sub", ""))
        if github_id not in self._allowed_github_user_ids:
            return None
        return access_token


class JsonOAuthClientCache:
    """Persist OAuth DCR clients in a JSON file with an in-memory hot cache."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._persistent_clients = self._read_persistent_unlocked()
        self._transient: dict[str, dict[str, dict[str, Any]]] = {}

    def _is_persistent_collection(self, collection: str | None) -> bool:
        return collection == _OAUTH_CLIENT_COLLECTION

    @contextmanager
    def _persistent_file_lock_unlocked(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(f".{self._path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_persistent_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"OAuth client cache is malformed JSON: {self._path}") from exc
        except OSError:
            raise
        if not isinstance(raw, dict):
            raise ValueError(f"OAuth client cache root must be an object: {self._path}")
        collections = raw.get("collections")
        if not isinstance(collections, dict):
            raise ValueError(f"OAuth client cache collections must be an object: {self._path}")
        clients = collections.get(_OAUTH_CLIENT_COLLECTION)
        if not isinstance(clients, dict):
            raise ValueError(
                f"OAuth client cache collection {_OAUTH_CLIENT_COLLECTION!r} must be an object: {self._path}"
            )
        return {str(key): value for key, value in clients.items() if isinstance(value, dict)}

    def _write_persistent_unlocked(self, values: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "collections": {_OAUTH_CLIENT_COLLECTION: values},
        }
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                json.dump(payload, tmp, ensure_ascii=False, sort_keys=True, indent=2)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, self._path)
            _fsync_directory(self._path.parent)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if self._is_persistent_collection(collection):
                value = self._persistent_clients.get(key)
                if value is not None:
                    return dict(value)
                disk_values = self._read_persistent_unlocked()
                if disk_values:
                    self._persistent_clients = {**self._persistent_clients, **disk_values}
                    value = self._persistent_clients.get(key)
                return dict(value) if value is not None else None
            value = self._transient.get(collection or "", {}).get(key)
            return dict(value) if value is not None else None

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        return await self.get(key, collection=collection), None

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        del ttl
        with self._lock:
            value_dict = dict(value)
            if self._is_persistent_collection(collection):
                with self._persistent_file_lock_unlocked():
                    disk_values = self._read_persistent_unlocked()
                    values = {**self._persistent_clients, **disk_values, key: value_dict}
                    self._write_persistent_unlocked(values)
                    self._persistent_clients = values
                return
            self._transient.setdefault(collection or "", {})[key] = value_dict

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        with self._lock:
            if self._is_persistent_collection(collection):
                with self._persistent_file_lock_unlocked():
                    disk_values = self._read_persistent_unlocked()
                    values = {**self._persistent_clients, **disk_values}
                    existed = key in values
                    if not existed:
                        return False
                    del values[key]
                    self._write_persistent_unlocked(values)
                    self._persistent_clients = values
                return existed
            return self._transient.get(collection or "", {}).pop(key, None) is not None

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(key, collection=collection) for key in keys]

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(key, collection=collection) for key in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        deleted = 0
        for key in keys:
            if await self.delete(key, collection=collection):
                deleted += 1
        return deleted


def oauth_metadata_compat_app(app):
    """Patch FastMCP 3.2 OAuth metadata for public DCR clients."""

    async def wrapped(scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "GET"
            or scope.get("path") != "/.well-known/oauth-authorization-server"
        ):
            await app(scope, receive, send)
            return

        response_start = None
        body_parts = []

        async def capture_send(message):
            nonlocal response_start
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                start_message = response_start or {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [],
                }
                body = b"".join(body_parts)
                headers_in = start_message.get("headers", [])
                content_type = next(
                    (
                        value.decode("latin-1")
                        for key, value in headers_in
                        if key.lower() == b"content-type"
                    ),
                    "",
                )
                if start_message.get("status") == 200 and "json" in content_type.lower():
                    try:
                        metadata = json.loads(body)
                    except json.JSONDecodeError:
                        pass
                    else:
                        methods = list(metadata.get("token_endpoint_auth_methods_supported") or [])
                        if "none" not in methods:
                            methods.insert(0, "none")
                        metadata["token_endpoint_auth_methods_supported"] = methods
                        metadata["client_id_metadata_document_supported"] = False
                        body = json.dumps(metadata).encode("utf-8")
                        headers = [
                            (key, value)
                            for key, value in headers_in
                            if key.lower() not in {b"content-length", b"content-type"}
                        ]
                        headers.extend(
                            [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("latin-1")),
                            ]
                        )
                        start_message = {**start_message, "headers": headers}
                await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await app(scope, receive, capture_send)

    wrapped.routes = getattr(app, "routes", None)
    wrapped.router = getattr(app, "router", None)
    wrapped.lifespan = getattr(app, "lifespan", None)
    return wrapped


def build_mcp_github_oauth_provider(
    config: McpGitHubOAuthConfig,
    *,
    static_access_token: str | None = None,
):
    """Build the FastMCP auth provider for GitHub OAuth plus optional static bearer."""
    github_kwargs: dict[str, Any] = {
        "allowed_github_user_ids": config.allowed_github_user_ids,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "base_url": config.public_base_url,
        "issuer_url": config.public_base_url,
        "required_scopes": list(config.required_scopes),
        "jwt_signing_key": config.jwt_signing_key,
    }
    if config.client_cache_path is not None:
        github_kwargs["client_storage"] = JsonOAuthClientCache(config.client_cache_path)
    if "enable_cimd" in inspect.signature(GitHubProvider.__init__).parameters:
        github_kwargs["enable_cimd"] = False
    github_provider = _AllowedGitHubProvider(**github_kwargs)
    verifiers = []
    if static_access_token:
        verifiers.append(
            StaticBearerTokenVerifier(
                static_access_token,
                base_url=config.public_base_url,
                required_scopes=list(config.required_scopes),
            )
        )
    if verifiers:
        return MultiAuth(
            server=github_provider, verifiers=verifiers, base_url=config.public_base_url
        )
    return github_provider
