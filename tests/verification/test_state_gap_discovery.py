from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "formal" / "discover_state_gaps.py"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


def test_state_gap_discovery_reports_uncovered_scenarios(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("observable evidence\n", encoding="utf-8")
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(
        """
version: 1
subsystems:
  - id: demo_protocol
    dimensions:
      client: [known, unknown]
      resource: [valid, mismatched]
    obligations:
      - id: happy_path
        status: implemented
        evidence: [evidence.txt]
        when:
          client: known
          resource: valid
        expected: continue
      - id: unknown_client_fails_closed
        status: implemented
        evidence: [evidence.txt]
        when:
          client: unknown
        expected: fail_closed
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "gap"
    assert payload["uncovered_count"] == 1
    assert payload["uncovered"][0]["subsystem"] == "demo_protocol"
    assert payload["uncovered"][0]["state"] == {"client": "known", "resource": "mismatched"}


def test_state_gap_discovery_accepts_complete_obligation_catalog(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("observable evidence\n", encoding="utf-8")
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(
        """
version: 1
subsystems:
  - id: demo_protocol
    dimensions:
      client: [known, unknown]
      resource: [valid, mismatched]
    obligations:
      - id: happy_path
        status: implemented
        priority: 10
        evidence: [evidence.txt]
        when:
          client: known
          resource: valid
        expected: continue
      - id: known_bad_resource_fails_closed
        status: implemented
        priority: 10
        evidence: [evidence.txt]
        when:
          client: known
          resource: mismatched
        expected: fail_closed
      - id: unknown_client_fails_closed
        status: implemented
        priority: 20
        evidence: [evidence.txt]
        when:
          client: unknown
        expected: fail_closed
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "covered"
    assert payload["total_states"] == 4
    assert payload["covered_count"] == 4
    assert payload["uncovered_count"] == 0


def test_state_gap_discovery_treats_declared_unhandled_states_as_gaps(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("observable evidence\n", encoding="utf-8")
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(
        """
version: 1
subsystems:
  - id: demo_protocol
    dimensions:
      client: [known]
      storage_fault: [none, enospc]
    obligations:
      - id: happy_path
        status: implemented
        evidence: [evidence.txt]
        when:
          client: known
          storage_fault: none
        expected: continue
      - id: enospc_needs_design
        status: known_gap
        when:
          client: known
          storage_fault: enospc
        expected: fail_closed
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "gap"
    assert payload["uncovered_count"] == 0
    assert payload["known_gap_count"] == 1
    assert payload["known_gaps"][0]["obligations"] == ["enospc_needs_design"]


def test_state_gap_discovery_rejects_ambiguous_highest_priority_matches(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("observable evidence\n", encoding="utf-8")
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(
        """
version: 1
subsystems:
  - id: demo_protocol
    dimensions:
      client: [known]
    obligations:
      - id: first_rule
        status: implemented
        priority: 10
        evidence: [evidence.txt]
        when:
          client: known
        expected: continue
      - id: second_rule
        status: implemented
        priority: 10
        evidence: [evidence.txt]
        when:
          client: known
        expected: fail_closed
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "gap"
    assert payload["ambiguous_count"] == 1


def test_state_gap_discovery_requires_evidence_for_implemented_obligations(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(
        """
version: 1
subsystems:
  - id: demo_protocol
    dimensions:
      client: [known]
    obligations:
      - id: asserted_without_evidence
        status: implemented
        when:
          client: known
        expected: continue
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "gap"
    assert payload["missing_evidence_count"] == 1


def test_state_gap_discovery_rejects_empty_subsystems(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yml"
    catalog.write_text("version: 1\nsubsystems: []\n", encoding="utf-8")

    result = _run(
        [sys.executable, str(SCRIPT), "--catalog", str(catalog), "--json", "--fail-on-gap"],
        tmp_path,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "schema_error"
    assert payload["total_states"] == 0


def test_repo_oauth_catalog_models_reauth_recovery_explicitly() -> None:
    import yaml

    catalog = yaml.safe_load(
        (REPO_ROOT / "docs/verification/state-space-obligations.yml").read_text()
    )
    oauth = next(item for item in catalog["subsystems"] if item["id"] == "oauth_mcp")

    assert "missing_recoverable" in oauth["dimensions"]["client_state"]
    assert "missing_malformed" in oauth["dimensions"]["client_state"]

    obligations = {item["id"]: item for item in oauth["obligations"]}
    assert (
        obligations["oauth_recoverable_missing_client_starts_reauth_without_token"]["expected"]
        == "recover_client_registration_then_restart_authorization_without_token_issue"
    )
    assert (
        obligations["oauth_recovered_client_token_requires_completed_auth_chain"]["expected"]
        == "issue_token_only_after_reauth_github_pkce_resource_redirect_valid"
    )
    assert obligations["oauth_malformed_missing_client_fails_closed"]["expected"] == "fail_closed"
