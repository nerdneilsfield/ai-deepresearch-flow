from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
COMPOSE_EXAMPLE = ROOT / "scripts" / "docker" / "docker-compose.example.yml"


def test_docker_compose_example_requires_private_mcp_access_token_for_all_profiles() -> None:
    content = COMPOSE_EXAMPLE.read_text(encoding="utf-8")

    assert "deploy-local-static:" in content
    assert "deploy-local-static-advanced:" in content
    assert "deploy-external-static:" in content
    assert "deploy-external-static-advanced:" in content
    assert content.count('MCP_ACCESS_TOKEN: "${MCP_ACCESS_TOKEN:?set MCP_ACCESS_TOKEN}"') == 4
    assert (
        content.count('SEARCH_ACCESS_TOKEN: "${SEARCH_ACCESS_TOKEN:?set SEARCH_ACCESS_TOKEN}"') == 2
    )
    assert "your-mcp-token" not in content
    assert "SEARCH_ACCESS_TOKEN: your-token" not in content
    assert content.count('"127.0.0.1:8080:8899"') == 4
    assert '"8080:8899"' not in content
    assert content.count("${PWD}/paper_snapshot.db:/db/papers.db") == 4
    assert "./paper_snapshot.db:/db/papers.db" not in content
