from __future__ import annotations

import json
import logging
import random
from pathlib import Path
import re

from click.testing import CliRunner
import httpx
import pytest

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


def _collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _load_json_or_text(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


_PLACEHOLDER_TOKEN_RE = re.compile(r"__PH_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*__")


def _collect_placeholder_tokens(value):
    for text in _collect_strings(value):
        yield from _PLACEHOLDER_TOKEN_RE.findall(text)


async def _fake_openai_send(self, request, *args, **kwargs):
    _ = (self, args, kwargs)
    payload = None
    try:
        payload = json.loads(request.content.decode("utf-8"))
    except Exception:
        payload = {}

    translated = "<NODE_START_0000>\n已翻译文本\n</NODE_END_0000>\n"
    if payload.get("stream"):
        body = (
            'data: {"choices":[{"delta":{"content":"'
            + translated.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            + '"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode("utf-8")
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            request=request,
        )

    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": translated}}]},
        request=request,
    )


async def _identity_format_markdown(self, text, stage):
    _ = (self, stage)
    return text


async def _available_permits(semaphore) -> int:
    count = 0
    while not semaphore.locked():
        await semaphore.acquire()
        count += 1
    for _ in range(count):
        semaphore.release()
    return count


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
        model = "openai/gpt-4.1"
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
        seen["main_concurrency"] = await _available_permits(
            self._configs[DocStage.TRANSLATING].provider_semaphore
        )
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "document_window": 5,
        "initial_workers": 3,
        "retry_workers": 2,
        "main_concurrency": 2,
    }


def test_translate_uses_translator_config_retry_model_and_concurrency(
    tmp_path: Path, monkeypatch
) -> None:
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
        model = "openai/gpt-4.1"
        retry_model = "openai/gpt-4.1-fallback"
        retry_workers = 3
        retry_concurrency = 5
        """,
    )
    seen: dict[str, object] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["initial_model"] = self._configs[DocStage.TRANSLATING].model
        seen["retry_model"] = self._configs[DocStage.RETRYING].model
        seen["retry_workers"] = self._configs[DocStage.RETRYING].workers
        seen["retry_concurrency"] = await _available_permits(
            self._configs[DocStage.RETRYING].provider_semaphore
        )
        seen["shares_main_semaphore"] = (
            self._configs[DocStage.RETRYING].provider_semaphore
            is self._configs[DocStage.TRANSLATING].provider_semaphore
        )
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "initial_model": "gpt-4.1",
        "retry_model": "gpt-4.1-fallback",
        "retry_workers": 3,
        "retry_concurrency": 5,
        "shares_main_semaphore": False,
    }


def test_translate_keeps_retry_on_main_model_when_retry_model_omitted(
    tmp_path: Path, monkeypatch
) -> None:
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
        model = "openai/gpt-4.1"
        main_concurrency = 4
        retry_workers = 2
        """,
    )
    seen: dict[str, object] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["initial_model"] = self._configs[DocStage.TRANSLATING].model
        seen["retry_model"] = self._configs[DocStage.RETRYING].model
        seen["shares_main_route_pool"] = (
            self._configs[DocStage.RETRYING].route_pool
            is self._configs[DocStage.TRANSLATING].route_pool
        )
        seen["shares_main_semaphore"] = (
            self._configs[DocStage.RETRYING].provider_semaphore
            is self._configs[DocStage.TRANSLATING].provider_semaphore
        )
        seen["retry_concurrency"] = await _available_permits(
            self._configs[DocStage.RETRYING].provider_semaphore
        )
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "initial_model": "gpt-4.1",
        "retry_model": "gpt-4.1",
        "shares_main_route_pool": True,
        "shares_main_semaphore": True,
        "retry_concurrency": 4,
    }


def test_translate_rejects_retry_concurrency_without_retry_model(tmp_path: Path) -> None:
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
        model = "openai/gpt-4.1"
        retry_concurrency = 5
        """,
    )

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
        ],
    )

    assert result.exit_code != 0
    assert "retry_concurrency requires retry_model" in result.output


def test_translate_retry_cli_options_override_config_defaults(
    tmp_path: Path, monkeypatch
) -> None:
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
        model = "openai/gpt-4.1"
        retry_model = "openai/gpt-4.1-fallback"
        retry_concurrency = 2
        """,
    )
    seen: dict[str, object] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["retry_model"] = self._configs[DocStage.RETRYING].model
        seen["retry_concurrency"] = await _available_permits(
            self._configs[DocStage.RETRYING].provider_semaphore
        )
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
            "--retry-model",
            "openai/gpt-4.1",
            "--retry-concurrency",
            "6",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "retry_model": "gpt-4.1",
        "retry_concurrency": 6,
    }


