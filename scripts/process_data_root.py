#!/usr/bin/env python3
"""Run the PDF-to-research workflow. Use --dry-run to preview commands."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--ocr-config", type=Path, default=Path("ocr.toml"))
    parser.add_argument(
        "--model", required=True, help="LLM provider/model for repair and extraction"
    )
    parser.add_argument("--target-lang", default="zh")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print commands without writing files"
    )
    args = parser.parse_args()
    root = args.data_root.expanduser().resolve()
    config = args.config.expanduser().resolve()
    ocr_config = args.ocr_config.expanduser().resolve()
    logs = root / "logs"
    llm = ["--config", str(config), "--model", args.model]
    steps = [
        (
            "ocr",
            [
                "recognize",
                "ocr",
                str(root / "pdf"),
                "--config",
                str(ocr_config),
                "--output-dir",
                str(root / "ocr"),
            ],
        ),
        ("fix", ["recognize", "fix", "--input", str(root / "ocr"), "-r", "--in-place"]),
        (
            "fix-math",
            [
                "recognize",
                "fix-math",
                "--input",
                str(root / "ocr"),
                "-r",
                "--in-place",
                *llm,
                "--report",
                str(logs / "fix-math-errors.json"),
            ],
        ),
        (
            "organize",
            [
                "recognize",
                "organize",
                "--input",
                str(root / "ocr"),
                "-r",
                "--output-simple",
                str(root / "md_simple"),
                "--output-base64",
                str(root / "md_base64"),
            ],
        ),
    ]
    for template in ("simple", "deep_read"):
        steps.append(
            (
                template,
                [
                    "paper",
                    "extract",
                    "--input",
                    str(root / "md_simple"),
                    *llm,
                    "--prompt-template",
                    template,
                    "--output",
                    str(root / f"{template}.json"),
                    "--errors",
                    str(logs / f"{template}-errors.json"),
                ],
            )
        )
    steps.append(
        (
            "translate",
            [
                "translator",
                "translate",
                "--input",
                str(root / "md_base64"),
                *llm,
                "--target-lang",
                args.target_lang,
                "--fix-level",
                "moderate",
                "--output-dir",
                str(root / "md_base64_translated"),
                "--debug-dir",
                str(logs / "translator"),
            ],
        )
    )
    for name, source, mode in (
        ("simple", root / "simple.json", ["--json"]),
        ("deep_read", root / "deep_read.json", ["--json"]),
        ("translated", root / "md_base64_translated", ["-r"]),
    ):
        steps.append(
            (
                f"fix-{name}",
                [
                    "recognize",
                    "fix",
                    "--input",
                    str(source),
                    *mode,
                    "--in-place",
                ],
            )
        )
        steps.append(
            (
                f"fix-math-{name}",
                [
                    "recognize",
                    "fix-math",
                    "--input",
                    str(source),
                    *mode,
                    "--in-place",
                    *llm,
                    "--report",
                    str(logs / f"fix-math-{name}-errors.json"),
                ],
            )
        )
    steps.append(
        (
            "fix-mermaid-deep_read",
            [
                "recognize",
                "fix-mermaid",
                "--input",
                str(root / "deep_read.json"),
                "--json",
                "--in-place",
                *llm,
                "--report",
                str(logs / "fix-mermaid-deep_read-errors.json"),
            ],
        )
    )
    # Keep the same environment as `uv run python`, even after changing cwd.
    prefix = [sys.executable, "-m", "deepresearch_flow.cli"]
    if args.dry_run:
        for name, command in steps:
            print(f"# {name} (cwd: {logs / name})")
            print(shlex.join(prefix + command))
        return 0

    logs.mkdir(parents=True, exist_ok=True)
    progress_path = logs / "progress.json"
    progress = {"data_root": str(root), "status": "running", "steps": []}

    def record() -> None:
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = progress_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(progress_path)

    def announce(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (logs / "pipeline.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    record()
    try:
        if not (root / "pdf").is_dir() or not any(
            p.is_file() and p.suffix.lower() == ".pdf" for p in (root / "pdf").rglob("*")
        ):
            raise ValueError(f"No PDF files found in {root / 'pdf'}")
        for path in (config, ocr_config):
            if not path.is_file():
                raise ValueError(f"Config file not found: {path}")
        # Mermaid validation also needs the repository-local npm executable after chdir.
        node_bin = Path(__file__).resolve().parents[1] / "node_modules" / ".bin"
        command_path = str(node_bin) + os.pathsep + os.environ.get("PATH", "")
        if not shutil.which("mmdc", path=command_path):
            raise ValueError("mmdc not found; run npm install in the repository first")
        for directory in ("ocr", "md_simple", "md_base64", "md_base64_translated"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb"}
        env["PATH"] = command_path
        for index, (name, command) in enumerate(steps, 1):
            workdir = logs / name
            workdir.mkdir(parents=True, exist_ok=True)
            log_path = logs / f"{index:02d}-{name}.log"
            entry = {"name": name, "status": "running", "log": str(log_path)}
            progress["steps"].append(entry)
            record()
            announce(f"[{index}/{len(steps)}] {name}: {log_path}")
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write("\n$ " + shlex.join(prefix + command) + "\n")
                stream.flush()
                result = subprocess.run(
                    prefix + command,
                    cwd=workdir,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            entry["returncode"] = result.returncode
            if result.returncode:
                entry["status"] = "failed"
                raise RuntimeError(f"{name} exited with {result.returncode}; see {log_path}")
            entry["status"] = "completed"
            record()
        progress["status"] = "completed"
        announce("All commands finished. Check step logs and error reports for per-file failures.")
        record()
        return 0
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        progress["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        progress["error"] = str(exc) or "Interrupted"
        if progress["steps"] and progress["steps"][-1]["status"] == "running":
            progress["steps"][-1]["status"] = progress["status"]
        announce(f"ERROR: {progress['error']}")
        record()
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
