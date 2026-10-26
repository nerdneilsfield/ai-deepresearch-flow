from __future__ import annotations

import os
import signal
import subprocess
import time
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
    assert len(calls) == 4  # 1 + ROUNDS(3)
    assert "--retry-failed" not in calls[0]
    assert all(c.endswith("--retry-failed") for c in calls[1:])


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

def test_commands_are_printed_with_plus_prefix(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    result = subprocess.run([str(script), "fix-math-simple"], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout
    plus = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert any("--model openai/gpt-4.1" in line for line in plus)
    assert any(line in (script.parent / "logs" / "fix-math-simple.log").read_text() for line in plus)


def test_notify_before_and_after_a_step(tmp_path: Path, monkeypatch) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )
    notify_log = script.parent / "notify.log"
    notify_log.write_text("", encoding="utf-8")
    monkeypatch.setenv("NOTIFY_URL", "http://example.invalid/x")

    probe = script.parent / "probe.sh"
    probe.write_text(
        _helpers(script) + "\ncurl() { echo \"$*\" >> \"" + str(notify_log) + "\"; }\n"
        + "drflow() { :; }\nIN_CHAIN=1 run_step fix-simple\n",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert result.returncode == 0, result.stderr
    sent = notify_log.read_text(encoding="utf-8").splitlines()
    assert any("fix-simple start" in line for line in sent)
    assert any("fix-simple done" in line for line in sent)


def test_retry_rerun_passes_no_extra_flag(tmp_path: Path) -> None:
    script = _script_with_stub(
        tmp_path,
        LOG_AND_SUCCEED + '; case "$*" in *--retry-failed*) return 0;; *) return 3;; esac',
    )
    probe = script.parent / "probe.sh"
    probe.write_text(_helpers(script) + "\nrerun drflow translator translate\n", encoding="utf-8")

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert result.returncode == 0, result.stderr
    calls = _calls(script.parent)
    assert len(calls) == 4  # 1 + ROUNDS(3)
    assert all("--retry-failed" not in c for c in calls)


def test_interrupt_stops_the_running_step_and_leaves_a_failed_file(
    tmp_path: Path,
) -> None:
    script = _script_with_stub(tmp_path, "sleep 30")
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    proc = subprocess.Popen([str(script), "fix-simple"])
    import time

    time.sleep(2)
    proc.send_signal(2)
    rc = proc.wait(timeout=30)

    assert rc == 130
    assert (script.parent / "logs" / "fix-simple.failed").exists()


def test_stop_then_stale_done_files_do_not_report_done(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )
    assert subprocess.run([str(script), "fix-simple"], capture_output=True).returncode == 0

    (script.parent / "logs" / "fix-simple.done").write_text("stale", encoding="utf-8")
    result = subprocess.run([str(script), "fix-simple"], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "[fix-simple] already done" not in result.stdout

def test_every_command_is_printed_with_a_plus_prefix(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    result = subprocess.run([str(script), "fix-math-simple"], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout
    printed = [line for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert printed, result.stdout
    assert any("--model openai/gpt-4.1" in line for line in printed)
    assert any("--report logs/fix-math-simple.json" in line for line in printed)


def test_notify_is_sent_before_and_after_a_step(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )
    probe = script.parent / "probe.sh"
    probe.write_text(
        _helpers(script)
        + "\nNOTIFY_URL=http://example.invalid/x\n"
        + "curl() { printf '%s\\n' \"$*\" >> \"$ROOT/notify.log\"; }\n"
        + "drflow() { :; }\n"
        + "run_step fix-simple\n",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert result.returncode == 0, result.stderr
    sent = (script.parent / "notify.log").read_text(encoding="utf-8")
    assert "fix-simple start" in sent
    assert "fix-simple done" in sent


def test_retry_uses_retry_failed_and_rerun_does_not(tmp_path: Path) -> None:
    script = _script_with_stub(
        tmp_path,
        LOG_AND_SUCCEED + '; case "$*" in *--retry-failed*) return 0;; *) return 3;; esac',
    )
    probe = script.parent / "probe.sh"
    probe.write_text(
        _helpers(script)
        + "\nretry drflow recognize fix-math\n"
        + "rerun drflow translator translate\n",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, cwd=script.parent)

    assert result.returncode == 0, result.stderr
    calls = _calls(script.parent)
    assert "recognize fix-math" in calls[0]
    assert calls[1].endswith("--retry-failed")
    # The stub succeeds once --retry-failed is present, so the retry mode stops
    # after one round. The rerun mode has no such flag.
    rerun_calls = [c for c in calls if "translator translate" in c]
    assert rerun_calls
    assert all("--retry-failed" not in c for c in rerun_calls)


def test_sigterm_stops_the_run_and_leaves_no_child(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, "sleep 300")
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    proc = subprocess.Popen([str(script), "fix-simple"], start_new_session=True)
    time.sleep(2)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=30) == 130

    assert (script.parent / "logs" / "fix-simple.failed").exists()
    children = subprocess.run(
        ["pgrep", "-P", str(proc.pid)], capture_output=True, text=True
    ).stdout.split()
    assert not children


def test_step_lock_rejects_a_second_runner(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, "sleep 300")
    first = subprocess.Popen([str(script), "fix-simple"], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        log = script.parent / "logs" / "fix-simple.log"
        while time.monotonic() < deadline and not log.exists():
            time.sleep(0.05)

        second = subprocess.run(
            [str(script), "fix-simple"], capture_output=True, text=True
        )
        assert second.returncode == 1
        assert "already running" in second.stderr
    finally:
        first.send_signal(signal.SIGTERM)
        assert first.wait(timeout=30) == 130


def test_knobs_are_editable_at_the_top_of_the_script(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert _run(project).exit_code == 0

    text = (project / "run.sh").read_text(encoding="utf-8")
    for knob in ("WORKERS", "BATCH_SIZE", "MAX_CONCURRENCY", "OCR_WORKERS"):
        assert f"{knob}=${{{knob}:-" in text, knob



def test_existing_run_sh_is_not_overwritten(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert _run(project).exit_code == 0
    script = project / "run.sh"
    script.write_text("my edits\n", encoding="utf-8")

    result = _run(project)

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert script.read_text(encoding="utf-8") == "my edits\n"

    assert _run(project, "--force").exit_code == 0
    assert script.read_text(encoding="utf-8") != "my edits\n"


def test_deep_read_uses_the_stage_dag(tmp_path: Path) -> None:
    script = _script_with_stub(tmp_path, LOG_AND_SUCCEED)
    script.write_text(
        script.read_text(encoding="utf-8").replace("PROVIDER/MODEL", "openai/gpt-4.1"),
        encoding="utf-8",
    )

    result = subprocess.run([str(script), "deep_read"], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout
    call = _calls(script.parent)[0]
    assert "--prompt-template deep_read" in call
    assert "--stage-dag" in call