def test_translate_defaults_global_concurrency_to_enabled_stage_sum(
    tmp_path: Path, monkeypatch
) -> None:
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
        model = "openai/gpt-4.1"
        retry_model = "openai/gpt-4.1-fallback"
        fallback_model = "openai/gpt-4.1-fallback"
        main_concurrency = 4
        retry_concurrency = 2
        fallback_concurrency = 3
        """,
    )
    seen: dict[str, int] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["global_concurrency"] = await _available_permits(self._global_sem)
        seen["main_concurrency"] = await _available_permits(
            self._configs[DocStage.TRANSLATING].provider_semaphore
        )
        seen["retry_concurrency"] = await _available_permits(
            self._configs[DocStage.RETRYING].provider_semaphore
        )
        seen["fallback_concurrency"] = await _available_permits(
            self._configs[DocStage.FALLBACK_1].provider_semaphore
        )
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "global_concurrency": 9,
        "main_concurrency": 4,
        "retry_concurrency": 2,
        "fallback_concurrency": 3,
    }


def test_translate_explicit_max_concurrency_overrides_stage_sum(
    tmp_path: Path, monkeypatch
) -> None:
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
        model = "openai/gpt-4.1"
        retry_model = "openai/gpt-4.1-fallback"
        fallback_model = "openai/gpt-4.1-fallback"
        main_concurrency = 4
        retry_concurrency = 2
        fallback_concurrency = 3
        """,
    )
    seen: dict[str, int] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["global_concurrency"] = await _available_permits(self._global_sem)
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
            "--max-concurrency",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {"global_concurrency": 5}


def test_translate_logs_resolved_scheduler_concurrency(
    tmp_path: Path, monkeypatch, caplog
) -> None:
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
        model = "openai/gpt-4.1"
        retry_model = "openai/gpt-4.1-fallback"
        fallback_model = "openai/gpt-4.1-fallback"
        document_window = 7
        initial_workers = 4
        retry_workers = 2
        fallback_workers = 3
        main_concurrency = 4
        retry_concurrency = 2
        fallback_concurrency = 3
        """,
    )

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (self, paths, output_map, kwargs)
        return []

    monkeypatch.setattr("deepresearch_flow.translator.scheduler.Scheduler.run", fake_run)
    caplog.set_level(logging.INFO, logger="deepresearch_flow.translator.cli")

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
        ],
    )

    assert result.exit_code == 0, result.output
    log_text = "\n".join(caplog.messages)
    assert "Translator scheduler concurrency: global=9 (auto=sum(enabled stages))" in log_text
    assert "document_window=7" in log_text
    assert "initial=workers:4/concurrency:4" in log_text
    assert "retry=workers:2/concurrency:2/dedicated" in log_text
    assert "fallback=workers:3/concurrency:3" in log_text
    assert "Translator scheduler models: main=gpt-4.1, retry=gpt-4.1-fallback (dedicated)" in log_text


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


def test_translate_uses_model_defaults_from_translator_config(tmp_path: Path, monkeypatch) -> None:
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
        model = "openai/gpt-4.1"
        fallback_model = "openai/gpt-4.1-fallback"
        """,
    )
    seen: dict[str, str] = {}

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (paths, output_map, kwargs)
        seen["model"] = self._configs[DocStage.TRANSLATING].model
        seen["fallback"] = self._configs[DocStage.FALLBACK_1].model
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {
        "model": "gpt-4.1",
        "fallback": "gpt-4.1-fallback",
    }


def test_translate_requires_model_when_not_in_cli_or_config(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "doc.md").write_text("source content", encoding="utf-8")
    config_path = _write_config(tmp_path)

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
        ],
    )

    assert result.exit_code != 0
    assert "--model is required" in result.output


