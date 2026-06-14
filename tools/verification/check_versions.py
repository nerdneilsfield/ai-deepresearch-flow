#!/usr/bin/env python3
"""Local release-version consistency gate.

No network access is used. The command exits non-zero when checked version
surfaces disagree, and prints explicit gaps for optional/missing surfaces.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def python_version_satisfies(version: str, requirement: str) -> bool:
    """Evaluate the simple >=X.Y style used by this repository."""

    def parts(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", text)[:3])

    current = parts(version)
    if not current:
        return False
    for clause in requirement.split(","):
        clause = clause.strip()
        if clause.startswith(">=") and current < parts(clause):
            return False
        if clause.startswith(">") and current <= parts(clause):
            return False
        if clause.startswith("<=") and current > parts(clause):
            return False
        if clause.startswith("<") and current >= parts(clause):
            return False
        if clause.startswith("==") and current != parts(clause):
            return False
    return True


def load_pyproject() -> dict[str, Any]:
    path = REPO_ROOT / "pyproject.toml"
    with path.open("rb") as fh:
        return tomllib.load(fh)


def collect_versions() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    pyproject_path = REPO_ROOT / "pyproject.toml"
    if pyproject_path.exists():
        data = load_pyproject()
        project = data.get("project", {})
        version = project.get("version")
        checks.append(
            {"surface": rel(pyproject_path), "field": "project.version", "value": version}
        )
        if not isinstance(version, str) or not SEMVER_RE.match(version):
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(pyproject_path),
                    "message": "project.version is missing or is not semver-like",
                    "value": version,
                }
            )

        requires_python = project.get("requires-python")
        checks.append(
            {
                "surface": rel(pyproject_path),
                "field": "project.requires-python",
                "value": requires_python,
            }
        )
        python_version_file = REPO_ROOT / ".python-version"
        if python_version_file.exists():
            requested = read_text(python_version_file).strip()
            checks.append(
                {"surface": rel(python_version_file), "field": "python-version", "value": requested}
            )
            if (
                requires_python
                and requested
                and not python_version_satisfies(requested, str(requires_python))
            ):
                findings.append(
                    {
                        "severity": "fail",
                        "surface": rel(python_version_file),
                        "message": ".python-version does not satisfy project.requires-python",
                        "value": requested,
                        "expected_context": requires_python,
                    }
                )
        else:
            gaps.append(
                {
                    "surface": ".python-version",
                    "message": "optional Python runtime pin file is absent",
                }
            )
    else:
        findings.append(
            {"severity": "fail", "surface": "pyproject.toml", "message": "pyproject.toml missing"}
        )
        return checks, findings, gaps

    canonical = next(
        (
            c["value"]
            for c in checks
            if c["surface"] == "pyproject.toml" and c["field"] == "project.version"
        ),
        None,
    )

    init_path = REPO_ROOT / "python" / "deepresearch_flow" / "__init__.py"
    if init_path.exists():
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", read_text(init_path), re.M)
        value = match.group(1) if match else None
        checks.append({"surface": rel(init_path), "field": "__version__", "value": value})
        if value != canonical:
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(init_path),
                    "message": "package __version__ differs from pyproject project.version",
                    "value": value,
                    "expected": canonical,
                }
            )
    else:
        gaps.append(
            {
                "surface": rel(init_path),
                "message": "optional package __version__ surface is absent",
            }
        )

    for pkg in [REPO_ROOT / "package.json", REPO_ROOT / "frontend" / "package.json"]:
        if not pkg.exists():
            gaps.append({"surface": rel(pkg), "message": "optional npm package file is absent"})
            continue
        try:
            data = json.loads(read_text(pkg))
        except json.JSONDecodeError as exc:
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(pkg),
                    "message": "package.json is not valid JSON",
                    "detail": str(exc),
                }
            )
            continue
        value = data.get("version")
        checks.append({"surface": rel(pkg), "field": "version", "value": value})
        if value != canonical:
            findings.append(
                {
                    "severity": "fail",
                    "surface": rel(pkg),
                    "message": "npm package version differs from pyproject project.version",
                    "value": value,
                    "expected": canonical,
                }
            )

    return checks, findings, gaps


def emit(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"version gate: {report['status']}")
    for finding in report["findings"]:
        print(f"FAIL {finding['surface']}: {finding['message']}")
    for gap in report["gaps"]:
        print(f"GAP {gap['surface']}: {gap['message']}")
    print(f"checked surfaces: {len(report['checks'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    checks, findings, gaps = collect_versions()
    report = {
        "tool": "check_versions",
        "network": "disabled",
        "status": "fail" if findings else "pass",
        "checks": checks,
        "findings": findings,
        "gaps": gaps,
    }
    emit(report, as_json=args.json)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
