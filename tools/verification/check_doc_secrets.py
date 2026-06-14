#!/usr/bin/env python3
"""Scan documentation/example/deploy surfaces for committed secrets.

The scanner is intentionally local-only and conservative. It allows documented
placeholders such as example.com, localhost, YOUR_TOKEN, your-token, and env:*.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS = [
    "docs",
    "README.md",
    "README_ZH.md",
    "config.example.toml",
    "ocr.example.toml",
    "remote.example.toml",
    "scripts/docker",
    ".github/workflows",
]
TEXT_SUFFIXES = {
    ".md",
    ".mdx",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".sh",
    ".env",
    ".example",
    ".template",
    ".conf",
    ".ini",
    ".cfg",
    ".dockerfile",
}
SECRET_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|secret)"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?P<name>[A-Z0-9_.-]*(?:ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|BEARER[_-]?TOKEN|API[_-]?KEY|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|PASSWORD|PASSWD|SECRET)[A-Z0-9_.-]*)\b\s*[:=]\s*(?P<quote>['\"]?)(?P<value>[^'\"\s#]+)"
)
KEYLIKE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|[A-Za-z0-9+/=._-]{40,})\b"
)
URL_HOST_RE = re.compile(r"(?i)\b(?:https?|postgres|mysql|redis|mongodb)://([^/\s:@]+)")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HOST_RE = re.compile(r"\b([A-Za-z0-9-]+\.(?:internal|local|lan|corp|home))\b", re.I)

ALLOW_VALUE_RE = re.compile(
    r"(?ix)^(?:"
    r"YOUR[_-]?[A-Z0-9_-]*TOKEN|your[-_a-z0-9]*token|"
    r"YOUR[_-]?[A-Z0-9_-]*KEY|your[-_a-z0-9]*key|"
    r"YOUR[_-]?[A-Z0-9_-]*SECRET|your[-_a-z0-9]*secret|"
    r"changeme|change-me|placeholder|example|dummy|test|"
    r"env:[A-Z_][A-Z0-9_]*|\$\{?[A-Z_][A-Z0-9_]*\}?|"
    r"<[^>]+>|\*+|x+|0+"
    r")$"
)


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def is_text_candidate(path: Path) -> bool:
    if path.name.startswith(".") and path.suffix == "":
        return True
    if path.name in {"Dockerfile", "Makefile"}:
        return True
    if path.name.startswith("Dockerfile"):
        return True
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return False


def tracked_files() -> set[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, text=True, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line for line in out.splitlines() if line}


def iter_target_files(paths: Iterable[str]) -> list[Path]:
    tracked = tracked_files()
    files: set[Path] = set()
    for raw in paths:
        base = (REPO_ROOT / raw).resolve()
        if not str(base).startswith(str(REPO_ROOT)):
            continue
        if base.is_file():
            if is_text_candidate(base):
                files.add(base)
            continue
        if not base.exists():
            continue
        for candidate in base.rglob("*"):
            if not candidate.is_file() or not is_text_candidate(candidate):
                continue
            r = rel(candidate)
            if tracked and r not in tracked:
                continue
            files.add(candidate)
    return sorted(files)


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    return -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())


def allowed_value(value: str, line: str) -> bool:
    stripped = value.strip().strip("'\"`,.;)")
    lowered = stripped.lower()
    if "example.com" in lowered or "localhost" in lowered:
        return True
    if ALLOW_VALUE_RE.match(stripped):
        return True
    if stripped.startswith(("${", "$", "$(")):
        return True
    if "env:" in line.lower() or "example" in line.lower() and "token" in lowered:
        return True
    return False


def private_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False
    if ip.is_loopback or str(ip) == "0.0.0.0":
        return False
    return ip.is_private


def is_documented_cidr(line: str, match: re.Match[str]) -> bool:
    """Return whether a private IP literal is part of a documented CIDR range."""
    del line
    return match.end() < len(match.string) and match.string[match.end()] == "/"


def is_repo_path_host_literal(line: str, match: re.Match[str]) -> bool:
    """Return whether a .local-like token is embedded in a repository filename."""
    host = match.group(1)
    prefix = line[: match.start(1)]
    suffix = line[match.end(1) :]
    if "://" in prefix[-12:]:
        return False
    if suffix.startswith("."):
        return True
    return f"/{host}" in line or f"{host}/" in line


def scan_file(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings

    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")) and "token" not in stripped.lower():
            continue

        for match in ASSIGNMENT_RE.finditer(line):
            value = match.group("value").strip().strip(",")
            common_token = value.startswith("sk-") or re.match(r"gh[pousr]_", value)
            entropy_token = (
                len(value) >= 24
                and shannon_entropy(value) >= 4.2
                and any(c.islower() for c in value)
                and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value)
                and not any(ch in value for ch in "()[]{}")
            )
            if not allowed_value(value, line) and (common_token or entropy_token):
                findings.append(
                    {
                        "kind": "literal-secret-assignment",
                        "path": rel(path),
                        "line": line_no,
                        "name": match.group("name"),
                        "redacted": value[:4] + "…" + value[-4:],
                    }
                )

        for match in KEYLIKE_RE.finditer(line):
            value = match.group(0).strip(".,;)")
            if allowed_value(value, line):
                continue
            common_token = value.startswith("sk-") or re.match(r"gh[pousr]_", value)
            entropy_token = (
                shannon_entropy(value) >= 4.5
                and any(c.islower() for c in value)
                and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value)
                and not any(sep in value for sep in ("/", "\\"))
            )
            if common_token or (SECRET_NAME_RE.search(line) and entropy_token):
                findings.append(
                    {
                        "kind": "high-entropy-key-like-value",
                        "path": rel(path),
                        "line": line_no,
                        "redacted": value[:4] + "…" + value[-4:],
                        "entropy": round(shannon_entropy(value), 2),
                    }
                )

        for host_match in URL_HOST_RE.finditer(line):
            host = host_match.group(1).strip("[]")
            if host in {"localhost", "example.com"}:
                continue
            if private_ip(host):
                findings.append(
                    {"kind": "private-ip-url", "path": rel(path), "line": line_no, "host": host}
                )
        for ip_match in IP_RE.finditer(line):
            value = ip_match.group(0)
            if is_documented_cidr(line, ip_match):
                continue
            if private_ip(value):
                findings.append(
                    {
                        "kind": "private-ip-literal",
                        "path": rel(path),
                        "line": line_no,
                        "host": value,
                    }
                )
        for host_match in HOST_RE.finditer(line):
            host = host_match.group(1)
            if host.lower() == "localhost":
                continue
            if is_repo_path_host_literal(line, host_match):
                continue
            findings.append(
                {"kind": "private-host-literal", "path": rel(path), "line": line_no, "host": host}
            )
    return findings


def emit(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"doc secret gate: {report['status']}")
    for finding in report["findings"]:
        location = f"{finding['path']}:{finding['line']}"
        print(f"FAIL {location}: {finding['kind']}")
    for gap in report["gaps"]:
        print(f"GAP {gap['surface']}: {gap['message']}")
    print(f"scanned files: {report['scanned_file_count']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("paths", nargs="*", help="override default scan paths")
    args = parser.parse_args(argv)

    paths = args.paths or DEFAULT_TARGETS
    files = iter_target_files(paths)
    gaps = [
        {"surface": path, "message": "configured scan target is absent"}
        for path in paths
        if not (REPO_ROOT / path).exists()
    ]
    findings: list[dict[str, Any]] = []
    for path in files:
        findings.extend(scan_file(path))
    report = {
        "tool": "check_doc_secrets",
        "network": "disabled",
        "status": "fail" if findings else "pass",
        "scan_targets": paths,
        "scanned_file_count": len(files),
        "findings": findings,
        "gaps": gaps,
        "allowlist": ["example.com", "localhost", "YOUR_TOKEN", "your-token", "env:*"],
    }
    emit(report, as_json=args.json)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