def test_translate_filters_documents_by_start_and_end_index(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    for name in ("001.md", "002.md", "003.md"):
        (input_dir / name).write_text(f"source {name}", encoding="utf-8")
    config_path = _write_config(tmp_path)

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (self, kwargs)
        for path in paths:
            output_map[path].write_text(path.stem, encoding="utf-8")
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
            "--start-index",
            "2",
            "--end-index",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (output_dir / "001.zh.md").exists()
    assert (output_dir / "002.zh.md").read_text(encoding="utf-8") == "002"
    assert (output_dir / "003.zh.md").read_text(encoding="utf-8") == "003"


def test_translate_start_index_without_end_uses_remaining_documents(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    for name in ("001.md", "002.md", "003.md"):
        (input_dir / name).write_text(f"source {name}", encoding="utf-8")
    config_path = _write_config(tmp_path)

    async def fake_run(self, *, paths, output_map, **kwargs):
        _ = (self, kwargs)
        for path in paths:
            output_map[path].write_text(path.stem, encoding="utf-8")
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
            "--start-index",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (output_dir / "001.zh.md").exists()
    assert (output_dir / "002.zh.md").read_text(encoding="utf-8") == "002"
    assert (output_dir / "003.zh.md").read_text(encoding="utf-8") == "003"


def test_translate_rejects_end_index_before_start_index(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "001.md").write_text("source 001", encoding="utf-8")
    config_path = _write_config(tmp_path)

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
            "--start-index",
            "3",
            "--end-index",
            "2",
        ],
    )

    assert result.exit_code != 0
    assert "--end-index must be greater than or equal to --start-index" in result.output


def test_translate_rejects_start_index_beyond_discovered_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "001.md").write_text("source 001", encoding="utf-8")
    config_path = _write_config(tmp_path)

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
            "--start-index",
            "2",
        ],
    )

    assert result.exit_code != 0
    assert "--start-index 2 exceeds discovered markdown count 1" in result.output


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


@pytest.mark.parametrize(
    "dump_flag",
    ["--dump-protected", "--dump-placeholders", "--dump-nodes"],
)
def test_dump_flags_stay_on_scheduler_path(
    tmp_path: Path, monkeypatch, dump_flag: str
) -> None:
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
            dump_flag,
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["scheduler"]


@pytest.mark.parametrize(
    ("dump_flag", "expected_suffix"),
    [
        ("--dump-requests-log", ".requests.json"),
        ("--dump-nodes", ".nodes.json"),
        ("--dump-protected", ".protected.md"),
        ("--dump-placeholders", ".placeholders.json"),
    ],
)
def test_dump_flags_write_semantic_debug_artifacts_through_scheduler(
    tmp_path: Path, monkeypatch, dump_flag: str, expected_suffix: str
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    debug_dir = tmp_path / "debug"
    input_dir.mkdir()
    output_dir.mkdir()
    debug_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text(
        "# Title\n\n"
        "See [example](https://example.com) and ![diagram](https://example.com/a.png).\n\n"
        "Footnote[^1]\n\n"
        "[^1]: detail\n",
        encoding="utf-8",
    )
    config_path = _write_config(tmp_path)

    monkeypatch.setattr("httpx.AsyncClient.send", _fake_openai_send)
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        _identity_format_markdown,
    )

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
            "--debug-dir",
            str(debug_dir),
            "--model",
            "openai/gpt-4.1",
            dump_flag,
        ],
    )

    assert result.exit_code == 0, result.output
    matches = [path for path in debug_dir.iterdir() if path.name.endswith(expected_suffix)]
    assert len(matches) == 1
    payload = _load_json_or_text(matches[0])

    if expected_suffix == ".requests.json":
        assert isinstance(payload, list)
        assert payload
        for entry in payload:
            assert isinstance(entry, dict)
            assert {
                "stage",
                "group_index",
                "attempt",
                "provider",
                "model",
                "messages",
            }.issubset(entry)
            assert "response" in entry or "error" in entry
    elif expected_suffix == ".nodes.json":
        assert isinstance(payload, dict)
        assert payload
        for node in payload.values():
            assert isinstance(node, dict)
            assert "origin_text" in node
            assert "translated_text" in node
    else:
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, ensure_ascii=False)
        assert "__PH_" in text
        if expected_suffix == ".protected.md":
            assert "__PH_" in text
        else:
            assert isinstance(payload, (dict, list, str))
            assert any("__PH_" in chunk for chunk in _collect_strings(payload))


def _build_seeded_fuzz_document(seed: int) -> str:
    rng = random.Random(seed)
    construct_templates = [
        (
            "link",
            "Reference [seed-{seed}-{index}](https://example.com/{seed}/{index}) for details.",
        ),
        (
            "image",
            "![seed-{seed}-{index}](https://example.com/assets/{seed}-{index}.png)",
        ),
        (
            "footnote",
            "Footnote marker[^seed{seed}{index}]\n\n[^seed{seed}{index}]: footnote detail {seed}-{index}",
        ),
        (
            "html",
            '<div data-seed="{seed}" data-index="{index}">html block {seed}-{index}</div>',
        ),
        (
            "math",
            "$$x_{seed}_{index} = \\frac{{1}}{{2}} + {seed} + {index}$$",
        ),
    ]
    count = rng.randint(3, len(construct_templates))
    chosen = rng.sample(construct_templates, k=count)
    rng.shuffle(chosen)

    parts = [
        f"# Seed {seed}",
        f"Intro paragraph for seed {seed}.",
    ]
    for index, (_, template) in enumerate(chosen, start=1):
        parts.append(template.format(seed=seed, index=index))
    parts.append(f"Closing paragraph for seed {seed}.")
    return "\n\n".join(parts) + "\n"


