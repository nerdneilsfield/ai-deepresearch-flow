from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from deepresearch_flow.cli import cli


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [
          { model = "openai/gpt-4.1", weight = 2 },
          { model = "openai/gpt-4.1-mini", weight = 1 }
        ]

        [extract]
        output = "paper_infos.json"
        errors = "paper_errors.json"
        max_concurrency = 1
        max_retries = 1
        timeout = 1
        backoff_base_seconds = 1.0
        backoff_max_seconds = 1.0
        pause_threshold_seconds = 0.0
        truncate_strategy = "head_tail"
        truncate_max_chars = 1000
        cost_estimate = true
        stage_dag = false

        [render]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [
          { url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }
        ]
        models = [
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true },
          { model_name = "gpt-4.1-mini", is_stream = true, is_support_json_schema = true, is_support_json_object = true }
        ]
        """,
        encoding="utf-8",
    )
    return config_path


def test_paper_extract_uses_config_main_model_when_model_omitted(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)
    input_path = tmp_path / "doc.md"
    input_path.write_text("# test", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_extract_documents(*args, **kwargs):
        captured["provider"] = kwargs["provider"]
        captured["model"] = kwargs["model"]
        captured["model_selector"] = kwargs["model_selector"]

    monkeypatch.setattr("deepresearch_flow.paper.cli.extract_documents", fake_extract_documents)

    result = runner.invoke(
        cli,
        [
            "paper",
            "extract",
            "--config",
            str(config_path),
            "--input",
            str(input_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["provider"] is None
    assert captured["model"] is None
    selector = captured["model_selector"]
    assert selector is not None
    assert selector.kind == "pool"
    assert [item.model for item in selector.pool] == ["openai/gpt-4.1", "openai/gpt-4.1-mini"]


def test_paper_extract_model_flag_overrides_config_main_model(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    config_path = _write_config(tmp_path)
    input_path = tmp_path / "doc.md"
    input_path.write_text("# test", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_extract_documents(*args, **kwargs):
        captured["provider"] = kwargs["provider"]
        captured["model"] = kwargs["model"]
        captured["model_selector"] = kwargs["model_selector"]

    monkeypatch.setattr("deepresearch_flow.paper.cli.extract_documents", fake_extract_documents)

    result = runner.invoke(
        cli,
        [
            "paper",
            "extract",
            "--config",
            str(config_path),
            "--input",
            str(input_path),
            "--model",
            "openai/gpt-4.1-mini",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["provider"] is not None
    assert captured["model"] == "gpt-4.1-mini"
    selector = captured["model_selector"]
    assert selector is not None
    assert selector.kind == "single"
    assert selector.fixed_model == "openai/gpt-4.1-mini"
