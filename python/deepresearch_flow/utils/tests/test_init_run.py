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


def _set(script: Path, name: str, value: str) -> None:
    lines = script.read_text(encoding="utf-8").split("\n")
    lines = [f"{name}={value}" if line.startswith(f"{name}=") else line for line in lines]
    script.write_text("\n".join(lines), encoding="utf-8")


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


def test_list_prints_every_step(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)

    result = subprocess.run([str(script), "list"], capture_output=True, text=True)

    assert result.returncode == 0
    for name in ("ocr", "fix-math", "deep_read", "fix-mermaid-deep_read", "translate", "build"):
        assert name in result.stdout.split()


def test_named_steps_run_in_given_order_and_only_those(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)

    result = subprocess.run(
        [str(script), "fix-deep_read,fix-simple"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    calls = _calls(script.parent)
    assert len(calls) == 2
    assert "deep_read.json" in calls[0] and "simple.json" in calls[1]


def test_named_step_reruns_even_when_done(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    (script.parent / "logs").mkdir()
    (script.parent / "logs" / "fix-simple.done").touch()

    subprocess.run([str(script), "fix-simple"], capture_output=True, text=True)

    assert len(_calls(script.parent)) == 1


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


def test_exit_code_other_than_3_is_not_retried(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED + "; return 1")
    probe = script.parent / "probe.sh"
    probe.write_text(_helpers(script) + "\nretry drflow first\necho rc=$?\n", encoding="utf-8")

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert "rc=1" in result.stdout
    assert _calls(script.parent) == ["first"]


def test_each_task_takes_its_own_model(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    _set(script, "MODEL_EXTRACT_DEEP", "a/deep")
    _set(script, "MODEL_FIX_MATH", "b/math")
    _set(script, "MODEL_TRANSLATE", "c/tr")

    result = subprocess.run(
        [str(script), "deep_read,fix-math-simple,translate"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout
    deep, math, tr = _calls(script.parent)
    assert "--model a/deep" in deep
    assert "--model b/math" in math
    assert "--model c/tr" in tr


def test_empty_extract_model_omits_the_flag(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    _set(script, "MODEL_EXTRACT_SIMPLE", '""')

    subprocess.run([str(script), "simple"], capture_output=True, text=True)

    (call,) = _calls(script.parent)
    assert "--model" not in call


def test_model_with_spaces_is_passed_as_one_argument(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, 'printf "%s\\n" "$@" >> "$ROOT/calls.log"')
    _set(script, "MODEL_EXTRACT_SIMPLE", "'[{\"model\": \"a/b\", \"weight\": 1}]'")

    subprocess.run([str(script), "simple"], capture_output=True, text=True)

    assert '[{"model": "a/b", "weight": 1}]' in _calls(script.parent)


def test_required_model_empty_stops_with_a_message(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    _set(script, "MODEL_FIX_MATH", '""')

    result = subprocess.run([str(script), "fix-math-simple"], capture_output=True, text=True)

    assert result.returncode == 1
    assert "MODEL_FIX_MATH" in result.stdout
    assert not (script.parent / "calls.log").exists()