def _load_debug_artifact(debug_dir: Path, suffix: str):
    matches = sorted(path for path in debug_dir.iterdir() if path.name.endswith(suffix))
    assert matches, suffix
    assert matches[0].read_text(encoding="utf-8").strip(), matches[0].name
    return _load_json_or_text(matches[0])


FUZZ_SEEDS = tuple(range(3, 103))


def _assert_dump_artifacts_black_box(debug_dir: Path, output_dir: Path) -> None:
    protected_payload = _load_debug_artifact(debug_dir, ".protected.md")
    protected_text = (
        protected_payload
        if isinstance(protected_payload, str)
        else json.dumps(protected_payload, ensure_ascii=False)
    )
    protected_tokens = list(_collect_placeholder_tokens(protected_text))
    if "__PH_" in protected_text:
        assert protected_tokens
        assert all(token.startswith("__PH_") and token.endswith("__") for token in protected_tokens)

    placeholders_payload = _load_debug_artifact(debug_dir, ".placeholders.json")
    placeholder_tokens = list(_collect_placeholder_tokens(placeholders_payload))
    if placeholder_tokens:
        assert all(token.startswith("__PH_") and token.endswith("__") for token in placeholder_tokens)

    nodes_payload = _load_debug_artifact(debug_dir, ".nodes.json")
    assert isinstance(nodes_payload, dict)
    assert nodes_payload
    for node in nodes_payload.values():
        assert isinstance(node, dict)
        assert "origin_text" in node
        assert "translated_text" in node

    requests_payload = _load_debug_artifact(debug_dir, ".requests.json")
    assert isinstance(requests_payload, list)
    assert requests_payload
    for entry in requests_payload:
        assert isinstance(entry, dict)
        assert {
            "stage",
            "group_index",
            "attempt",
            "provider",
            "model",
            "messages",
        }.issubset(entry)
        assert "response" in entry or "error" in entry

    output_path = output_dir / "doc.zh.md"
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip()


def _build_response_perturbation_content(kind: str, variant: int) -> str:
    blocks = [
        f"<NODE_START_{index:04d}>\n已翻译文本 {kind} {variant} {index}\n</NODE_END_{index:04d}>\n"
        for index in range(3)
    ]
    noise_prefix = f"前置无关文本 {kind} {variant}\n\n"
    noise_suffix = f"\n\n尾部无关文本 {kind} {variant}"

    if kind == "ordered":
        return "".join(blocks)
    if kind == "reordered":
        return "".join([blocks[2], blocks[0], blocks[1]])
    if kind == "duplicate":
        return "".join([blocks[0], blocks[1], blocks[1], blocks[2]])
    if kind == "missing":
        return "".join([blocks[0], blocks[2]])
    if kind == "leading_noise":
        return noise_prefix + "".join(blocks)
    if kind == "trailing_noise":
        return "".join(blocks) + noise_suffix
    if kind == "partial":
        return "".join([blocks[0], f"<NODE_START_0001>\n已翻译文本 partial {variant} 1\n", blocks[2]])
    if kind == "mixed_error":
        return f"ERROR: upstream timeout {variant}\n\n" + "".join(blocks)
    raise ValueError(kind)


def _fake_openai_send_with_perturbation(
    response_text: str, *, include_error_field: bool = False
):
    async def _send(self, request, *args, **kwargs):
        _ = (self, args, kwargs)
        payload = None
        try:
            payload = json.loads(request.content.decode("utf-8"))
        except Exception:
            payload = {}

        if payload.get("stream"):
            lines = [
                'data: {"choices":[{"delta":{"content":"'
                + response_text.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                + '"}}]}'
            ]
            if include_error_field:
                lines.append('data: {"error":{"message":"upstream timeout"}}')
            lines.append("data: [DONE]")
            body = ("\n\n".join(lines) + "\n\n").encode("utf-8")
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream; charset=utf-8"},
                request=request,
            )

        response_payload = {"choices": [{"message": {"content": response_text}}]}
        if include_error_field:
            response_payload["error"] = {"message": "upstream timeout"}
        return httpx.Response(200, json=response_payload, request=request)

    return _send


def _dirty_case(name: str, body: str) -> tuple[str, str]:
    return name, body


