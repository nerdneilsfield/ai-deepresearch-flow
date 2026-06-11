from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner
import click
import pytest

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.config import (
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    BaseConfig,
    EmbeddingConfig,
    EmbeddingModelConfig,
    EmbeddingProviderConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
)
from deepresearch_flow.paper.routing import (
    ParsedModelSelector,
    ProviderOutOfActiveWindow,
    provider_window_error_as_click,
)


def _window_error() -> ProviderOutOfActiveWindow:
    return ProviderOutOfActiveWindow(
        ["https://window.example.com/v1"],
        datetime(2026, 4, 21, 22, 0, tzinfo=timezone.utc),
    )


def _assert_wrapped_cli_error(result) -> None:
    assert result.exit_code == 1
    assert not isinstance(result.exception, ProviderOutOfActiveWindow)
    assert isinstance(result.exception, SystemExit)
    assert result.output.strip().splitlines() == [f"Error: {_window_error()}"]


def _basic_provider() -> ProviderConfig:
    return ProviderConfig(
        name="openai",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://api.example.com/v1",
                weight=1,
                key=[KeyConfig(value="test-key", weight=1)],
            )
        ],
        models=[
            ModelCapability(
                model_name="gpt-4.1",
                is_stream=True,
                is_support_json_schema=True,
                is_support_json_object=True,
            )
        ],
        api_version=None,
        deployment=None,
        project_id=None,
        location=None,
        credentials_path=None,
        anthropic_version=None,
        max_tokens=None,
        extra_headers={},
        system_prompt=None,
        user_prompt=None,
    )


def _basic_config(*, with_embedding: bool = False) -> PaperConfig:
    provider = _basic_provider()
    embedding = None
    if with_embedding:
        embedding = EmbeddingConfig(
            default_model="bge-m3",
            default_provider="ollama",
            dimensions=1024,
            normalized=True,
            batch_size=2,
            chunk_max_tokens=512,
            chunk_overlap_tokens=64,
            providers=[
                EmbeddingProviderConfig(
                    name="ollama",
                    type="openai_compatible",
                    base=[
                        BaseConfig(
                            url="http://localhost:11434/v1",
                            weight=1,
                            key=[KeyConfig(value="ollama", weight=1)],
                        )
                    ],
                    models=[
                        EmbeddingModelConfig(model_name="bge-m3", dimensions=1024, max_context=8192)
                    ],
                )
            ],
        )
    return PaperConfig(
        extract=DEFAULT_EXTRACT,
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="openai/gpt-4.1", weight=1)],
        embedding=embedding,
    )


def test_provider_window_error_as_click_wraps_provider_window_error() -> None:
    error = _window_error()

    with pytest.raises(click.ClickException) as exc_info:
        with provider_window_error_as_click():
            raise error

    assert exc_info.value.message == str(error)
    assert exc_info.value.exit_code == 1


def test_provider_window_error_as_click_does_not_wrap_other_runtime_errors() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with provider_window_error_as_click():
            raise RuntimeError("boom")


def test_provider_window_error_as_click_allows_click_exception_to_pass_through() -> None:
    with pytest.raises(click.ClickException, match="already wrapped"):
        with provider_window_error_as_click():
            raise click.ClickException("already wrapped")


def test_translator_cli_wraps_provider_window_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepresearch_flow.translator.cli as mod

    source_path = tmp_path / "doc.md"
    source_path.write_text("# Title\n", encoding="utf-8")
    monkeypatch.setattr(mod, "load_config", lambda path: _basic_config())
    monkeypatch.setattr(
        mod,
        "parse_model_selector",
        lambda *args, **kwargs: ParsedModelSelector(
            kind="single", fixed_model="openai/gpt-4.1", pool=[]
        ),
    )
    monkeypatch.setattr(
        mod, "select_runtime_route", lambda *args, **kwargs: (_ for _ in ()).throw(_window_error())
    )

    result = CliRunner().invoke(
        cli,
        ["translator", "translate", "--input", str(source_path), "--model", "openai/gpt-4.1"],
    )

    _assert_wrapped_cli_error(result)


def test_recognize_cli_wraps_provider_window_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepresearch_flow.recognize.cli as mod

    source_path = tmp_path / "doc.md"
    source_path.write_text("x^2\n", encoding="utf-8")
    monkeypatch.setattr(mod, "require_pylatexenc", lambda: None)
    monkeypatch.setattr(mod, "load_config", lambda path: _basic_config())
    monkeypatch.setattr(
        mod,
        "parse_model_selector",
        lambda *args, **kwargs: ParsedModelSelector(
            kind="single", fixed_model="openai/gpt-4.1", pool=[]
        ),
    )
    monkeypatch.setattr(
        mod, "select_runtime_route", lambda *args, **kwargs: (_ for _ in ()).throw(_window_error())
    )

    result = CliRunner().invoke(
        cli,
        [
            "recognize",
            "fix-math",
            "--input",
            str(source_path),
            "--in-place",
            "--model",
            "openai/gpt-4.1",
        ],
    )

    _assert_wrapped_cli_error(result)


def test_utils_cli_wraps_provider_window_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepresearch_flow.utils.cli as mod

    monkeypatch.setattr(mod, "load_config", lambda path: _basic_config())
    monkeypatch.setattr(
        mod,
        "parse_model_selector",
        lambda *args, **kwargs: ParsedModelSelector(
            kind="single", fixed_model="openai/gpt-4.1", pool=[]
        ),
    )
    monkeypatch.setattr(
        mod, "select_runtime_route", lambda *args, **kwargs: (_ for _ in ()).throw(_window_error())
    )

    result = CliRunner().invoke(
        cli,
        [
            "utils",
            "test-mode",
            "--config",
            str(tmp_path / "config.toml"),
            "--model",
            "openai/gpt-4.1",
        ],
    )

    _assert_wrapped_cli_error(result)


def test_paper_embed_cli_wraps_provider_window_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepresearch_flow.paper.cli as mod
    import deepresearch_flow.paper.embed_pipeline as embed_pipeline
    import deepresearch_flow.paper.vector_store as vector_store

    monkeypatch.setattr(mod, "load_config", lambda path: _basic_config(with_embedding=True))
    monkeypatch.setattr(vector_store, "preflight_vector_store", lambda *args, **kwargs: None)

    async def raise_window_error(*args, **kwargs):
        raise _window_error()

    monkeypatch.setattr(embed_pipeline, "run_embed_pipeline", raise_window_error)

    result = CliRunner().invoke(
        cli,
        ["paper", "embed", "--config", "config.toml", "--input", str(tmp_path / "papers.json")],
    )

    _assert_wrapped_cli_error(result)


def test_paper_db_generate_tags_cli_wraps_provider_window_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deepresearch_flow.paper.db as mod

    class _FakeRoutePool:
        @staticmethod
        def from_selector(*args, **kwargs):
            raise _window_error()

    monkeypatch.setattr(mod, "load_config", lambda path: _basic_config())
    monkeypatch.setattr(
        mod,
        "parse_model_selector",
        lambda *args, **kwargs: ParsedModelSelector(
            kind="single", fixed_model="openai/gpt-4.1", pool=[]
        ),
    )
    monkeypatch.setattr(mod, "RoutePool", _FakeRoutePool)

    result = CliRunner().invoke(
        cli,
        [
            "paper",
            "db",
            "generate-tags",
            "--input",
            str(tmp_path / "papers.json"),
            "--output",
            str(tmp_path / "out.json"),
            "--config",
            "config.toml",
            "--model",
            "openai/gpt-4.1",
        ],
    )

    _assert_wrapped_cli_error(result)
