from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "formal" / "smt" / "check_all_smt_models.py"
EXPECTED_MODELS = {
    "oauth_client_cache",
    "provider_routing",
    "semantic_ingest",
    "translation_scheduler",
}

pytestmark = pytest.mark.skipif(
    os.environ.get("DRFLOW_RUN_LOCAL_FORMAL") != "1",
    reason="local SMT formal gate is intentionally excluded from default pytest/CI",
)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_smt_gate_exhaustively_checks_all_core_state_machines() -> None:
    result = _run(["--json"])

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["solver"] == "z3"
    assert payload["status"] == "pass"
    assert {model["model"] for model in payload["models"]} == EXPECTED_MODELS
    for model in payload["models"]:
        assert model["status"] == "pass"
        assert model["unmodeled_faults"] == []
        assert (
            model["state_space"]["universe_states"] >= model["state_space"]["reachable_states"] > 0
        )
        assert model["state_space"]["transitions_checked"] > 0
        assert model["queries"]["reachable_invariant_violation"] == "unsat"


def test_smt_gate_reports_counterexamples_for_injected_bugs() -> None:
    result = _run(["--inject-bug", "--json"])

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["solver"] == "z3"
    assert payload["status"] == "fail"
    failing = [model for model in payload["models"] if model["status"] == "fail"]
    assert {model["model"] for model in failing} == EXPECTED_MODELS
    for model in failing:
        assert model["counterexample"]


def test_oauth_smt_model_exposes_reauth_recovery_contract() -> None:
    result = _run(["--json"])

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    oauth = next(model for model in payload["models"] if model["model"] == "oauth_client_cache")

    assert "missing_recoverable" in oauth["state_space"]["variables"]
    assert "missing_malformed" in oauth["state_space"]["variables"]
    assert "reauth_pending" in oauth["state_space"]["variables"]
    assert "github_auth_ok" in oauth["state_space"]["variables"]
    assert "recover_missing_client_for_reauth" in oauth["state_space"]["action_names"]
    assert "token_after_reauth" in oauth["state_space"]["action_names"]
    assert oauth["queries"]["reachable_invariant_violation"] == "unsat"
    assert oauth["queries"]["universe_recovery_violation"] == "unsat"