def _indexed_templates(templates: tuple[str, ...], *, token_base: int) -> tuple[str, ...]:
    return tuple(
        template.format(i=index, token=token_base + index)
        for index, template in enumerate(templates, start=1)
    )


_NESTED_PRIMARY = tuple(
    f'<article>[outer-{i} ![inner-{i}](https://example.com/nested/{i}.png)](https://example.com/nested/{i}) <section>tail-{i}</section>'
    for i in range(1, 6)
)
_CROSS_PRIMARY = tuple(
    f'<!-- cross-{i} --> [link-{i}](https://example.com/cross/{i}) ![img-{i}](https://example.com/cross/{i}.png) <div>bridge-{i}</div>'
    for i in range(1, 6)
)
_HALF_CLOSED_PRIMARY = tuple(
    f'<div class="half-{i}">[link-{i}](https://example.com/half/{i}) ![img-{i}](https://example.com/half/{i}.png) $$x_{i} + y_{i}'
    for i in range(1, 6)
)
_PSEUDO_PRIMARY = tuple(
    f'Pseudo text __PH_{100 + i}__ beside [link-{i}](https://example.com/pseudo/{i}) and ![img-{i}](https://example.com/pseudo/{i}.png).'
    for i in range(1, 6)
)
_TOKEN_MIX_PRIMARY = tuple(
    f'__PH_{200 + i}__ meets __ph_{200 + i}__ and __PH-{200 + i}__ around [link-{i}](https://example.com/tokens/{i}) and <span>markup-{i}</span>.'
    for i in range(1, 6)
)
_ADJACENT_PRIMARY = tuple(
    f'[link-{i}](https://example.com/adjacent/{i})![img-{i}](https://example.com/adjacent/{i}.png)__PH_{300 + i}__<em>edge-{i}</em>'
    for i in range(1, 6)
)
_WHITESPACE_PRIMARY = tuple(
    f'\n\n\t  [link-{i}](https://example.com/space/{i}) \n\t ![img-{i}](https://example.com/space/{i}.png) \n\n __PH_{400 + i}__  '
    for i in range(1, 6)
)

_BROKEN_LINK_PRIMARY = _indexed_templates(
    (
        'Broken [link-{i}](ht!tp://exa mple.com/link/{i}) with ![img-{i}](https://example.com/link/{i}.png) and __PH_{token}__.',
        'Broken [link-{i}](https://example.com/link/{i} with a missing close and <div>tail-{i}</div>.',
        'Broken [link-{i}](https://example.com/link/{i})) with an extra close and [ref-{i}](https://example.com/ref/{i}).',
        'Broken [link-{i}(https://example.com/link/{i}) with a mismatched bracket and ![img-{i}](https://example.com/link/{i}.png).',
        'Broken [link-{i}](https://example.com/link/{i} path) with spaces in the target and <span>html-{i}</span>.',
    ),
    token_base=500,
)
_BROKEN_IMAGE_PRIMARY = _indexed_templates(
    (
        'Broken ![img-{i}](ht!tp://exa mple.com/image/{i}.png) with [link-{i}](https://example.com/image/{i}) and __PH_{token}__.',
        'Broken ![img-{i}](https://example.com/image/{i}.png with a missing close and <div>tail-{i}</div>.',
        'Broken ![img-{i}](https://example.com/image/{i}.png)) with an extra close and [link-{i}](https://example.com/ref/{i}).',
        'Broken ![img-{i}(https://example.com/image/{i}.png) with a mismatched alt bracket.',
        'Broken ![img-{i}](https://example.com/image/{i} path.png) with spaces and __PH_{token}__.',
    ),
    token_base=600,
)
_BROKEN_HTML_PRIMARY = _indexed_templates(
    (
        '<div class="html-{i}">open tag with [link-{i}](https://example.com/html/{i}) and ![img-{i}](https://example.com/html/{i}.png).',
        '<!-- comment-{i} starts but never closes [link-{i}](https://example.com/html/{i}) and __PH_{token}__.',
        '<span data-x="{i}">mismatched </div> with broken nesting and <em>tail-{i}</em>.',
        '<section><article>nested but missing end {i} and ![img-{i}](https://example.com/html/{i}.png).',
        '<section class="broken-{i}" data-i="{i}"',
    ),
    token_base=700,
)
_BROKEN_MATH_PRIMARY = _indexed_templates(
    (
        '$$x_{i} + y_{i} = z_{i} and [link-{i}](https://example.com/math/{i}) with __PH_{token}__.',
        '$a_{i} + b_{i} = c_{i} and ![img-{i}](https://example.com/math/{i}.png) before the close.',
        'Equation ends badly x_{i} + y_{i} = z_{i}$$ and <span>tail-{i}</span>.',
        '$$\n x_{i} + y_{i}\n and the display math never closes properly.',
        '$x_{i} + y_{i}$$ mixed fence and [link-{i}](https://example.com/math/{i}).',
    ),
    token_base=800,
)
_BROKEN_FOOTNOTE_PRIMARY = _indexed_templates(
    (
        'Marker[^f{i}] with [link-{i}](https://example.com/footnote/{i}) and ![img-{i}](https://example.com/footnote/{i}.png).',
        'Repeated marker[^f{i}] and again[^f{i}] with __PH_{token}__.\n\n[^f{i}]: note {i}.',
        'Orphan definition[^orphan{i}] in the body with <div>tail-{i}</div>.\n\n[^orphan{i}]: definition without ref.',
        'Dangling marker[^] with malformed footnote and $$x_{i}$$.',
        'Footnote body[^f{i}] plus adjacent token__PH_{token}__\n\n[^f{i}]: note.',
    ),
    token_base=900,
)
_BROKEN_FENCE_PRIMARY = _indexed_templates(
    (
        '```yaml\nkey: value\nbroken fence {i}\n[link-{i}](https://example.com/fence/{i})',
        '```html\n<div>[link-{i}](https://example.com/fence/{i}) and ![img-{i}](https://example.com/fence/{i}.png)\n',
        '```text\n__PH_{token}__ and broken fence {i}\n``',
        '```\ncontent starts with a stray fence and never resolves {i}',
        '```md\ncontent {i}\n```\n```',
    ),
    token_base=1000,
)


