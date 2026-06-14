#!/usr/bin/env python3
"""Run all dependency-free bounded formal model checkers."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CHECKERS = (
    "check_oauth_client_cache_model.py",
    "check_provider_routing_model.py",
    "check_semantic_ingest_model.py",
    "check_translation_scheduler_model.py",
)


def run_checker(path: Path, *, inject_bug: bool) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(path)]
    if inject_bug:
        cmd.append("--inject-bug")
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inject-bug",
        action="store_true",
        help="run every checker in bug-injection mode; exits nonzero after printing traces",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    failures = 0
    unexpected_passes = 0
    for checker in CHECKERS:
        result = run_checker(root / checker, inject_bug=args.inject_bug)
        print(f"== {checker} ==")
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if args.inject_bug:
            if result.returncode == 0:
                unexpected_passes += 1
                print(f"MODEL_PROOF check_all_models FAIL: {checker} did not expose injected bug")
        elif result.returncode != 0:
            failures += 1

    if args.inject_bug:
        if unexpected_passes:
            return 2
        print("MODEL_PROOF check_all_models FAIL: injected bugs produced counterexample traces")
        return 1
    if failures:
        print(f"MODEL_PROOF check_all_models FAIL: {failures} checker(s) failed")
        return 1
    print(f"MODEL_PROOF check_all_models PASS checkers={len(CHECKERS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
