from __future__ import annotations

import asyncio
import httpx
import os
import tempfile
from pathlib import Path
from contextlib import suppress
import unittest

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.auth import McpGitHubOAuthConfig
from deepresearch_flow.paper.snapshot.mcp_server import (
    McpSnapshotConfig,
    _allowed_methods_for_transport,
    create_mcp_app,
    create_mcp_apps,
)

OAUTH_ROUTE_MATRIX = [
    {
        "external_path": "/mcp",
        "forwarded_internal_path": "/",
        "auth_provider": "static-bearer",
        "expected_status_or_challenge": "valid static bearer initializes; missing/invalid returns bare Bearer challenge",
        "protected_resource_metadata_route": None,
        "sse_message_post_path": None,
        "lifespan": "bearer-streamable-http",
    },
    {
        "external_path": "/mcp-sse",
        "forwarded_internal_path": "/",
        "auth_provider": "static-bearer",
        "expected_status_or_challenge": "valid static bearer opens SSE; missing/invalid returns bare Bearer challenge",
        "protected_resource_metadata_route": None,
        "sse_message_post_path": "/mcp-sse/messages/",
        "lifespan": "bearer-sse",
    },
    {
        "external_path": "/oauth/mcp",
        "forwarded_internal_path": "/oauth/mcp",
        "auth_provider": "github-oauth",
        "expected_status_or_challenge": "missing/invalid returns OAuth Bearer challenge for {base}/oauth/mcp",
        "protected_resource_metadata_route": "/.well-known/oauth-protected-resource/oauth/mcp",
        "sse_message_post_path": None,
        "lifespan": "oauth-streamable-http",
    },
    {
        "external_path": "/oauth/mcp-sse",
        "forwarded_internal_path": None,
        "auth_provider": "github-oauth",
        "expected_status_or_challenge": "absent unless OAuth SSE gate passes",
        "protected_resource_metadata_route": None,
        "sse_message_post_path": None,
        "lifespan": None,
    },
    {
        "external_path": "/.well-known/oauth-authorization-server",
        "forwarded_internal_path": "/.well-known/oauth-authorization-server",
        "auth_provider": "github-oauth",
        "expected_status_or_challenge": "authorization metadata",
        "protected_resource_metadata_route": None,
        "sse_message_post_path": None,
        "lifespan": "oauth-streamable-http",
    },
    {
        "external_path": "/register",
        "forwarded_internal_path": "/register",
        "auth_provider": "github-oauth",
        "expected_status_or_challenge": "dynamic client registration",
        "protected_resource_metadata_route": None,
        "sse_message_post_path": None,
        "lifespan": "oauth-streamable-http",
    },
]


