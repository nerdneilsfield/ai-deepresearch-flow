from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

import httpx

from deepresearch_flow.paper.snapshot.common import (
    ApiLimits,
    _column_exists,
    _open_ro_conn,
    _open_rw_conn,
    _table_exists,
)
from deepresearch_flow.paper.snapshot.mcp_server import (
    McpSnapshotConfig,
    McpToolError,
    _fetch_static_text,
    _load_static_text,
    _truncate,
    _validate_paper_id,
    _validate_query,
    configure,
    resolve_static_export_dir,
)


class TestSnapshotCommonHelpers(unittest.TestCase):
    def test_open_rw_and_ro_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "snapshot.db"

            rw_conn = _open_rw_conn(db_path)
            try:
                self.assertIs(rw_conn.row_factory, sqlite3.Row)
                rw_conn.execute("CREATE TABLE paper (paper_id TEXT PRIMARY KEY, title TEXT)")
                rw_conn.execute(
                    "INSERT INTO paper(paper_id, title) VALUES (?, ?)",
                    ("p1", "Paper One"),
                )
                rw_conn.commit()
            finally:
                rw_conn.close()

            ro_conn = _open_ro_conn(db_path)
            try:
                row = ro_conn.execute("SELECT paper_id, title FROM paper").fetchone()
                assert row is not None
                self.assertEqual(row["paper_id"], "p1")
                self.assertEqual(row["title"], "Paper One")
            finally:
                ro_conn.close()

    def test_table_and_column_exists(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE paper (paper_id TEXT PRIMARY KEY, title TEXT)")
            self.assertTrue(_table_exists(conn, "paper"))
            self.assertFalse(_table_exists(conn, "missing_table"))
            self.assertTrue(_column_exists(conn, "paper", "title"))
            self.assertFalse(_column_exists(conn, "paper", "doi"))
            self.assertFalse(_column_exists(conn, "missing_table", "title"))
        finally:
            conn.close()


class TestMcpServerHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "snapshot.db"
        cls.static_dir = root / "static"
        cls.static_dir.mkdir(parents=True, exist_ok=True)
        cls.db_path.touch()
        configure(
            McpSnapshotConfig(
                snapshot_db=cls.db_path,
                static_base_url="",
                static_export_dir=cls.static_dir,
                limits=ApiLimits(max_query_length=8),
                origin_allowlist=["*"],
                max_paper_id_length=12,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def setUp(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=self.static_dir,
                limits=ApiLimits(max_query_length=8),
                origin_allowlist=["*"],
                max_paper_id_length=12,
            )
        )

    def test_tool_error_to_dict(self) -> None:
        error = McpToolError("bad_input", "Broken", detail="oops")
        self.assertEqual(error.code, "bad_input")
        self.assertEqual(error.details, {"detail": "oops"})
        self.assertEqual(
            error.to_dict(),
            {"error": "bad_input", "message": "Broken", "detail": "oops"},
        )

    def test_validate_query_success_and_failures(self) -> None:
        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(max_query_length=8),
            origin_allowlist=["*"],
        )

        self.assertEqual(_validate_query("  graph  ", cfg), "graph")

        with self.assertRaises(McpToolError) as empty_ctx:
            _validate_query("   ", cfg)
        self.assertEqual(empty_ctx.exception.code, "invalid_query")

        with self.assertRaises(McpToolError) as long_ctx:
            _validate_query("graphsage", cfg)
        self.assertEqual(long_ctx.exception.code, "query_too_long")
        self.assertEqual(long_ctx.exception.details["max_length"], 8)
        self.assertEqual(long_ctx.exception.details["length"], 9)

    def test_validate_paper_id_success_and_failures(self) -> None:
        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
            max_paper_id_length=12,
        )

        self.assertEqual(_validate_paper_id("paper_01-ok", cfg), "paper_01-ok")

        with self.assertRaises(McpToolError) as empty_ctx:
            _validate_paper_id("", cfg)
        self.assertEqual(empty_ctx.exception.code, "invalid_paper_id")

        with self.assertRaises(McpToolError) as long_ctx:
            _validate_paper_id("paper-id-too-long", cfg)
        self.assertEqual(long_ctx.exception.code, "paper_id_too_long")

        with self.assertRaises(McpToolError) as format_ctx:
            _validate_paper_id("paper:id", cfg)
        self.assertEqual(format_ctx.exception.code, "invalid_paper_id_format")

    def test_truncate_behaviors(self) -> None:
        self.assertEqual(_truncate("abcdef", None), "abcdef")
        self.assertEqual(_truncate("abcdef", 0), "abcdef")
        self.assertEqual(_truncate("abc", 10), "abc")
        self.assertEqual(_truncate("abcdef", 3), "abc\n[truncated: 3 more chars]")

    def test_load_static_text_prefers_local_export_dir(self) -> None:
        target = self.static_dir / "summary" / "paper.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hello world", encoding="utf-8")

        self.assertEqual(_load_static_text("summary/paper.json"), "hello world")

    def test_get_http_client_reuses_shared_client(self) -> None:
        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="https://example.com/static",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
        )

        client_a = cfg.get_http_client()
        client_b = cfg.get_http_client()

        self.assertIs(client_a, client_b)

    def test_fetch_and_load_static_text_over_http(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://example.com/static/summary/paper.json")
            return httpx.Response(200, text="remote payload")

        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="https://example.com/static",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
        )
        object.__setattr__(cfg, "_http_client", httpx.Client(transport=httpx.MockTransport(handler)))
        configure(cfg)

        self.assertEqual(_fetch_static_text("summary/paper.json"), "remote payload")
        self.assertEqual(_load_static_text("summary/paper.json"), "remote payload")

    def test_load_static_text_wraps_http_status_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="missing")

        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="https://example.com/static",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
        )
        object.__setattr__(cfg, "_http_client", httpx.Client(transport=httpx.MockTransport(handler)))
        configure(cfg)

        with self.assertRaises(RuntimeError) as ctx:
            _load_static_text("summary/missing.json")
        self.assertEqual(str(ctx.exception), "asset_fetch_failed:404")

    def test_load_static_text_wraps_request_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="https://example.com/static",
            static_export_dir=None,
            limits=ApiLimits(),
            origin_allowlist=["*"],
        )
        object.__setattr__(cfg, "_http_client", httpx.Client(transport=httpx.MockTransport(handler)))
        configure(cfg)

        with self.assertRaises(RuntimeError) as ctx:
            _load_static_text("summary/missing.json")
        self.assertEqual(str(ctx.exception), "asset_fetch_failed:request_error")

    def test_load_static_text_raises_when_unconfigured(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=None,
                limits=ApiLimits(),
                origin_allowlist=["*"],
            )
        )
        try:
            with self.assertRaises(RuntimeError) as ctx:
                _load_static_text("summary/missing.json")
            self.assertEqual(str(ctx.exception), "asset_fetch_failed:not_configured")
        finally:
            configure(
                McpSnapshotConfig(
                    snapshot_db=self.db_path,
                    static_base_url="",
                    static_export_dir=self.static_dir,
                    limits=ApiLimits(max_query_length=8),
                    origin_allowlist=["*"],
                    max_paper_id_length=12,
                )
            )

    def test_resolve_static_export_dir(self) -> None:
        import os

        previous = os.environ.get("PAPER_DB_STATIC_EXPORT_DIR")
        try:
            os.environ.pop("PAPER_DB_STATIC_EXPORT_DIR", None)
            self.assertIsNone(resolve_static_export_dir())

            temp_path = self.static_dir / "env-static"
            temp_path.mkdir(exist_ok=True)
            os.environ["PAPER_DB_STATIC_EXPORT_DIR"] = str(temp_path)
            self.assertEqual(resolve_static_export_dir(), temp_path)
        finally:
            if previous is None:
                os.environ.pop("PAPER_DB_STATIC_EXPORT_DIR", None)
            else:
                os.environ["PAPER_DB_STATIC_EXPORT_DIR"] = previous
