from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
COMPOSE_EXAMPLE = ROOT / "scripts" / "docker" / "docker-compose.example.yml"


def test_docker_compose_example_sets_mcp_access_token_for_all_profiles() -> None:
    content = COMPOSE_EXAMPLE.read_text(encoding="utf-8")

    assert "deploy-local-static:" in content
    assert "deploy-local-static-advanced:" in content
    assert "deploy-external-static:" in content
    assert "deploy-external-static-advanced:" in content
    assert content.count("MCP_ACCESS_TOKEN: your-mcp-token") == 4
