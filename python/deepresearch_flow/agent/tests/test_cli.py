"""Black-box tests for the Agent CLI command surface."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.agent.cli import agent


def test_agent_cli_exposes_submit_status_retry_cancel_and_review() -> None:
    result = CliRunner().invoke(agent, ["--help"])
    assert result.exit_code == 0
    for command in ("submit", "status", "submission", "retry", "cancel", "review"):
        assert command in result.output


def test_agent_submit_requires_idempotency_key_and_pdf() -> None:
    result = CliRunner().invoke(
        agent,
        ["submit", "--api", "http://localhost:8001/api/v1/agent", "--token", "token"],
    )
    assert result.exit_code != 0
    assert "Missing option" in result.output
