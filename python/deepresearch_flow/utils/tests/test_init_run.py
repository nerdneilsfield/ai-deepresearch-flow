from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli


def _project(tmp_path: Path, bibs: int = 1) -> Path:
    project = tmp_path / "demo"
    (project / "pdf").mkdir(parents=True)
    for i in range(bibs):
        (project / f"refs{i}.bib").write_text("", encoding="utf-8")
    return project


def _run(project: Path, *extra: str):
    return CliRunner().invoke(cli, ["utils", "init-run", str(project), "-m", "p/m", *extra])


def test_writes_executable_script_with_valid_bash_syntax(tmp_path: Path) -> None:
    project = _project(tmp_path)

    result = _run(project)

    assert result.exit_code == 0, result.output
    script = project / "run.sh"
    assert script.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text(encoding="utf-8")
    assert "refs0.bib" in text
    assert "@@" not in text


def test_values_with_spaces_are_quoted_in_valid_bash(tmp_path: Path) -> None:
    project = _project(tmp_path)

    result = _run(project, "--config", "my config.toml")

    assert result.exit_code == 0, result.output
    assert subprocess.run(["bash", "-n", str(project / "run.sh")]).returncode == 0
    assert "'" in (project / "run.sh").read_text(encoding="utf-8").split("CONFIG=")[1].split("\n")[0]


def test_requires_exactly_one_bib(tmp_path: Path) -> None:
    assert _run(_project(tmp_path / "a", bibs=0)).exit_code != 0
    assert _run(_project(tmp_path / "b", bibs=2)).exit_code != 0


def test_requires_pdf_directory(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "x.bib").write_text("", encoding="utf-8")

    assert _run(project).exit_code != 0
