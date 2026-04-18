from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "docker" / "start-api.sh"
)


def _write_fake_cli(tmp_path: Path) -> tuple[Path, Path]:
    args_file = tmp_path / "args.txt"
    token_file = tmp_path / "token.txt"
    cli_path = tmp_path / "deepresearch-flow"
    cli_path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\n' \"$@\" > \"$CAPTURE_ARGS_FILE\"\n"
        "printf '%s' \"${SEARCH_ACCESS_TOKEN:-}\" > \"$CAPTURE_TOKEN_FILE\"\n",
        encoding="utf-8",
    )
    cli_path.chmod(cli_path.stat().st_mode | stat.S_IEXEC)
    return args_file, token_file


def _run_start_api(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    args_file, token_file = _write_fake_cli(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "CAPTURE_ARGS_FILE": str(args_file),
            "CAPTURE_TOKEN_FILE": str(token_file),
            "PAPER_DB_SNAPSHOT_DB": "/db/papers.db",
            "PAPER_DB_STATIC_BASE": "/static",
        }
    )
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=Path(__file__).resolve().parents[4],
        env=env,
        capture_output=True,
        text=True,
    )


def test_start_api_script_uses_basic_mode_without_advanced_envs(tmp_path: Path) -> None:
    result = _run_start_api(tmp_path)

    assert result.returncode == 0
    args = (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines()
    assert "--snapshot-db" in args
    assert "/db/papers.db" in args
    assert "--embed-db" not in args
    assert "--config" not in args


def test_start_api_script_rejects_partial_advanced_env_configuration(tmp_path: Path) -> None:
    result = _run_start_api(tmp_path, PAPER_DB_EMBED_DB="/db/paper_vectors")

    assert result.returncode != 0
    assert "advanced" in result.stderr.lower()
    assert "PAPER_DB_EMBED_DB" in result.stderr
    assert "PAPER_DB_CONFIG" in result.stderr
    assert not (tmp_path / "args.txt").exists()


def test_start_api_script_uses_embedded_mode_with_multiple_advanced_envs(tmp_path: Path) -> None:
    result = _run_start_api(
        tmp_path,
        PAPER_DB_EMBED_DB="/db/paper_vectors",
        PAPER_DB_CONFIG="/app/config.toml",
        SEARCH_ACCESS_TOKEN="docker-token",
    )

    assert result.returncode == 0
    args = (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines()
    assert "--embed-db" in args
    assert "/db/paper_vectors" in args
    assert "--config" in args
    assert "/app/config.toml" in args
    assert (tmp_path / "token.txt").read_text(encoding="utf-8") == "docker-token"
