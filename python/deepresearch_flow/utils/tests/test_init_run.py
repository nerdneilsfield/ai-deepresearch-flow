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
    return CliRunner().invoke(cli, ["utils", "init-run", str(project), *extra])


def test_requires_exactly_one_bib(tmp_path: Path) -> None:
    assert _run(_project(tmp_path / "a", bibs=0)).exit_code != 0
    assert _run(_project(tmp_path / "b", bibs=2)).exit_code != 0


def test_requires_pdf_directory(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "x.bib").write_text("", encoding="utf-8")

    assert _run(project).exit_code != 0


def _script_with_stub(tmp_path: Path, stub_body: str) -> Path:
    """Generated script whose `drflow` is replaced by a stub that logs its arguments."""
    project = _project(tmp_path)
    assert _run(project).exit_code == 0
    script = project / "run.sh"
    text = script.read_text(encoding="utf-8")
    start = text.index("drflow() {")
    end = text.index("\n", start)
    script.write_text(
        text[:start] + "drflow() { " + stub_body + "; }" + text[end:], encoding="utf-8"
    )
    return script


def _calls(project: Path) -> list[str]:
    return (project / "calls.log").read_text(encoding="utf-8").splitlines()


LOG_AND_SUCCEED = 'echo "$*" >> "$ROOT/calls.log"'


def test_unknown_step_is_rejected_before_anything_runs(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)

    result = subprocess.run([str(script), "fix-simple,bogus"], capture_output=True, text=True)

    assert result.returncode == 1
    assert "unknown step: bogus" in result.stdout
    assert not (script.parent / "calls.log").exists()


def test_named_steps_run_in_given_order_and_only_those(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)

    result = subprocess.run(
        [str(script), "fix-deep_read,fix-simple"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    calls = _calls(script.parent)
    assert len(calls) == 2
    assert "deep_read.json" in calls[0] and "simple.json" in calls[1]


def test_failing_step_stops_the_rest_of_the_list(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED + "; return 1")

    result = subprocess.run(
        [str(script), "fix-simple,fix-deep_read"], capture_output=True, text=True
    )

    assert result.returncode == 1
    assert len(_calls(script.parent)) == 1
    assert (script.parent / "logs" / "fix-simple.failed").exists()


def _helpers(script: Path) -> str:
    return script.read_text(encoding="utf-8").split("# Entry point used inside")[0]


def test_retry_repeats_with_retry_failed_after_exit_3(tmp_path: Path) -> None:
    script = _script_with_stub(
        tmp_path,
        LOG_AND_SUCCEED + '; case "$*" in *--retry-failed*) return 0;; *) return 3;; esac',
    )
    probe = script.parent / "probe.sh"
    probe.write_text(_helpers(script) + "\nretry drflow recognize fix-math\n", encoding="utf-8")

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert result.returncode == 0, result.stderr
    calls = _calls(script.parent)
    assert len(calls) == 2
    assert "--retry-failed" not in calls[0] and calls[1].endswith("--retry-failed")


def test_unreplaced_model_placeholder_stops_before_running(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)

    result = subprocess.run([str(script), "fix-math-simple"], capture_output=True, text=True)

    assert result.returncode == 1
    assert "fix-math-simple" in result.stdout and "placeholder" in result.stdout
    assert not (script.parent / "calls.log").exists()


def test_replaced_placeholder_runs(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    result = subprocess.run([str(script), "fix-math-simple"], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout
    assert "--model openai/gpt-4.1" in _calls(script.parent)[0]