def _compose_dirty_fuzz_case(case_name: str, fragments: tuple[str, ...]) -> tuple[str, str]:
    body = "\n\n".join(
        [
            f"# Dirty fuzz {case_name}",
            "The document intentionally mixes several malformed fragments in a single long input.",
            *fragments,
            "Closing prose keeps the body realistic while still containing a trailing raw token __PH_TAIL__.",
        ]
    )
    return _dirty_case(case_name, body + "\n")


def _build_category_cases(
    category_name: str,
    primaries: tuple[str, ...],
    mix_a: tuple[str, ...],
    mix_b: tuple[str, ...],
    mix_c: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    cases = []
    for index, primary in enumerate(primaries, start=1):
        offset = index - 1
        cases.append(
            _compose_dirty_fuzz_case(
                f"{category_name}_{index:02d}_a",
                (
                    primary,
                    mix_a[offset % len(mix_a)],
                    mix_b[offset % len(mix_b)],
                    mix_c[offset % len(mix_c)],
                ),
            )
        )
        cases.append(
            _compose_dirty_fuzz_case(
                f"{category_name}_{index:02d}_b",
                (
                    mix_c[offset % len(mix_c)],
                    primary,
                    mix_a[(offset + 1) % len(mix_a)],
                    mix_b[(offset + 1) % len(mix_b)],
                ),
            )
        )
    return tuple(cases)


def _build_broken_subtype_cases(
    subtype_name: str, primaries: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    cases = []
    for index, primary in enumerate(primaries, start=1):
        offset = index - 1
        cases.append(
            _compose_dirty_fuzz_case(
                f"{subtype_name}_{index:02d}",
                (
                    primary,
                    _PSEUDO_PRIMARY[offset % len(_PSEUDO_PRIMARY)],
                    _WHITESPACE_PRIMARY[offset % len(_WHITESPACE_PRIMARY)],
                    _ADJACENT_PRIMARY[offset % len(_ADJACENT_PRIMARY)],
                ),
            )
        )
    return tuple(cases)


DETERMINISTIC_DIRTY_FUZZ_CASES = (
    _build_category_cases(
        "nested",
        _NESTED_PRIMARY,
        _CROSS_PRIMARY,
        _WHITESPACE_PRIMARY,
        _TOKEN_MIX_PRIMARY,
    )
    + _build_category_cases(
        "crossing",
        _CROSS_PRIMARY,
        _ADJACENT_PRIMARY,
        _WHITESPACE_PRIMARY,
        _PSEUDO_PRIMARY,
    )
    + _build_category_cases(
        "half_closed",
        _HALF_CLOSED_PRIMARY,
        _NESTED_PRIMARY,
        _CROSS_PRIMARY,
        _WHITESPACE_PRIMARY,
    )
    + _build_category_cases(
        "pseudo",
        _PSEUDO_PRIMARY,
        _TOKEN_MIX_PRIMARY,
        _ADJACENT_PRIMARY,
        _WHITESPACE_PRIMARY,
    )
    + _build_category_cases(
        "token_mix",
        _TOKEN_MIX_PRIMARY,
        _PSEUDO_PRIMARY,
        _ADJACENT_PRIMARY,
        _WHITESPACE_PRIMARY,
    )
    + _build_category_cases(
        "adjacent",
        _ADJACENT_PRIMARY,
        _WHITESPACE_PRIMARY,
        _TOKEN_MIX_PRIMARY,
        _PSEUDO_PRIMARY,
    )
    + _build_category_cases(
        "whitespace",
        _WHITESPACE_PRIMARY,
        _ADJACENT_PRIMARY,
        _PSEUDO_PRIMARY,
        _TOKEN_MIX_PRIMARY,
    )
    + _build_broken_subtype_cases("broken_link", _BROKEN_LINK_PRIMARY)
    + _build_broken_subtype_cases("broken_image", _BROKEN_IMAGE_PRIMARY)
    + _build_broken_subtype_cases("broken_html", _BROKEN_HTML_PRIMARY)
    + _build_broken_subtype_cases("broken_math", _BROKEN_MATH_PRIMARY)
    + _build_broken_subtype_cases("broken_footnote", _BROKEN_FOOTNOTE_PRIMARY)
    + _build_broken_subtype_cases("broken_fence", _BROKEN_FENCE_PRIMARY)
)

assert len(DETERMINISTIC_DIRTY_FUZZ_CASES) == 100


RESP_PERTURBATION_KINDS = (
    "ordered",
    "reordered",
    "duplicate",
    "missing",
    "leading_noise",
    "trailing_noise",
    "partial",
    "mixed_error",
)

RESP_PERTURBATION_CASES = tuple(
    (f"{kind}_{variant:02d}", kind, variant)
    for kind in RESP_PERTURBATION_KINDS
    for variant in range(1, 6)
)

assert len(RESP_PERTURBATION_CASES) == 40


AGGRESSIVE_RESP_PERTURBATION_KINDS = (
    "emptyish",
    "short",
    "partial_open",
    "partial_close",
    "duplicate",
    "reordered",
    "wrong_id",
    "noise",
    "illegal_close",
    "alternating_error",
)

AGGRESSIVE_RESP_PERTURBATION_CASES = tuple(
    (f"{kind}_{variant:02d}", kind, variant)
    for kind in AGGRESSIVE_RESP_PERTURBATION_KINDS
    for variant in range(1, 5)
)

assert len(AGGRESSIVE_RESP_PERTURBATION_CASES) == 40


@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_dump_flags_fuzz_seeded_markdown_constructs_keep_debug_artifacts_valid(
    tmp_path: Path, monkeypatch, seed: int
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    debug_dir = tmp_path / "debug"
    input_dir.mkdir()
    output_dir.mkdir()
    debug_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text(_build_seeded_fuzz_document(seed), encoding="utf-8")
    config_path = _write_config(tmp_path)

    monkeypatch.setattr("httpx.AsyncClient.send", _fake_openai_send)
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        _identity_format_markdown,
    )

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
            "--debug-dir",
            str(debug_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-requests-log",
            "--dump-nodes",
            "--dump-protected",
            "--dump-placeholders",
        ],
    )

    assert result.exit_code == 0, result.output
    _assert_dump_artifacts_black_box(debug_dir, output_dir)


@pytest.mark.parametrize("case_name,case_body", DETERMINISTIC_DIRTY_FUZZ_CASES)
def test_dump_flags_deterministic_dirty_fuzz_cases_keep_debug_artifacts_valid(
    tmp_path: Path, monkeypatch, case_name: str, case_body: str
) -> None:
    _ = case_name
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    debug_dir = tmp_path / "debug"
    input_dir.mkdir()
    output_dir.mkdir()
    debug_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text(
        f"# Dirty fuzz\n\n{case_body}\n",
        encoding="utf-8",
    )
    config_path = _write_config(tmp_path)

    monkeypatch.setattr("httpx.AsyncClient.send", _fake_openai_send)
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        _identity_format_markdown,
    )

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
            "--debug-dir",
            str(debug_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-requests-log",
            "--dump-nodes",
            "--dump-protected",
            "--dump-placeholders",
        ],
    )

    assert result.exit_code == 0, result.output
    _assert_dump_artifacts_black_box(debug_dir, output_dir)


