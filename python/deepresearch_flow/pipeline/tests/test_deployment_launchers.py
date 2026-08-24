from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[4]
DOCKER = ROOT / "scripts" / "docker"


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _pipeline_config(path: Path, *, enabled: bool) -> Path:
    path.write_text(
        "[pipeline]\n"
        f"enabled = {'true' if enabled else 'false'}\n"
        "[pipeline.models.ocr]\nallowlist=['ocr/test']\ndefault='ocr/test'\n"
        "[pipeline.models.extract]\nallowlist=['extract/test']\ndefault='extract/test'\n"
        "[pipeline.models.translate]\nallowlist=['translate/test']\ndefault='translate/test'\n",
        encoding="utf-8",
    )
    return path


def test_start_nginx_renders_default_and_custom_body_limit_without_work_alias(
    tmp_path: Path,
) -> None:
    fake_nginx = _executable(tmp_path / "nginx", "#!/bin/sh\nexit 0\n")
    output = tmp_path / "nginx.conf"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_nginx.parent}:{env['PATH']}",
            "PAPER_DB_NGINX_CONFIG_PATH": str(output),
            "PAPER_DB_NGINX_TEMPLATE_DIR": str(DOCKER),
            "PAPER_DB_API_BASE": "http://api:8000",
            "PAPER_DB_NGINX_TEMPLATE": "root",
        }
    )
    result = subprocess.run(
        ["bash", str(DOCKER / "start-nginx.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    assert "client_max_body_size 500m;" in rendered
    assert "proxy_read_timeout 1800s;" in rendered
    assert "pipeline-work" not in rendered

    env["PAPER_DB_NGINX_BODY_LIMIT"] = "64m"
    env["PAPER_DB_NGINX_TEMPLATE"] = "prefix"
    result = subprocess.run(
        ["bash", str(DOCKER / "start-nginx.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "client_max_body_size 64m;" in output.read_text(encoding="utf-8")


def test_start_nginx_rejects_invalid_body_limit(tmp_path: Path) -> None:
    fake_nginx = _executable(tmp_path / "nginx", "#!/bin/sh\nexit 9\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_nginx.parent}:{env['PATH']}",
            "PAPER_DB_NGINX_CONFIG_PATH": str(tmp_path / "nginx.conf"),
            "PAPER_DB_NGINX_BODY_LIMIT": "500m; add_header X-Leak yes",
        }
    )
    result = subprocess.run(
        ["bash", str(DOCKER / "start-nginx.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "BODY_LIMIT" in result.stderr


def test_start_supervisor_materializes_worker_only_for_consistent_enabled_config(
    tmp_path: Path,
) -> None:
    fake_supervisor = _executable(
        tmp_path / "supervisord",
        "#!/bin/sh\n"
        "printf '%s' \"$2\" > \"$CAPTURE_CONFIG\"\n"
        "exit 0\n",
    )
    template = tmp_path / "supervisord.conf"
    template.write_text("[supervisord]\nnodaemon=true\n", encoding="utf-8")
    output = tmp_path / "rendered.conf"
    disabled_config = _pipeline_config(tmp_path / "disabled.toml", enabled=False)
    env = os.environ.copy()
    env.update(
        {
            "SUPERVISOR_BIN": str(fake_supervisor),
            "SUPERVISOR_CONFIG_TEMPLATE": str(template),
            "SUPERVISOR_CONFIG_OUTPUT": str(output),
            "CAPTURE_CONFIG": str(tmp_path / "captured"),
            "PAPER_DB_CONFIG": str(disabled_config),
            "PYTHONPATH": str(ROOT / "python"),
            "PYTHON_BIN": str(ROOT / ".venv" / "bin" / "python"),
        }
    )
    result = subprocess.run(
        ["bash", str(DOCKER / "start-supervisor.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "program:pipeline-worker" not in output.read_text(encoding="utf-8")

    env["PAPER_PIPELINE_ENABLED"] = "0"
    env.pop("PAPER_DB_CONFIG")
    result = subprocess.run(
        ["bash", str(DOCKER / "start-supervisor.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "program:pipeline-worker" not in output.read_text(encoding="utf-8")

    enabled_config = _pipeline_config(tmp_path / "enabled.toml", enabled=True)
    ocr_config = tmp_path / "ocr.toml"
    ocr_config.write_text("[backend]\ntype='fake'\napi_url='http://ocr'\ntoken='x'\n", encoding="utf-8")
    env.update(
        {
            "PAPER_PIPELINE_ENABLED": "1",
            "PAPER_DB_CONFIG": str(enabled_config),
            "PAPER_DB_ADMIN_TOKEN": "admin-token",
            "PAPER_OCR_CONFIG": str(ocr_config),
        }
    )
    result = subprocess.run(
        ["bash", str(DOCKER / "start-supervisor.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    assert rendered.count("program:pipeline-worker") == 1
    assert "stopsignal=TERM" in rendered
    assert "stopwaitsecs=120" in rendered

    env["PAPER_PIPELINE_ENABLED"] = "0"
    result = subprocess.run(
        ["bash", str(DOCKER / "start-supervisor.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid or inconsistent" in result.stderr
