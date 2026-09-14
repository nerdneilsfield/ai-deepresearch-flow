"""Black-box checks for the data-root workflow CLI."""

from pathlib import Path
import json
import os
import shlex
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "process_data_root.py"
MODEL = "unconfigured/test-model"


def invoke(cwd, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def printed_commands(output):
    commands = []
    for line in output.splitlines():
        tokens = shlex.split(line)
        for index, token in enumerate(tokens):
            if token in {"recognize", "paper", "translator"}:
                commands.append(tokens[index:])
                break
    return commands


def option(command, *names):
    for name in names:
        if name in command:
            return command[command.index(name) + 1]
    pytest.fail(f"Missing option {names} in {command}")


@pytest.mark.parametrize("root_name", ["data", "data with spaces"])
def test_dry_run_prints_ordered_absolute_workflow_without_creating_files(tmp_path, root_name):
    root = tmp_path / root_name
    result = invoke(tmp_path, root_name, "--model", MODEL, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
    commands = printed_commands(result.stdout)
    assert [cmd[:2] for cmd in commands[:7]] == [
        ["recognize", "ocr"],
        ["recognize", "fix"],
        ["recognize", "fix-math"],
        ["recognize", "organize"],
        ["paper", "extract"],
        ["paper", "extract"],
        ["translator", "translate"],
    ]
    assert len(commands) == 14
    remaining = commands[7:]
    for name in ["simple.json", "deep_read.json", "md_base64_translated"]:
        target = str(root / name)
        fixes = [cmd for cmd in remaining if option(cmd, "--input", "-i") == target]
        assert {cmd[1] for cmd in fixes} == (
            {"fix", "fix-math", "fix-mermaid"} if name == "deep_read.json" else {"fix", "fix-math"}
        )
        assert [cmd[1] for cmd in fixes].index("fix") < [cmd[1] for cmd in fixes].index("fix-math")
        for command in fixes:
            assert "--in-place" in command
            if name.endswith(".json"):
                assert "--json" in command
            else:
                assert "--recursive" in command or "-r" in command
            if command[1] in {"fix-math", "fix-mermaid"}:
                assert option(command, "--model", "-m") == MODEL
                assert option(command, "--config", "-c") == str(tmp_path / "config.toml")
                assert Path(option(command, "--report")).is_relative_to(root / "logs")
    assert str(root / "pdf") in commands[0]
    assert option(commands[0], "--output-dir") == str(root / "ocr")
    assert option(commands[0], "--config") == str(tmp_path / "ocr.toml")
    for command in commands[1:4]:
        assert option(command, "--input", "-i") == str(root / "ocr")
        assert "--recursive" in command or "-r" in command
    for command in commands[1:3]:
        assert "--in-place" in command
    assert option(commands[3], "--output-simple") == str(root / "md_simple")
    assert option(commands[3], "--output-base64") == str(root / "md_base64")
    for command, template in zip(commands[4:6], ["simple", "deep_read"]):
        assert option(command, "--input", "-i") == str(root / "md_simple")
        assert option(command, "--prompt-template") == template
        assert option(command, "--output", "-o") == str(root / f"{template}.json")
        assert Path(option(command, "--errors", "-e")).is_relative_to(root / "logs")
    assert option(commands[6], "--input", "-i") == str(root / "md_base64")
    assert option(commands[6], "--output-dir") == str(root / "md_base64_translated")
    assert option(commands[6], "--target-lang") == "zh"
    assert Path(option(commands[6], "--debug-dir")).is_relative_to(root / "logs")
    assert Path(option(commands[2], "--report")).is_relative_to(root / "logs")
    for command in [commands[2], *commands[4:7]]:
        assert option(command, "--config", "-c") == str(tmp_path / "config.toml")
        assert option(command, "--model", "-m") == MODEL


def test_dry_run_resolves_explicit_configs_from_caller_directory(tmp_path):
    root = tmp_path / "elsewhere" / "papers"
    result = invoke(
        tmp_path,
        root,
        "--model",
        MODEL,
        "--dry-run",
        "--config",
        "settings/main config.toml",
        "--ocr-config",
        "settings/ocr config.toml",
        "--target-lang",
        "fr",
    )
    assert result.returncode == 0, result.stderr
    commands = printed_commands(result.stdout)
    assert len(commands) == 14
    assert option(commands[0], "--config") == str(tmp_path / "settings/ocr config.toml")
    for command in [commands[2], *commands[4:7]]:
        assert option(command, "--config", "-c") == str(tmp_path / "settings/main config.toml")
    assert option(commands[6], "--target-lang") == "fr"
    assert list(tmp_path.iterdir()) == []


def test_model_is_required_even_for_dry_run(tmp_path):
    result = invoke(tmp_path, "data", "--dry-run")
    assert result.returncode != 0
    assert "--model" in result.stderr + result.stdout
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("missing", ["pdf", "config.toml", "ocr.toml"])
def test_preflight_failure_records_error_under_root_logs(tmp_path, missing):
    root = tmp_path / "papers"
    root.mkdir()
    if missing != "pdf":
        (root / "pdf").mkdir()
        (root / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    for name in ["config.toml", "ocr.toml"]:
        if missing != name:
            (tmp_path / name).write_text("", encoding="utf-8")
    result = invoke(tmp_path, root, "--model", MODEL)
    assert result.returncode != 0
    logs = [path for path in (root / "logs").rglob("*") if path.is_file()]
    assert logs, "Preflight failures must produce a log file"
    log_text = "\n".join(path.read_text(encoding="utf-8") for path in logs)
    assert missing in log_text
    for name in [
        "ocr",
        "md_simple",
        "md_base64",
        "md_base64_translated",
        "simple.json",
        "deep_read.json",
    ]:
        assert not (root / name).exists()


@pytest.mark.parametrize("fail_at", [None, "paper extract"])
def test_execution_records_logs_and_stops_after_failure(tmp_path, monkeypatch, fail_at):
    package = tmp_path / "fake_cli" / "deepresearch_flow"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CALLS_FILE']).open('a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['paper', 'extract']:\n"
        "    Path('intermediate.json').write_text('{}')\n"
        "print('FAKE_STDOUT_MARKER', flush=True)\n"
        "print('FAKE_STDERR_MARKER', file=sys.stderr, flush=True)\n"
        "sys.exit(9 if ' '.join(sys.argv[1:3]) == os.environ.get('FAIL_AT') else 0)\n",
        encoding="utf-8",
    )
    calls_file = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    monkeypatch.setenv("CALLS_FILE", str(calls_file))
    if fail_at:
        monkeypatch.setenv("FAIL_AT", fail_at)
    else:
        monkeypatch.delenv("FAIL_AT", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mmdc = bin_dir / "mmdc"
    mmdc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mmdc.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    root = tmp_path / "papers"
    (root / "pdf").mkdir(parents=True)
    (root / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    for name in ["config.toml", "ocr.toml"]:
        (tmp_path / name).write_text("", encoding="utf-8")
    result = invoke(tmp_path, root, "--model", MODEL)
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    if fail_at:
        assert result.returncode != 0
        assert len(calls) == 5
        assert calls[-1][:2] == ["paper", "extract"]
    else:
        assert result.returncode == 0, result.stderr
        assert len(calls) == 14
    console = result.stdout + result.stderr
    assert str(root / "logs") in console
    assert "ocr" in console
    assert "simple" in console
    logs = [path for path in (root / "logs").rglob("*") if path.is_file()]
    assert logs
    assert list((root / "logs").rglob("intermediate.json"))
    assert not (tmp_path / "intermediate.json").exists()
    assert "FAKE_STDOUT_MARKER" in result.stdout
    assert "FAKE_STDERR_MARKER" in result.stderr


@pytest.mark.parametrize(
    "selection",
    [
        ("--steps", "translate,simple"),
        ("--from-step", "translate"),
    ],
)
def test_selected_commands_follow_workflow_order(tmp_path, selection):
    result = invoke(tmp_path, "data", "--model", MODEL, "--dry-run", *selection)
    assert result.returncode == 0, result.stderr
    commands = printed_commands(result.stdout)
    if selection[0] == "--steps":
        assert [cmd[:2] for cmd in commands] == [["paper", "extract"], ["translator", "translate"]]
        assert option(commands[0], "--prompt-template") == "simple"
    else:
        assert len(commands) == 8
        assert commands[0][:2] == ["translator", "translate"]
        assert commands[-1][:2] == ["recognize", "fix-mermaid"]
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "selection",
    [
        ("--steps", "unknown"),
        ("--steps", ""),
        ("--steps", "simple,"),
        ("--from-step", "unknown"),
        ("--steps", "simple", "--from-step", "translate"),
    ],
)
def test_invalid_selection_is_rejected(tmp_path, selection):
    result = invoke(tmp_path, "data", "--model", MODEL, "--dry-run", *selection)
    assert result.returncode != 0
    assert not (tmp_path / "data").exists()


def test_selected_fix_needs_no_pdf_configs_or_mermaid(tmp_path, monkeypatch):
    package = tmp_path / "fake_cli" / "deepresearch_flow"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text("print('SELECTED_FIX_OK')")
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    monkeypatch.setenv("PATH", "")
    root = tmp_path / "data"
    root.mkdir()
    (root / "simple.json").write_text("[]")
    result = invoke(tmp_path, root, "--model", MODEL, "--steps", "fix-simple")
    assert result.returncode == 0, result.stdout + result.stderr
    progress = json.loads((root / "logs" / "progress.json").read_text())
    assert progress["status"] == "completed"
    assert [step["name"] for step in progress["steps"]] == ["fix-simple"]
    assert "SELECTED_FIX_OK" in result.stdout
    assert not (root / "pdf").exists()


def test_selected_translation_reports_missing_markdown_not_pdf(tmp_path):
    result = invoke(tmp_path, "data", "--model", MODEL, "--steps", "translate")
    assert result.returncode != 0
    progress = json.loads((tmp_path / "data" / "logs" / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert "md_base64" in progress["error"]


@pytest.mark.parametrize("step", ["ocr", "organize", "fix"])
def test_non_model_steps_accept_no_model(tmp_path, step):
    result = invoke(tmp_path, "data", "--steps", step, "--dry-run")
    assert result.returncode == 0, result.stderr
    commands = printed_commands(result.stdout)
    assert len(commands) == 1
    assert "--model" not in commands[0]


def test_organize_runs_from_caller_directory_without_model(tmp_path, monkeypatch):
    package = tmp_path / "fake_cli" / "deepresearch_flow"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "from pathlib import Path\nprint(Path('caller-resource.txt').read_text())\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    (tmp_path / "caller-resource.txt").write_text("CALLER_RESOURCE_OK")
    root = tmp_path / "data"
    (root / "ocr").mkdir(parents=True)
    (root / "ocr" / "full.md").write_text("# Paper")
    result = invoke(tmp_path, root, "--steps", "organize")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CALLER_RESOURCE_OK" in result.stdout