@pytest.mark.parametrize("case_name,kind,variant", RESP_PERTURBATION_CASES)
def test_dump_flags_response_perturbation_fuzz_cases_keep_debug_artifacts_valid(
    tmp_path: Path,
    monkeypatch,
    case_name: str,
    kind: str,
    variant: int,
) -> None:
    _ = case_name
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    debug_dir = tmp_path / "debug"
    input_dir.mkdir()
    output_dir.mkdir()
    debug_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text(_build_seeded_fuzz_document(17), encoding="utf-8")
    config_path = _write_config(tmp_path)

    response_text = _build_response_perturbation_content(kind, variant)
    monkeypatch.setattr(
        "httpx.AsyncClient.send",
        _fake_openai_send_with_perturbation(
            response_text,
            include_error_field=kind == "mixed_error",
        ),
    )
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        _identity_format_markdown,
    )

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
            "--debug-dir",
            str(debug_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-requests-log",
            "--dump-nodes",
            "--dump-protected",
            "--dump-placeholders",
        ],
    )

    assert result.exit_code == 0, result.output
    _assert_dump_artifacts_black_box(debug_dir, output_dir)


def _build_aggressive_response_perturbation_content(kind: str, variant: int) -> str:
    valid_blocks = [
        f"<NODE_START_{index:04d}>\n已翻译文本 aggressive {kind} {variant} {index}\n</NODE_END_{index:04d}>\n"
        for index in range(3)
    ]

    if kind == "emptyish":
        return ["", " ", "\n\n", "\t \n"][variant - 1]
    if kind == "short":
        return ["OK", ".", "[]", "null"][variant - 1]
    if kind == "partial_open":
        return (
            f"<NODE_START_{variant:04d}>\n已翻译文本 partial-open {variant}\n"
            + valid_blocks[1]
            + valid_blocks[2]
        )
    if kind == "partial_close":
        return (
            valid_blocks[0]
            + f"已翻译文本 partial-close {variant}\n</NODE_END_{variant:04d}>\n"
            + valid_blocks[2]
        )
    if kind == "duplicate":
        return "".join([valid_blocks[0], valid_blocks[1], valid_blocks[1], valid_blocks[2]])
    if kind == "reordered":
        return "".join([valid_blocks[2], valid_blocks[0], valid_blocks[1]])
    if kind == "wrong_id":
        return (
            f"<NODE_START_{9000 + variant:04d}>\n已翻译文本 wrong-id {variant}\n"
            f"</NODE_END_{8000 + variant:04d}>\n"
            + valid_blocks[1]
        )
    if kind == "noise":
        long_noise = "".join(
            f"噪声-{variant}-{chunk}-" + ("x" * 90) + "\n"
            for chunk in range(1, 8 + variant)
        )
        return (
            f"前置无关文本 {variant}\n\n"
            + valid_blocks[0]
            + f"\n\n{long_noise}\n\n"
            + valid_blocks[2]
            + f"\n\n尾部无关文本 {variant}\n"
        )
    if kind == "illegal_close":
        return (
            f"</NODE_END_{variant:04d}>\n"
            + valid_blocks[0]
            + f"\n</NODE_END_{variant + 1000:04d}>\n"
            + valid_blocks[2]
        )
    if kind == "alternating_error":
        return (
            f"ERROR: upstream timeout {variant}\n\n"
            + valid_blocks[0]
            + f"\n\n{{\"error\":{{\"message\":\"still failing {variant}\"}}}}\n\n"
            + valid_blocks[1]
            + f"\n\nERROR: recoverable {variant}\n\n"
            + valid_blocks[2]
        )
    raise ValueError(kind)


