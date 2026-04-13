from __future__ import annotations

import logging
from pathlib import Path
import re

from click.testing import CliRunner

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.translator.cli import configure_logging
from deepresearch_flow.translator.engine import TranslationResult, TranslationStats
from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.scheduler import DocStage


def _write_config(tmp_path: Path, extra: str = "") -> Path:
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
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true },
          { model_name = "gpt-4.1-fallback", is_stream = true, is_support_json_schema = true, is_support_json_object = true }
        ]
        """,
        encoding="utf-8",
    )
    if extra:
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n" + extra.strip() + "\n",
            encoding="utf-8",
        )
    return config_path


def test_configure_logging_quiets_httpx_debug_noise(monkeypatch) -> None:
    calls: dict[str, str] = {}
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.coloredlogs.install",
        lambda **kwargs: calls.update(kwargs),
    )
    quiet_names = [
        "httpx",
        "httpx._client",
        "httpx._transports",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
    ]
    old_levels = {name: logging.getLogger(name).level for name in quiet_names}
    try:
        for name in quiet_names:
            logging.getLogger(name).setLevel(logging.NOTSET)
        configure_logging(verbose=True)
        assert calls["level"] == "DEBUG"
        for name in quiet_names:
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        for name, level in old_levels.items():
            logging.getLogger(name).setLevel(level)


def test_translate_skips_failed_file_and_continues(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    bad_path = input_dir / "bad.md"
    good_path = input_dir / "good.md"
    bad_path.write_text("bad content", encoding="utf-8")
    good_path.write_text("good content", encoding="utf-8")
    config_path = _write_config(tmp_path)

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (self, paths, kwargs)
        output_map[good_path].write_text("translated good content", encoding="utf-8")
        return [bad_path]

    monkeypatch.setattr("deepresearch_flow.translator.scheduler.Scheduler.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "bad.md" in result.output
    assert "Failed" in result.output
    assert not (output_dir / "bad.zh.md").exists()
    assert (output_dir / "good.zh.md").read_text(encoding="utf-8") == "translated good content"
    assert "Processed" in result.output
    assert "Failed files" in result.output


def test_translate_uses_fallback_model_after_group_provider_error(
    tmp_path: Path, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text("source content", encoding="utf-8")
    config_path = _write_config(tmp_path)

    async def fake_translate_group(
        self,
        group_text,
        provider,
        model,
        *args,
        **kwargs,
    ):
        if model == "gpt-4.1":
            raise ProviderError("timeout", retryable=True)
        return re.sub(
            r"(<NODE_START_\d+>\n)(.*?)(\n</NODE_END_\d+>)",
            r"\1已翻译文本\3",
            group_text,
            flags=re.DOTALL,
        )

    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._translate_group",
        fake_translate_group,
    )

    async def fake_format_markdown(self, text, stage):
        return text

    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        fake_format_markdown,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
            "--fallback-model",
            "openai/gpt-4.1-fallback",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Failed files" in result.output
    assert (output_dir / "doc.zh.md").read_text(encoding="utf-8").strip() == "已翻译文本"


def test_translate_uses_translator_config_defaults_for_scheduler(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text("source content", encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        extra="""
        [translator_config]
        document_window = 5
        initial_workers = 3
        retry_workers = 2
        main_concurrency = 2
        """,
    )
    seen: dict[str, int] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["document_window"] = self._document_window
        seen["initial_workers"] = self._configs[DocStage.TRANSLATING].workers
        seen["retry_workers"] = self._configs[DocStage.RETRYING].workers
        seen["main_concurrency"] = self._configs[DocStage.TRANSLATING].provider_semaphore._value
        return []

    monkeypatch.setattr("deepresearch_flow.translator.scheduler.Scheduler.run", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "document_window": 5,
        "initial_workers": 3,
        "retry_workers": 2,
        "main_concurrency": 2,
    }


def test_group_concurrency_maps_to_initial_workers(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text("source content", encoding="utf-8")
    config_path = _write_config(tmp_path)
    seen: dict[str, int] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["initial_workers"] = self._configs[DocStage.TRANSLATING].workers
        return []

    monkeypatch.setattr("deepresearch_flow.translator.scheduler.Scheduler.run", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
            "--group-concurrency",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "deprecated" in result.output.lower()
    assert seen == {"initial_workers": 4}


def test_dump_requests_log_stays_on_scheduler_path(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text("source content", encoding="utf-8")
    config_path = _write_config(tmp_path)
    calls: list[str] = []

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (self, paths, output_map, kwargs)
        calls.append("scheduler")
        return []

    async def fake_translate(self, content, *args, **kwargs):
        _ = (self, content, args, kwargs)
        calls.append("compat")
        return TranslationResult(
            translated_text="translated",
            protected_text="translated",
            placeholder_store=PlaceHolderStore(),
            nodes={},
            stats=TranslationStats(
                total_nodes=1,
                success_nodes=1,
                failed_nodes=0,
                skipped_nodes=0,
                initial_groups=1,
                retry_groups=0,
                retry_rounds=0,
            ),
        )

    monkeypatch.setattr("deepresearch_flow.translator.scheduler.Scheduler.run", fake_run)
    monkeypatch.setattr("deepresearch_flow.translator.cli.MarkdownTranslator.translate", fake_translate)

    result = CliRunner().invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-requests-log",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["scheduler"]


def test_compat_path_defaults_group_concurrency_to_one(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text("source content", encoding="utf-8")
    config_path = _write_config(tmp_path)
    seen: dict[str, int | None] = {}

    async def fake_translate(self, content, *args, **kwargs):
        _ = (self, content, args)
        seen["group_concurrency"] = kwargs["group_concurrency"]
        return TranslationResult(
            translated_text="translated",
            protected_text="translated",
            placeholder_store=PlaceHolderStore(),
            nodes={},
            stats=TranslationStats(
                total_nodes=1,
                success_nodes=1,
                failed_nodes=0,
                skipped_nodes=0,
                initial_groups=1,
                retry_groups=0,
                retry_rounds=0,
            ),
        )

    monkeypatch.setattr("deepresearch_flow.translator.cli.MarkdownTranslator.translate", fake_translate)

    result = CliRunner().invoke(
        cli,
        [
            "translator",
            "translate",
            "--config",
            str(config_path),
            "--input",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-protected",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {"group_concurrency": 1}
