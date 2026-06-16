from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "verification" / "generate_inventory.py"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_inventory_cli_emits_tracked_repo_surfaces_and_stable_ids(tmp_path: Path) -> None:
    _run(["git", "init", "-q"], tmp_path)
    _write(
        tmp_path / "pkg" / "mod.py",
        "class Service:\n    async def run(self, value: str) -> str:\n        return value\n",
    )
    _write(tmp_path / "constants.py", "def root_helper(value: int) -> int:\n    return value\n")
    _write(tmp_path / "tests" / "test_mod.py", "def test_public_behavior():\n    assert True\n")
    _write(tmp_path / "tests" / "conftest.py", "# shared fixtures\n")
    _write(tmp_path / "tests" / "_helper.py", "def helper():\n    return 1\n")
    _write(
        tmp_path / "frontend" / "src" / "lib" / "api.ts",
        "export function fetchPaper(id: string): string { return id }\n",
    )
    _write(
        tmp_path / "frontend" / "src" / "__tests__" / "api.test.ts",
        "import { fetchPaper } from '../lib/api'\n",
    )
    _write(tmp_path / "frontend" / "src" / "__tests__" / "fixtures.ts", "export const x = 1\n")
    _write(
        tmp_path / "frontend" / "src" / "views" / "PaperView.vue",
        '<script setup lang="ts">\nconst props = defineProps<{ id: string }>()\nconst emit = defineEmits<{ done: [] }>()\n</script>\n',
    )
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\nversion = "1.2.3"\n')
    _write(
        tmp_path / "package.json", '{"scripts":{"test":"vitest"},"dependencies":{"vue":"1.0.0"}}\n'
    )
    _write(
        tmp_path / ".github" / "workflows" / "push-to-pypi.yml",
        "name: publish\non: push\npermissions: {}\n",
    )
    _write(tmp_path / ".dockerignore", "node_modules\n")
    _write(tmp_path / "scripts" / "docker" / "Dockerfile.deploy", "FROM python:3.12\n")
    _write(
        tmp_path / "scripts" / "docker" / "docker-compose.example.yml",
        "services:\n  app:\n    image: demo\n",
    )
    _write(tmp_path / "scripts" / "docker" / "start-api.sh", "#!/usr/bin/env bash\necho start\n")
    _write(tmp_path / "scripts" / "docker" / "nginx.conf.http.template", "server { listen 80; }\n")
    _write(tmp_path / "scripts" / "docker" / "supervisord.conf", "[supervisord]\n")
    _write(tmp_path / "openspec" / "changes" / "demo.md", "# Demo\n")
    _write(tmp_path / "frontend" / "public" / "pdfjs" / "viewer.js", "// vendored\n")
    _run(["git", "add", "."], tmp_path)

    result = _run([sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--stdout"], tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    paths = {item["path"]: item for item in payload["items"]}
    assert "pkg/mod.py" in paths
    assert "constants.py" in paths
    assert "tests/test_mod.py" in paths
    assert "frontend/src/__tests__/api.test.ts" in paths
    assert "frontend/src/views/PaperView.vue" in paths
    assert ".github/workflows/push-to-pypi.yml" in paths
    assert "scripts/docker/Dockerfile.deploy" in paths
    assert "scripts/docker/nginx.conf.http.template" in paths
    assert "openspec/changes/demo.md" in paths
    assert paths["tests/test_mod.py"]["classification"] == "test"
    assert paths["tests/conftest.py"]["classification"] == "test_support"
    assert paths["tests/_helper.py"]["classification"] == "test_support"
    assert paths["frontend/src/__tests__/api.test.ts"]["classification"] == "test"
    assert paths["frontend/src/__tests__/fixtures.ts"]["classification"] == "test_support"
    assert paths[".github/workflows/push-to-pypi.yml"]["kind"] == "release_publish_workflow"
    assert paths["scripts/docker/Dockerfile.deploy"]["kind"] == "container_build"
    assert paths["scripts/docker/nginx.conf.http.template"]["kind"] == "reverse_proxy_config"
    assert (
        paths["frontend/public/pdfjs/viewer.js"]["artifact_group_id"]
        == "vendor:frontend-public-pdfjs"
    )
    symbol_ids = {
        symbol["stable_id"] for item in payload["items"] for symbol in item.get("symbols", [])
    }
    assert any(symbol_id.startswith("py:pkg.mod:Service.run#") for symbol_id in symbol_ids)
    assert any(symbol_id.startswith("py:constants:root_helper#") for symbol_id in symbol_ids)
    assert any(
        symbol_id.startswith("fe:frontend/src/lib/api.ts:fetchPaper#") for symbol_id in symbol_ids
    )
    assert any(
        symbol_id.startswith("fe:frontend/src/views/PaperView.vue:component#")
        for symbol_id in symbol_ids
    )
    assert all("@" not in symbol_id.split("#", 1)[0] for symbol_id in symbol_ids)
    config_ids = {entry["config_id"] for entry in payload["config_items"]}
    assert "toml:pyproject.toml:project.version" in config_ids
    assert "json:package.json:/scripts/test" in config_ids
    evidence_paths = {entry["path"] for entry in payload["evidence_assets"]}
    assert "tests/test_mod.py" in evidence_paths
    assert "frontend/src/__tests__/api.test.ts" in evidence_paths
    assert "tests/conftest.py" not in evidence_paths
    assert "tests/_helper.py" not in evidence_paths
    assert "frontend/src/__tests__/fixtures.ts" not in evidence_paths
    evidence_commands = {entry["path"]: entry["command"] for entry in payload["evidence_assets"]}
    assert (
        evidence_commands["frontend/src/__tests__/api.test.ts"]
        == "cd frontend && npm test -- --run src/__tests__/api.test.ts"
    )
    groups = {entry["artifact_group_id"]: entry for entry in payload["artifact_groups"]}
    assert groups["vendor:frontend-public-pdfjs"]["file_count"] == 1


def test_inventory_check_reports_stale_generated_file(tmp_path: Path) -> None:
    _run(["git", "init", "-q"], tmp_path)
    _write(tmp_path / "a.py", "def f() -> int:\n    return 1\n")
    _run(["git", "add", "."], tmp_path)
    output = tmp_path / "inventory.json"
    first = _run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--output", str(output)], tmp_path
    )
    assert first.returncode == 0, first.stderr
    _write(tmp_path / "b.py", "def g() -> int:\n    return 2\n")
    _run(["git", "add", "b.py"], tmp_path)

    result = _run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--output", str(output), "--check"],
        tmp_path,
    )

    assert result.returncode != 0
    assert "inventory is stale" in result.stderr
