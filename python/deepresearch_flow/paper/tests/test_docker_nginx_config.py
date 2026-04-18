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
