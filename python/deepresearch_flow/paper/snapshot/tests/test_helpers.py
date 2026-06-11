from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from starlette.applications import Starlette

from deepresearch_flow.paper.snapshot.mcp_server import (
    ApiLimits,
    McpSnapshotConfig,
    McpToolError,
    configure,
    create_mcp_app,
    create_mcp_apps,
    create_mcp_transport_app,
    filter_papers,
    list_top_facets,
    resource_metadata,
    resource_source,
    resource_summary_default,
    resource_translation,
    resolve_static_export_dir,
    search_papers,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestMcpSnapshotPublicBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.db_path = root / "snapshot.db"
        self.static_dir = root / "static"
        self.static_dir.mkdir(parents=True, exist_ok=True)
        self._seed_snapshot_db(include_bibtex=True, include_summary=True)
        configure(self._base_config())

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _base_config(self) -> McpSnapshotConfig:
        return McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="",
            static_export_dir=self.static_dir,
            limits=ApiLimits(max_query_length=8),
            origin_allowlist=["*"],
            mcp_access_token="test-mcp-token",
            max_paper_id_length=12,
        )

    def _seed_snapshot_db(self, *, include_bibtex: bool, include_summary: bool) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            init_snapshot_db(conn)
            conn.execute(
                """
                INSERT INTO paper(
                    paper_id, paper_key, paper_key_type, doi, title, year, month,
                    publication_date, venue, preferred_summary_template, summary_preview,
                    paper_index, source_hash, output_language, provider, model,
                    prompt_template, extracted_at, pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "p1",
                    "k1",
                    "doi",
                    "10.1000/xyz",
                    "Paper Title",
                    "2024",
                    "2024-01",
                    "2024-01-02",
                    "Test Venue",
                    "deep_read",
                    "Preview text",
                    1,
                    "hash",
                    "en",
                    "provider",
                    "model",
                    "tmpl",
                    "2024-01-03",
                    "pdfhash",
                    "mdhash",
                ),
            )
            if include_summary:
                conn.execute(
                    "INSERT INTO paper_summary(paper_id, template_tag) VALUES (?, ?)",
                    ("p1", "deep_read"),
                )
            if include_bibtex:
                conn.execute(
                    "INSERT INTO paper_bibtex(paper_id, bibtex_raw, bibtex_key, entry_type) VALUES (?, ?, ?, ?)",
                    ("p1", "@article{p1,title={Paper Title}}", "p1", "article"),
                )
            conn.commit()
        finally:
            conn.close()

    def _route_signature(self, app: object) -> list[dict[str, object]]:
        signature: list[dict[str, object]] = []
        for route in getattr(app, "routes", []):
            entry: dict[str, object] = {
                "type": type(route).__name__,
                "path": getattr(route, "path", None),
                "name": getattr(route, "name", None),
            }
            methods = getattr(route, "methods", None)
            if methods is not None:
                entry["methods"] = sorted(methods)
            signature.append(entry)
        return signature

    def test_tool_error_to_dict(self) -> None:
        error = McpToolError("bad_input", "Broken", detail="oops")
        self.assertEqual(error.code, "bad_input")
        self.assertEqual(error.details, {"detail": "oops"})
        self.assertEqual(
            error.to_dict(),
            {"error": "bad_input", "message": "Broken", "detail": "oops"},
        )

    def test_resolve_static_export_dir_uses_environment_override(self) -> None:
        previous = os.environ.get("PAPER_DB_STATIC_EXPORT_DIR")
        try:
            configure(
                McpSnapshotConfig(
                    snapshot_db=self.db_path,
                    static_base_url="",
                    static_export_dir=None,
                    limits=ApiLimits(max_query_length=8),
                    origin_allowlist=["*"],
                    max_paper_id_length=12,
                )
            )
            os.environ.pop("PAPER_DB_STATIC_EXPORT_DIR", None)
            self.assertIsNone(resolve_static_export_dir())

            override = self.static_dir / "env-static"
            override.mkdir(exist_ok=True)
            os.environ["PAPER_DB_STATIC_EXPORT_DIR"] = str(override)
            self.assertEqual(resolve_static_export_dir(), override)
        finally:
            if previous is None:
                os.environ.pop("PAPER_DB_STATIC_EXPORT_DIR", None)
            else:
                os.environ["PAPER_DB_STATIC_EXPORT_DIR"] = previous

    def test_http_client_is_reused_for_the_same_config(self) -> None:
        cfg = self._base_config()
        client1 = cfg.get_http_client()
        client2 = cfg.get_http_client()
        self.assertIs(client1, client2)

    def test_http_client_is_reused_under_concurrent_initialization(self) -> None:
        cfg = self._base_config()
        clients = []
        threads = [
            threading.Thread(target=lambda: clients.append(cfg.get_http_client()))
            for _ in range(8)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(clients), 8)
        self.assertTrue(all(client is clients[0] for client in clients))

    def test_http_client_can_be_closed_and_recreated(self) -> None:
        cfg = self._base_config()
        client1 = cfg.get_http_client()

        cfg.close_http_client()

        self.assertTrue(client1.is_closed)
        client2 = cfg.get_http_client()
        self.assertIsNot(client1, client2)

    def test_create_mcp_transport_app_does_not_override_default_config(self) -> None:
        summary_file = self.static_dir / "summary" / "p1.json"
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text('{"summary": "base"}', encoding="utf-8")
        configure(self._base_config())

        alt_static_dir = Path(self.tmpdir.name) / "alt-static"
        (alt_static_dir / "summary").mkdir(parents=True, exist_ok=True)
        (alt_static_dir / "summary" / "p1.json").write_text('{"summary": "other"}', encoding="utf-8")
        alt_cfg = McpSnapshotConfig(
            snapshot_db=self.db_path,
            static_base_url="",
            static_export_dir=alt_static_dir,
            limits=ApiLimits(max_query_length=8),
            origin_allowlist=["*"],
            mcp_access_token="test-mcp-token",
            max_paper_id_length=12,
        )

        app, lifespan = create_mcp_transport_app(alt_cfg, transport="sse")

        self.assertTrue(callable(lifespan))
        self.assertEqual(resource_summary_default("p1"), '{"summary": "base"}')
        self.assertIsNotNone(app)

    def test_create_mcp_apps_shared_lifespan_closes_shared_http_client(self) -> None:
        cfg = self._base_config()
        client1 = cfg.get_http_client()
        apps, lifespan = create_mcp_apps(cfg)

        self.assertEqual(sorted(apps.keys()), ["sse", "streamable-http"])
        self.assertTrue(callable(lifespan))

        async def run_lifespan() -> None:
            app = Starlette()
            async with lifespan(app):
                self.assertIs(cfg.get_http_client(), client1)

        asyncio.run(run_lifespan())

        self.assertTrue(client1.is_closed)
        client2 = cfg.get_http_client()
        self.assertIsNot(client1, client2)

    def test_resource_metadata_reflects_database_state(self) -> None:
        payload = json.loads(resource_metadata("p1"))
        self.assertEqual(payload["paper_id"], "p1")
        self.assertEqual(payload["title"], "Paper Title")
        self.assertEqual(payload["venue"], "Test Venue")
        self.assertEqual(payload["preferred_summary_template"], "deep_read")
        self.assertEqual(payload["available_summary_templates"], ["deep_read"])
        self.assertTrue(payload["has_bibtex"])

    def test_list_top_facets_clamps_limit_to_configured_page_size(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=self.static_dir,
                limits=ApiLimits(max_page_size=1),
                origin_allowlist=["*"],
            )
        )
        values = list_top_facets("venue", limit=1000)

        self.assertLessEqual(len(values), 1)

    def test_list_top_facets_rejects_non_integer_limit(self) -> None:
        for invalid_limit in ("many", "10", 1.5, True, 0, -1):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaises(McpToolError) as ctx:
                    list_top_facets("venue", limit=invalid_limit)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.code, "invalid_limit")

    def test_search_papers_rejects_non_integer_limit(self) -> None:
        for invalid_limit in ("many", "10", 1.5, True, 0, -1):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaises(McpToolError) as ctx:
                    search_papers("Paper", limit=invalid_limit)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.code, "invalid_limit")

    def test_filter_papers_rejects_non_integer_limit(self) -> None:
        for invalid_limit in ("many", "10", 1.5, True, 0, -1):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaises(McpToolError) as ctx:
                    filter_papers(venue="Test", limit=invalid_limit)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.code, "invalid_limit")

    def test_resource_metadata_missing_paper_raises_public_error(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            resource_metadata("missing")
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertIn("paper not found", str(ctx.exception))

    def test_resource_summary_default_returns_local_summary_content(self) -> None:
        summary_file = self.static_dir / "summary" / "p1.json"
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text('{"summary": "ready"}', encoding="utf-8")

        self.assertEqual(resource_summary_default("p1"), '{"summary": "ready"}')

    def test_resource_summary_default_raises_public_error_when_static_asset_is_unavailable(self) -> None:
        configure(
            McpSnapshotConfig(
                snapshot_db=self.db_path,
                static_base_url="",
                static_export_dir=None,
                limits=ApiLimits(max_query_length=8),
                origin_allowlist=["*"],
                max_paper_id_length=12,
            )
        )
        try:
            with self.assertRaises(McpToolError) as ctx:
                resource_summary_default("p1")
            self.assertEqual(ctx.exception.code, "asset_fetch_failed")
            self.assertIn("Failed to fetch summary asset", str(ctx.exception))
        finally:
            configure(self._base_config())

    def test_resource_source_reports_missing_asset_cleanly(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            resource_source("p1")
        self.assertEqual(ctx.exception.code, "asset_fetch_failed")
        self.assertIn("Failed to fetch source asset", str(ctx.exception))

    def test_resource_source_returns_markdown_when_local_asset_exists(self) -> None:
        source_file = self.static_dir / "md" / "mdhash.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("# Paper Title\n\nSource body", encoding="utf-8")

        self.assertEqual(resource_source("p1"), "# Paper Title\n\nSource body")

    def test_resource_translation_rejects_unsafe_language_path(self) -> None:
        with self.assertRaises(McpToolError) as ctx:
            resource_translation("p1", "../zh")

        self.assertEqual(ctx.exception.code, "invalid_lang")

    def test_create_mcp_app_exposes_streamable_http_route(self) -> None:
        app, lifespan = create_mcp_app(self._base_config())
        self.assertTrue(callable(lifespan))
        self.assertIn(
            {
                "type": "Route",
                "path": "/",
                "name": "StreamableHTTPASGIApp",
                "methods": ["DELETE", "POST"],
            },
            self._route_signature(app),
        )

    def test_create_mcp_transport_app_exposes_sse_routes(self) -> None:
        app, lifespan = create_mcp_transport_app(self._base_config(), transport="sse")
        self.assertTrue(callable(lifespan))
        signature = self._route_signature(app)
        self.assertIn(
            {
                "type": "Route",
                "path": "/",
                "name": "sse_endpoint",
                "methods": ["GET", "HEAD"],
            },
            signature,
        )
        self.assertIn(
            {
                "type": "Mount",
                "path": "/messages",
                "name": None,
            },
            signature,
        )


if __name__ == "__main__":
    unittest.main()
