from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.config import load_config


def test_loads_weighted_provider_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 2 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [
          { url = "https://api.example.com/v1", weight = 1, key = [{ value = "env:OPENAI_API_KEY", weight = 1 }] }
        ]
        models = [
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }
        ]
        """,
        encoding="utf-8",
    )

    loaded = load_config(str(config_path))

    assert loaded.main_model[0].model == "openai/gpt-4.1"
    assert loaded.main_model[0].weight == 2
    assert loaded.providers[0].base[0].key[0].value == "env:OPENAI_API_KEY"
    assert loaded.providers[0].models[0].model_name == "gpt-4.1"


def test_rejects_legacy_api_keys_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        api_keys = ["env:OPENAI_API_KEY"]
        model_list = ["gpt-4.1"]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="legacy provider field"):
        load_config(str(config_path))


def test_rejects_missing_main_model(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="main_model"):
        load_config(str(config_path))


def test_rejects_main_model_reference_not_declared(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4o-mini", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not resolve"):
        load_config(str(config_path))


def test_rejects_non_positive_weight(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 0 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "test-key", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="weight"):
        load_config(str(config_path))


def test_env_resolution_failure_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 1 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "env:OPENAI_API_KEY", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Environment variable not set"):
        load_config(str(config_path))
