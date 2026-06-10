from __future__ import annotations

import asyncio
import httpx
import tempfile
from pathlib import Path
from contextlib import suppress
import unittest

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.mcp_server import (
    McpSnapshotConfig,
    _allowed_methods_for_transport,
)


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
        await asyncio.Event().wait()

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
        )
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
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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

    async def test_streamable_mcp_accepts_authorized_initialize_without_trailing_slash(self) -> None:
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
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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


if __name__ == "__main__":
    unittest.main()