async def _capture_response_start(
    app,
    *,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    request_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": request_headers,
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
    }

    response_started = asyncio.Event()
    captured: dict[str, object] = {}
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] != "http.response.start" or response_started.is_set():
            return
        captured["status_code"] = message["status"]
        captured["headers"] = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in message.get("headers", [])
        }
        response_started.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(response_started.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return captured


class TestMcpTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.snapshot_db = Path(cls.tmpdir.name) / "snapshot.db"
        cls.snapshot_db.touch()
        cls.cfg = McpSnapshotConfig(
            snapshot_db=cls.snapshot_db,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            mcp_access_token="test-mcp-token",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_streamable_transport_rejects_get(self) -> None:
        self.assertNotIn("GET", _allowed_methods_for_transport("streamable-http"))

    def test_sse_transport_allows_get(self) -> None:
        self.assertIn("GET", _allowed_methods_for_transport("sse"))

    def test_api_mounts_streamable_and_sse_endpoints(self) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token="test-mcp-token",
        )
        mount_paths = sorted(getattr(route, "path", "") for route in app.routes)
        self.assertIn("/mcp", mount_paths)
        self.assertIn("/mcp-sse", mount_paths)

    def test_api_rejects_public_mcp_without_explicit_unsafe_override(self) -> None:
        previous = os.environ.pop("MCP_PUBLIC_UNSAFE", None)
        try:
            with self.assertRaises(ValueError):
                create_app(
                    snapshot_db=self.snapshot_db,
                    static_base_url="",
                    cors_allowed_origins=["*"],
                    limits=ApiLimits(),
                )
            with self.assertRaises(ValueError):
                create_app(
                    snapshot_db=self.snapshot_db,
                    static_base_url="",
                    cors_allowed_origins=["*"],
                    limits=ApiLimits(),
                    mcp_access_token="your-mcp-token",
                )
        finally:
            if previous is None:
                os.environ.pop("MCP_PUBLIC_UNSAFE", None)
            else:
                os.environ["MCP_PUBLIC_UNSAFE"] = previous

    def test_api_allows_public_mcp_only_with_explicit_unsafe_override(self) -> None:
        previous = os.environ.get("MCP_PUBLIC_UNSAFE")
        try:
            os.environ["MCP_PUBLIC_UNSAFE"] = "1"
            app = create_app(
                snapshot_db=self.snapshot_db,
                static_base_url="",
                cors_allowed_origins=["*"],
                limits=ApiLimits(),
            )
        finally:
            if previous is None:
                os.environ.pop("MCP_PUBLIC_UNSAFE", None)
            else:
                os.environ["MCP_PUBLIC_UNSAFE"] = previous

        mount_paths = sorted(getattr(route, "path", "") for route in app.routes)
        self.assertIn("/mcp", mount_paths)
        self.assertIn("/mcp-sse", mount_paths)


class TestMcpTransportAuth(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.snapshot_db = Path(cls.tmpdir.name) / "snapshot.db"
        cls.snapshot_db.touch()
        cls.access_token = "test-mcp-token"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def _initialize_payload(self) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0"},
            },
        }

    async def test_mcp_mounts_require_bearer_before_transport_semantics(self) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token=self.access_token,
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp-sse/",
                    headers={"Accept": "text/event-stream"},
                )
                self.assertEqual(response["status_code"], 401)
                self.assertEqual(response["headers"].get("www-authenticate"), "Bearer")

                response = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp/",
                )
                self.assertEqual(response["status_code"], 401)
                self.assertEqual(response["headers"].get("www-authenticate"), "Bearer")

                response = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp-sse/",
                    headers={
                        "Accept": "text/event-stream",
                        "Authorization": "Bearer wrong-token",
                    },
                )
                self.assertEqual(response["status_code"], 401)
                self.assertEqual(response["headers"].get("www-authenticate"), "Bearer")

                headers = {"Authorization": f"Bearer {self.access_token}"}

                response = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp-sse/",
                    headers={
                        "Accept": "text/event-stream",
                        **headers,
                    },
                )
                self.assertEqual(response["status_code"], 200)
                self.assertTrue(
                    str(response["headers"].get("content-type", "")).startswith("text/event-stream")
                )

                response = await client.get("/mcp/", headers=headers)
                self.assertEqual(response.status_code, 405)

                response = await client.post("/mcp-sse/", headers=headers, json={})
                self.assertEqual(response.status_code, 405)

    async def test_streamable_mcp_accepts_authorized_initialize_without_trailing_slash(
        self,
    ) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token=self.access_token,
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=self._initialize_payload(),
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "Paper DB MCP")

    async def test_streamable_mcp_accepts_case_insensitive_bearer_scheme(self) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token=self.access_token,
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/mcp/",
                    headers={
                        "Authorization": f"bearer {self.access_token}",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=self._initialize_payload(),
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "Paper DB MCP")

    async def test_static_mode_does_not_expose_oauth_routes(self) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token=self.access_token,
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                oauth_mcp = await client.post("/oauth/mcp")
                protected_resource = await client.get(
                    "/.well-known/oauth-protected-resource/oauth/mcp"
                )
                authorization = await client.get("/.well-known/oauth-authorization-server")
                registration = await client.post("/register", json={})

        self.assertEqual(oauth_mcp.status_code, 404)
        self.assertEqual(protected_resource.status_code, 404)
        self.assertEqual(authorization.status_code, 404)
        self.assertEqual(registration.status_code, 404)


