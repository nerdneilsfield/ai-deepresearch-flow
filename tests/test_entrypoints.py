from __future__ import annotations

import runpy
import sys


def test_module_main_delegates_to_cli_main(monkeypatch) -> None:
    calls: list[str] = []

    def fake_main() -> None:
        calls.append("called")

    monkeypatch.setattr("deepresearch_flow.cli.main", fake_main)

    runpy.run_module("deepresearch_flow.__main__", run_name="__main__")

    assert calls == ["called"]


def test_cli_module_runs_main_when_executed_as_script(monkeypatch) -> None:
    calls: list[str] = []

    def fake_cli() -> None:
        calls.append("called")

    monkeypatch.setattr("click.core.Group.__call__", lambda self, *args, **kwargs: fake_cli())
    sys.modules.pop("deepresearch_flow.cli", None)

    runpy.run_module("deepresearch_flow.cli", run_name="__main__")

    assert calls == ["called"]
