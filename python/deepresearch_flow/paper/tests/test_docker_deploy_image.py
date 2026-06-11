from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
DOCKERFILE = ROOT / "scripts" / "docker" / "Dockerfile.deploy"


def test_deploy_dockerfile_has_api_healthcheck() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "HEALTHCHECK" in content
    assert "http://127.0.0.1:8899/api/v1/config" in content
