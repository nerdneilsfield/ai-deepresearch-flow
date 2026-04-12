from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from deepresearch_flow.paper.snapshot.mcp_server import (
    ApiLimits,
    McpSnapshotConfig,
    McpToolError,
    configure,
    create_mcp_app,
    create_mcp_transport_app,
    resource_metadata,
    resource_source,
    resource_summary_default,
    resolve_static_export_dir,
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

    def test_resource_metadata_reflects_database_state(self) -> None:
        payload = json.loads(resource_metadata("p1"))
        self.assertEqual(payload["paper_id"], "p1")
        self.assertEqual(payload["title"], "Paper Title")
        self.assertEqual(payload["venue"], "Test Venue")
        self.assertEqual(payload["preferred_summary_template"], "deep_read")
        self.assertEqual(payload["available_summary_templates"], ["deep_read"])
        self.assertTrue(payload["has_bibtex"])

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