@pytest.mark.parametrize("case_name,kind,variant", AGGRESSIVE_RESP_PERTURBATION_CASES)
def test_dump_flags_aggressive_response_perturbation_fuzz_cases_keep_debug_artifacts_valid(
    tmp_path: Path,
    monkeypatch,
    case_name: str,
    kind: str,
    variant: int,
) -> None:
    _ = case_name
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "out"
    debug_dir = tmp_path / "debug"
    input_dir.mkdir()
    output_dir.mkdir()
    debug_dir.mkdir()
    source_path = input_dir / "doc.md"
    source_path.write_text(_build_seeded_fuzz_document(71), encoding="utf-8")
    config_path = _write_config(tmp_path)

    response_text = _build_aggressive_response_perturbation_content(kind, variant)
    monkeypatch.setattr(
        "httpx.AsyncClient.send",
        _fake_openai_send_with_perturbation(
            response_text,
            include_error_field=kind == "alternating_error",
        ),
    )
    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._format_markdown",
        _identity_format_markdown,
    )

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
            "--debug-dir",
            str(debug_dir),
            "--model",
            "openai/gpt-4.1",
            "--dump-requests-log",
            "--dump-nodes",
            "--dump-protected",
            "--dump-placeholders",
        ],
    )

    assert result.exit_code == 0, result.output
    _assert_dump_artifacts_black_box(debug_dir, output_dir)
