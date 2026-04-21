from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.config import load_config


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [
          { url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }
        ]
        models = [
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = false, is_support_json_object = false }
        ]
        """,
        encoding="utf-8",
    )
    return config_path


def test_rejects_bare_model_name(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "gpt-4.1",
        ],
    )

    assert result.exit_code != 0
    assert "provider/model" in result.output


def test_rejects_unknown_declared_model(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-unknown",
        ],
    )

    assert result.exit_code != 0
    assert "does not resolve" in result.output


def test_reports_probe_results_without_write_back(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)
    original = config_path.read_text(encoding="utf-8")

    def fake_probe(route, mode):
        assert route.provider.name == "openai"
        return mode == "json_schema"

    monkeypatch.setattr("deepresearch_flow.utils.cli.probe_model_mode", fake_probe)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-4.1",
        ],
    )

    assert result.exit_code == 0
    assert "json_schema" in result.output
    assert "json_object" in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_write_back_updates_only_probed_modes(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)

    def fake_probe(route, mode):
        return mode == "json_schema"

    monkeypatch.setattr("deepresearch_flow.utils.cli.probe_model_mode", fake_probe)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-4.1",
            "--write-back",
        ],
    )

    assert result.exit_code == 0
    assert "Wrote probe results back to" in result.output
    updated = config_path.read_text(encoding="utf-8")
    assert "is_support_json_schema = true" in updated
    assert "is_support_json_object = false" in updated
    reloaded = load_config(str(config_path))
    assert [item.model for item in reloaded.main_model] == ["openai/gpt-4.1"]


def test_probe_failure_exits_non_zero_and_does_not_write_back(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)
    original = config_path.read_text(encoding="utf-8")

    def fake_probe(route, mode):
        raise RuntimeError(f"probe failed for {mode}")

    monkeypatch.setattr("deepresearch_flow.utils.cli.probe_model_mode", fake_probe)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-4.1",
            "--write-back",
        ],
    )

    assert result.exit_code != 0
    assert "probe failed" in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_explicit_unsupported_error_writes_back_false(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)

    def fake_probe(route, mode):
        if mode == "json_schema":
            raise RuntimeError(
                "BadRequest | response_format.type is not valid: json_schema is not supported by this model"
            )
        return True

    monkeypatch.setattr("deepresearch_flow.utils.cli.probe_model_mode", fake_probe)

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-4.1",
            "--write-back",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = config_path.read_text(encoding="utf-8")
    assert "is_support_json_schema = false" in updated
    assert "is_support_json_object = true" in updated
    assert "unsupported" in result.output


def test_write_back_preserves_active_window_fields(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }',
            '{ url = "https://api.example.com/v1", weight = 1, active_windows = ["00:00-24:00"], active_timezone = "Asia/Shanghai", key = [{ value = "test-key", weight = 1 }] }',
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("deepresearch_flow.utils.cli.probe_model_mode", lambda route, mode: mode == "json_schema")

    result = runner.invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(config_path),
            "--model",
            "openai/gpt-4.1",
            "--write-back",
        ],
    )

    assert result.exit_code == 0
    updated = config_path.read_text(encoding="utf-8")
    assert 'active_windows = ["00:00-24:00"]' in updated
    assert 'active_timezone = "Asia/Shanghai"' in updated
    reloaded = load_config(str(config_path))
    assert reloaded.providers[0].base[0].active_windows == ["00:00-24:00"]
    assert reloaded.providers[0].base[0].active_timezone == "Asia/Shanghai"
