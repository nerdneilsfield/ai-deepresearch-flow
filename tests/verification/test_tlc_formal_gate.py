from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "formal" / "tlc" / "check_all_tlc_models.py"
EXPECTED_MODELS = {
    "oauth_client_cache",
    "provider_routing",
    "semantic_ingest",
    "translation_scheduler",
}

_SPEC = spec_from_file_location("check_all_tlc_models", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_TLC_MODULE = module_from_spec(_SPEC)
sys.modules["check_all_tlc_models"] = _TLC_MODULE
_SPEC.loader.exec_module(_TLC_MODULE)


@pytest.mark.skipif(
    os.environ.get("DRFLOW_RUN_LOCAL_FORMAL") != "1",
    reason="local formal TLC gate is intentionally excluded from default pytest/CI",
)
def test_tlc_exhaustively_checks_reachable_states_for_core_state_machines() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["tool"] == "tlc"
    assert payload["status"] == "pass"
    assert {model["model"] for model in payload["models"]} == EXPECTED_MODELS
    for model in payload["models"]:
        assert model["status"] == "pass"
        assert model["distinct_states"] > 0
        assert model["states_left_on_queue"] == 0
        assert model["complete_state_graph"] is True


def test_tlc_parser_rejects_incomplete_state_graph() -> None:
    result = subprocess.CompletedProcess(
        args=["tlc"],
        returncode=0,
        stdout=(
            "TLC2 Version 2.20\n"
            "Model checking completed. No error has been found.\n"
            "10 states generated, 8 distinct states found, 2 states left on queue.\n"
            "The depth of the complete state graph search is 4.\n"
        ),
        stderr="",
    )

    payload = _TLC_MODULE.parse_tlc_output(_TLC_MODULE.TlcModel("demo", REPO_ROOT, "Demo"), result)

    assert payload["status"] == "fail"
    assert payload["complete_state_graph"] is False
    assert payload["states_left_on_queue"] == 2
