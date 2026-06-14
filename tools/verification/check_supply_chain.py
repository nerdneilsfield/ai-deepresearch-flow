#!/usr/bin/env python3
"""Local-only supply-chain gate.

Runs checks that can be performed without network access. Missing or networked
vulnerability scanners are reported as explicit gaps rather than silently
ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_SCRIPT_RE = re.compile(r"(?i)(curl|wget)\b.*\|\s*(sh|bash|zsh|python)")
UNSAFE_SPEC_RE = re.compile(r"(?i)(?:^|\s)(?:git\+|https?://|file:|path:)")
PINNED_REQ_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^\]]+\])?==[^\s;]+(?:\s*;.*)?$")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def run_local(command: list[str], cwd: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": " ".join(command),
            "cwd": rel(cwd) if cwd != REPO_ROOT else ".",
            "status": "gap",
            "message": str(exc),
        }
    return {
        "command": " ".join(command),
        "cwd": rel(cwd) if cwd != REPO_ROOT else ".",
        "status": "pass" if proc.returncode == 0 else "fail",
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def check_requirements(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not path.exists():
        return findings
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement")):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "line": idx,
                    "message": "nested requirements are not checked by this gate",
                }
            )
            continue
        if UNSAFE_SPEC_RE.search(line):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "line": idx,
                    "message": "direct URL/path dependency spec is not allowed in locked requirements",
                }
            )
        if not PINNED_REQ_RE.match(line):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "line": idx,
                    "message": "requirement is not pinned with ==",
                }
            )
    return findings


def check_package_json(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not path.exists():
        return findings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [
            {
                "severity": "fail",
                "surface": rel(path),
                "message": "package.json is invalid JSON",
                "detail": str(exc),
            }
        ]
    for name, script in data.get("scripts", {}).items():
        if isinstance(script, str) and REMOTE_SCRIPT_RE.search(script):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "script": name,
                    "message": "script pipes remote content into an interpreter",
                }
            )
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for dep, spec in deps.items():
            if isinstance(spec, str) and UNSAFE_SPEC_RE.search(spec):
                findings.append(
                    {
                        "severity": "fail",
                        "surface": rel(path),
                        "dependency": dep,
                        "message": "direct URL/path dependency spec is not allowed",
                    }
                )
    return findings


def check_package_lock(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not path.exists():
        return findings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [
            {
                "severity": "fail",
                "surface": rel(path),
                "message": "package-lock.json is invalid JSON",
                "detail": str(exc),
            }
        ]
    if not data.get("lockfileVersion"):
        findings.append(
            {"severity": "fail", "surface": rel(path), "message": "lockfileVersion is missing"}
        )
    for pkg_path, meta in (data.get("packages") or {}).items():
        if not isinstance(meta, dict) or pkg_path == "":
            continue
        resolved = str(meta.get("resolved", ""))
        integrity = meta.get("integrity")
        if resolved.startswith("http://"):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "package": pkg_path,
                    "message": "package resolves over plain HTTP",
                }
            )
        if resolved.startswith("https://registry.npmjs.org/") and not integrity:
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(path),
                    "package": pkg_path,
                    "message": "registry package is missing integrity hash",
                }
            )
    return findings


def collect() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    command_checks: list[dict[str, Any]] = []

    for path in [REPO_ROOT / "requirements.txt"]:
        if path.exists():
            findings.extend(check_requirements(path))
        else:
            gaps.append({"surface": rel(path), "message": "requirements lock surface absent"})

    for path in [REPO_ROOT / "package.json", REPO_ROOT / "frontend" / "package.json"]:
        if path.exists():
            findings.extend(check_package_json(path))
        else:
            gaps.append({"surface": rel(path), "message": "npm manifest absent"})

    for path in [REPO_ROOT / "package-lock.json", REPO_ROOT / "frontend" / "package-lock.json"]:
        if path.exists():
            findings.extend(check_package_lock(path))
        else:
            gaps.append({"surface": rel(path), "message": "npm lockfile absent"})

    uv = shutil.which("uv")
    if uv and (REPO_ROOT / "uv.lock").exists():
        command_checks.append(run_local([uv, "lock", "--check"], REPO_ROOT))
    else:
        gaps.append(
            {
                "surface": "uv lock --check",
                "message": "uv or uv.lock absent; Python lock consistency not executed",
            }
        )

    npm = shutil.which("npm")
    for cwd in [REPO_ROOT, REPO_ROOT / "frontend"]:
        if npm and (cwd / "package-lock.json").exists():
            command_checks.append(
                run_local([npm, "ls", "--package-lock-only", "--all", "--json"], cwd)
            )
        else:
            gaps.append(
                {
                    "surface": f"{rel(cwd) if cwd != REPO_ROOT else '.'}/package-lock.json",
                    "message": "npm or package-lock absent; npm lock consistency not executed",
                }
            )

    for scanner in ["pip-audit", "osv-scanner", "safety"]:
        if shutil.which(scanner):
            gaps.append(
                {
                    "surface": scanner,
                    "message": "scanner is installed but skipped because this gate is network-disabled and no local vulnerability database was configured",
                }
            )
        else:
            gaps.append(
                {"surface": scanner, "message": "optional vulnerability scanner is not installed"}
            )

    for result in command_checks:
        if result["status"] == "fail":
            findings.append(
                {
                    "severity": "fail",
                    "surface": result["command"],
                    "message": "local supply-chain command failed",
                    "exit_code": result.get("exit_code"),
                    "stderr_tail": result.get("stderr_tail", ""),
                }
            )

    return {
        "tool": "check_supply_chain",
        "network": "disabled",
        "status": "fail" if findings else "pass",
        "findings": findings,
        "gaps": gaps,
        "command_checks": command_checks,
    }


def emit(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"supply-chain gate: {report['status']}")
    for finding in report["findings"]:
        print(f"FAIL {finding['surface']}: {finding['message']}")
    for check in report["command_checks"]:
        print(f"CHECK {check['status']} [{check['cwd']}]: {check['command']}")
    for gap in report["gaps"]:
        print(f"GAP {gap['surface']}: {gap['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    report = collect()
    emit(report, as_json=args.json)
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
