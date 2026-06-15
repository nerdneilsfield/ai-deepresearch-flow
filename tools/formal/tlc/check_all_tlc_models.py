#!/usr/bin/env python3
"""Run local TLC exhaustive reachable-state checks for core TLA+ models.

This target is intentionally local-only. It requires a TLA+ tools jar pointed to
by TLA_TOOLS_JAR or located at .cache/formal/tla2tools.jar.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_JAR = REPO_ROOT / ".cache" / "formal" / "tla2tools.jar"
MODELS = (
    ("oauth_client_cache", REPO_ROOT / "formal" / "oauth_client_cache", "OAuthClientCache"),
    ("provider_routing", REPO_ROOT / "formal" / "provider_routing", "ProviderRouting"),
    ("semantic_ingest", REPO_ROOT / "formal" / "semantic_ingest", "SemanticIngest"),
    (
        "translation_scheduler",
        REPO_ROOT / "formal" / "translation_scheduler",
        "TranslationScheduler",
    ),
)
STATE_RE = re.compile(
    r"(\d+) states generated, (\d+) distinct states found, (\d+) states left on queue\."
)
DEPTH_RE = re.compile(r"The depth of the complete state graph search is (\d+)\.")
VERSION_RE = re.compile(r"TLC2 Version ([^\n]+)")


@dataclass(frozen=True)
class TlcModel:
    name: str
    directory: Path
    module: str


def resolve_jar() -> Path:
    configured = os.environ.get("TLA_TOOLS_JAR")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_JAR


def parse_tlc_output(model: TlcModel, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    output = result.stdout + result.stderr
    state_match = STATE_RE.search(output)
    depth_match = DEPTH_RE.search(output)
    version_match = VERSION_RE.search(output)
    no_error = "Model checking completed. No error has been found." in output
    complete_state_graph = False
    if state_match:
        complete_state_graph = int(state_match.group(3)) == 0
    status = "pass" if result.returncode == 0 and no_error and complete_state_graph else "fail"
    payload: dict[str, Any] = {
        "model": model.name,
        "module": model.module,
        "status": status,
        "returncode": result.returncode,
        "tlc_version": version_match.group(1).strip() if version_match else None,
        "complete_state_graph": False,
        "states_generated": 0,
        "distinct_states": 0,
        "states_left_on_queue": None,
        "depth": None,
    }
    if state_match:
        generated, distinct, left = (int(part) for part in state_match.groups())
        payload.update(
            {
                "states_generated": generated,
                "distinct_states": distinct,
                "states_left_on_queue": left,
                "complete_state_graph": no_error and left == 0,
            }
        )
    if depth_match:
        payload["depth"] = int(depth_match.group(1))
    if status != "pass":
        payload["output_tail"] = "\n".join(output.splitlines()[-80:])
    return payload


def run_model(model: TlcModel, jar: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "java",
            "-XX:+UseParallelGC",
            "-cp",
            str(jar),
            "tlc2.TLC",
            "-workers",
            "1",
            "-deadlock",
            model.module,
        ],
        cwd=model.directory,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return parse_tlc_output(model, result)


def run_all(jar: Path) -> dict[str, Any]:
    if not jar.exists():
        return {
            "tool": "tlc",
            "status": "fail",
            "jar": str(jar),
            "error": "missing_tla_tools_jar",
            "message": "Install TLC locally, e.g. curl -L https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar -o .cache/formal/tla2tools.jar",
            "models": [],
        }
    models = [run_model(TlcModel(*item), jar) for item in MODELS]
    status = "pass" if all(model["status"] == "pass" for model in models) else "fail"
    version = next((model.get("tlc_version") for model in models if model.get("tlc_version")), None)
    return {
        "tool": "tlc",
        "status": status,
        "jar": str(jar),
        "tlc_version": version,
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = run_all(resolve_jar())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if payload.get("error"):
            print(f"TLC_MODEL FAIL {payload['error']}: {payload['message']}")
        for model in payload.get("models", []):
            print(
                "TLC_MODEL {model} {status} distinct={distinct} generated={generated} left={left} depth={depth}".format(
                    model=model["model"],
                    status=model["status"].upper(),
                    distinct=model["distinct_states"],
                    generated=model["states_generated"],
                    left=model["states_left_on_queue"],
                    depth=model["depth"],
                )
            )
            if model["status"] != "pass":
                print(model.get("output_tail", ""))
        print(f"TLC_MODEL check_all_tlc_models {payload['status'].upper()}")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
