from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.mcp_server import (
    McpSnapshotConfig,
    _allowed_methods_for_transport,
)


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


if __name__ == "__main__":
    unittest.main()
