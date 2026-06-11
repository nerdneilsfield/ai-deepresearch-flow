from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def _render_template(template_name: str) -> str:
    template = ROOT / "scripts" / "docker" / template_name
    return template.read_text(encoding="utf-8").replace(
        "${PAPER_DB_API_BASE}",
        "http://127.0.0.1:8000",
    )


def _location_block(config_text: str, location_prefix: str) -> str:
    marker = f"location {location_prefix} {{"
    start = config_text.index(marker)
    end = config_text.index("\n        }", start)
    return config_text[start:end]


def test_nginx_api_proxy_locations_allow_long_running_requests() -> None:
    for template_name in ("nginx.conf.root.template", "nginx.conf.prefix.template"):
        rendered = _render_template(template_name)

        api_block = _location_block(rendered, "/api/")
        mcp_block = _location_block(rendered, "^~ /mcp")

        for block in (api_block, mcp_block):
            assert "proxy_connect_timeout 30s;" in block
            assert "proxy_read_timeout 300s;" in block
            assert "proxy_send_timeout 300s;" in block


def _proxy_pass_target(config_text: str, location_prefix: str) -> str:
    block = _location_block(config_text, location_prefix)
    marker = "proxy_pass "
    start = block.index(marker) + len(marker)
    end = block.index(";", start)
    return block[start:end].strip()


def test_nginx_root_oauth_operational_routes_are_api_routes() -> None:
    oauth_route_locations = (
        "^~ /.well-known/",
        "= /authorize",
        "= /token",
        "= /register",
        "= /auth/callback",
        "= /consent",
    )

    for template_name in ("nginx.conf.root.template", "nginx.conf.prefix.template"):
        rendered = _render_template(template_name)

        for location in oauth_route_locations:
            assert _proxy_pass_target(rendered, location) == "http://127.0.0.1:8000"


def test_nginx_mcp_sse_keeps_streaming_route_separate_from_streamable_mcp() -> None:
    for template_name in ("nginx.conf.root.template", "nginx.conf.prefix.template"):
        rendered = _render_template(template_name)

        mcp_block = _location_block(rendered, "^~ /mcp")
        sse_block = _location_block(rendered, "^~ /mcp-sse")

        assert _proxy_pass_target(rendered, "^~ /mcp") == "http://127.0.0.1:8000"
        assert _proxy_pass_target(rendered, "^~ /mcp-sse") == "http://127.0.0.1:8000"
        assert "proxy_read_timeout 300s;" in mcp_block
        assert "proxy_read_timeout 3600s;" in sse_block
        assert "proxy_buffering off;" in sse_block


def test_start_nginx_can_select_root_or_prefix_template() -> None:
    script = (ROOT / "scripts" / "docker" / "start-nginx.sh").read_text(encoding="utf-8")

    assert "PAPER_DB_NGINX_TEMPLATE" in script
    assert "root|prefix" in script
    assert "nginx.conf.${nginx_template}.template" in script
