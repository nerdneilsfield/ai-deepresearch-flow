from __future__ import annotations

from pathlib import Path
import re

from click.testing import CliRunner

from deepresearch_flow.cli import cli
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.translator.engine import TranslationResult, TranslationStats
from deepresearch_flow.translator.placeholder import PlaceHolderStore


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
          { model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true },
          { model_name = "gpt-4.1-fallback", is_stream = true, is_support_json_schema = true, is_support_json_object = true }
        ]
        """,
        encoding="utf-8",
    )
    return config_path


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

    async def fake_translate(self, content, *args, **kwargs):
        if "bad content" in content:
            raise ProviderError("timeout", retryable=True)
        return TranslationResult(
            translated_text="translated good content",
            protected_text=content,
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

    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator.translate",
        fake_translate,
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
            r"(<NODE_START_\d{4}>\n)(.*?)(\n</NODE_END_\d{4}>)",
            r"\1已翻译文本\3",
            group_text,
            flags=re.DOTALL,
        )

    monkeypatch.setattr(
        "deepresearch_flow.translator.cli.MarkdownTranslator._translate_group",
        fake_translate_group,
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