class TestMcpGitHubOAuth(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.snapshot_db = Path(cls.tmpdir.name) / "snapshot.db"
        cls.snapshot_db.touch()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def _app(self):
        return create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_auth_mode="github-oauth",
            mcp_public_base_url="https://papers.example.com",
            github_oauth_client_id="github-client",
            github_oauth_client_secret="github-secret",
            mcp_github_allowed_user_ids=["12345"],
            mcp_access_token="static-token",
        )

    def _initialize_payload(self) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0"},
            },
        }

    def test_github_oauth_rejects_incomplete_or_unsafe_configuration(self) -> None:
        base_kwargs = {
            "snapshot_db": self.snapshot_db,
            "static_base_url": "",
            "cors_allowed_origins": ["*"],
            "limits": ApiLimits(),
            "mcp_auth_mode": "github-oauth",
            "mcp_public_base_url": "https://papers.example.com",
            "github_oauth_client_id": "github-client",
            "github_oauth_client_secret": "github-secret",
            "mcp_github_allowed_user_ids": ["12345"],
            "mcp_access_token": "static-token",
        }
        invalid_cases = (
            {"mcp_access_token": None},
            {"mcp_public_base_url": ""},
            {"mcp_public_base_url": "https://papers.example.com/mcp"},
            {"mcp_public_base_url": "https://papers.example.com?x=1"},
            {"mcp_public_base_url": "http://papers.example.com"},
            {"github_oauth_client_id": ""},
            {"github_oauth_client_secret": ""},
            {"mcp_github_allowed_user_ids": []},
            {"mcp_github_allowed_user_ids": ["octocat"]},
        )

        for override in invalid_cases:
            kwargs = {**base_kwargs, **override}
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    create_app(**kwargs)

    def test_mcp_apps_reject_oauth_without_static_token_for_sse(self) -> None:
        cfg = McpSnapshotConfig(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            mcp_auth_mode="github-oauth",
            mcp_github_oauth=McpGitHubOAuthConfig(
                public_base_url="https://papers.example.com",
                client_id="github-client",
                client_secret="github-secret",
                allowed_github_user_ids=("12345",),
            ),
        )

        with self.assertRaises(ValueError):
            create_mcp_apps(cfg)

    def test_create_mcp_apps_uses_explicit_split_app_keys(self) -> None:
        static_cfg = McpSnapshotConfig(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            mcp_access_token="static-token",
        )
        static_apps, _ = create_mcp_apps(static_cfg)
        self.assertEqual(sorted(static_apps.keys()), ["bearer-sse", "bearer-streamable-http"])

        oauth_cfg = McpSnapshotConfig(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            mcp_auth_mode="github-oauth",
            mcp_access_token="static-token",
            mcp_github_oauth=McpGitHubOAuthConfig(
                public_base_url="https://papers.example.com",
                client_id="github-client",
                client_secret="github-secret",
                allowed_github_user_ids=("12345",),
            ),
        )
        oauth_apps, _ = create_mcp_apps(oauth_cfg)
        self.assertEqual(
            sorted(oauth_apps.keys()),
            ["bearer-sse", "bearer-streamable-http", "oauth-streamable-http"],
        )

    def test_single_transport_helper_rejects_github_oauth_config(self) -> None:
        cfg = McpSnapshotConfig(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            mcp_auth_mode="github-oauth",
        )

        with self.assertRaises(ValueError):
            create_mcp_app(cfg)

    async def test_oauth_mode_preserves_api_routes_before_mcp_catch_all(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://papers.example.com"
            ) as client:
                response = await client.get("/api/v1/config")
                unknown = await client.get("/definitely-not-an-oauth-route")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["static_base_url"], "")
        self.assertEqual(unknown.status_code, 404)

    async def test_oauth_discovery_is_root_level_for_oauth_mcp_resource(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://papers.example.com"
            ) as client:
                resource = await client.get("/.well-known/oauth-protected-resource/oauth/mcp")
                old_resource = await client.get("/.well-known/oauth-protected-resource/mcp")
                authorization = await client.get("/.well-known/oauth-authorization-server")
                nested = await client.get("/mcp/.well-known/oauth-authorization-server")
                registration = await client.post(
                    "/register",
                    json={
                        "redirect_uris": ["http://localhost:12345/callback"],
                        "token_endpoint_auth_method": "none",
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                    },
                )

        self.assertEqual(resource.status_code, 200)
        self.assertEqual(resource.json()["resource"], "https://papers.example.com/oauth/mcp")
        self.assertIn("https://papers.example.com/", resource.json()["authorization_servers"])
        self.assertNotEqual(old_resource.status_code, 200)
        self.assertEqual(authorization.status_code, 200)
        metadata = authorization.json()
        self.assertEqual(metadata["issuer"], "https://papers.example.com/")
        self.assertEqual(metadata["registration_endpoint"], "https://papers.example.com/register")
        self.assertIn("none", metadata["token_endpoint_auth_methods_supported"])
        self.assertFalse(metadata.get("client_id_metadata_document_supported", False))
        self.assertNotEqual(nested.status_code, 200)
        self.assertIn(registration.status_code, {200, 201})
        self.assertTrue(registration.json()["client_id"])
        self.assertEqual(registration.json()["token_endpoint_auth_method"], "none")

    async def test_oauth_mcp_challenges_with_resource_metadata_without_redirect(self) -> None:
        app = self._app()
        response = await _capture_response_start(app, method="POST", path="/oauth/mcp")

        self.assertEqual(response["status_code"], 401)
        challenge = str(response["headers"].get("www-authenticate", ""))
        self.assertIn("Bearer", challenge)
        self.assertIn(
            "https://papers.example.com/.well-known/oauth-protected-resource/oauth/mcp", challenge
        )

    async def test_oauth_mcp_probe_methods_return_oauth_challenge_without_redirect(self) -> None:
        for method in ("GET", "HEAD", "OPTIONS"):
            app = self._app()
            response = await _capture_response_start(app, method=method, path="/oauth/mcp")

            self.assertEqual(response["status_code"], 401)
            challenge = str(response["headers"].get("www-authenticate", ""))
            self.assertIn("Bearer", challenge)
            self.assertIn("oauth-protected-resource/oauth/mcp", challenge)

    async def test_sse_message_post_paths_do_not_redirect_or_leak_to_root(self) -> None:
        app = create_app(
            snapshot_db=self.snapshot_db,
            static_base_url="",
            cors_allowed_origins=["*"],
            limits=ApiLimits(),
            mcp_access_token="static-token",
        )
        async with app.router.lifespan_context(app):
            root = await _capture_response_start(
                app,
                method="POST",
                path="/messages",
                headers={"Authorization": "Bearer static-token"},
            )
            no_slash = await _capture_response_start(
                app,
                method="POST",
                path="/mcp-sse/messages",
                headers={"Authorization": "Bearer static-token"},
            )
            slash = await _capture_response_start(
                app,
                method="POST",
                path="/mcp-sse/messages/",
                headers={"Authorization": "Bearer static-token"},
            )

        self.assertEqual(root["status_code"], 404)
        self.assertNotIn(no_slash["status_code"], {307, 308})
        self.assertNotIn(slash["status_code"], {307, 308})

    async def test_oauth_mode_keeps_static_bearer_without_redirect_on_both_mcp_paths(self) -> None:
        for path in ("/mcp", "/mcp/"):
            app = self._app()
            transport = httpx.ASGITransport(app=app)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=transport, base_url="https://papers.example.com"
                ) as client:
                    response = await client.post(
                        path,
                        headers={
                            "Authorization": "Bearer static-token",
                            "Accept": "application/json, text/event-stream",
                            "Content-Type": "application/json",
                        },
                        json=self._initialize_payload(),
                    )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["serverInfo"]["name"], "Paper DB MCP")

    async def test_oauth_route_rejects_static_bearer_token(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://papers.example.com"
            ) as client:
                response = await client.post(
                    "/oauth/mcp",
                    headers={
                        "Authorization": "Bearer static-token",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=self._initialize_payload(),
                )

        self.assertEqual(response.status_code, 401)
        challenge = response.headers.get("www-authenticate", "")
        self.assertIn("oauth-protected-resource/oauth/mcp", challenge)

    async def test_static_route_rejects_oauth_looking_token_without_github_network(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://papers.example.com"
            ) as client:
                response = await client.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer gho_fake-token-that-must-not-call-github",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=self._initialize_payload(),
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    async def test_oauth_mode_keeps_sse_on_static_bearer_only(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://papers.example.com"
            ):
                missing = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp-sse",
                    headers={"Accept": "text/event-stream"},
                )
                allowed = await _capture_response_start(
                    app,
                    method="GET",
                    path="/mcp-sse",
                    headers={"Accept": "text/event-stream", "Authorization": "Bearer static-token"},
                )

        self.assertEqual(missing["status_code"], 401)
        self.assertEqual(missing["headers"].get("www-authenticate"), "Bearer")
        self.assertEqual(allowed["status_code"], 200)
        self.assertTrue(
            str(allowed["headers"].get("content-type", "")).startswith("text/event-stream")
        )

    async def test_oauth_sse_gate_is_explicitly_absent(self) -> None:
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://papers.example.com",
                follow_redirects=False,
            ) as client:
                sse = await client.get("/oauth/mcp-sse", headers={"Accept": "text/event-stream"})
                sse_slash = await client.get(
                    "/oauth/mcp-sse/", headers={"Accept": "text/event-stream"}
                )
                metadata = await client.get("/.well-known/oauth-protected-resource/oauth/mcp-sse")

        self.assertEqual(sse.status_code, 404)
        self.assertEqual(sse_slash.status_code, 404)
        self.assertEqual(metadata.status_code, 404)


if __name__ == "__main__":
    unittest.main()
