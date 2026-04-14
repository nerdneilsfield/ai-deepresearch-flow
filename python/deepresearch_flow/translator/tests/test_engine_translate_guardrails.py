from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
import re
import subprocess

import httpx
import pytest

from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.engine import MarkdownTranslator
from deepresearch_flow.translator.prompts import build_translation_messages


def _make_provider() -> SimpleNamespace:
    return SimpleNamespace(name="mock-provider", api_keys=[])


def _make_translator(**kwargs) -> MarkdownTranslator:
    cfg = TranslateConfig(
        retry_failed_nodes=kwargs.pop("retry_failed_nodes", False),
        retry_times=kwargs.pop("retry_times", 1),
        strict_placeholder_check=kwargs.pop("strict_placeholder_check", True),
        **kwargs,
    )
    return MarkdownTranslator(cfg)


async def _run_translate(
    translator: MarkdownTranslator,
    text: str,
):
    async with httpx.AsyncClient() as client:
        return await translator.translate(
            text=text,
            provider=_make_provider(),
            model="mock-model",
            client=client,
            api_keys=[],
            timeout=1.0,
            semaphore=asyncio.Semaphore(1),
            throttle=None,
            max_tokens=None,
            fix_level="off",
            progress=None,
            format_enabled=True,
            request_log=None,
            dump_callback=None,
            group_concurrency=1,
        )


def test_restored_protected_content_survives_post_processing(monkeypatch) -> None:
    translator = _make_translator()
    text = r"Before \(x + y\) after"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return group_text

    async def fake_format(self, content, stage):
        if stage == "post":
            return content.replace(r"\(x + y\)", r"\(BROKEN\)")
        return content

    def fake_normalize(self, content):
        return content.replace(r"\(x + y\)", r"\(BROKEN\)")

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)
    monkeypatch.setattr(
        MarkdownTranslator, "_normalize_markdown_blocks", fake_normalize
    )
    monkeypatch.setattr(MarkdownTranslator, "_collect_failed_nodes", lambda self, nodes: dict(nodes))

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text == text


def test_strict_placeholder_check_rejects_residual_placeholder_tokens(
    monkeypatch,
) -> None:
    translator = _make_translator(strict_placeholder_check=True)
    text = r"Before \(x + y\) after"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return group_text.replace("after", "after __PH_UNKNOWN_999999__")

    async def fake_format(self, content, stage):
        return content

    def fake_normalize(self, content):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)
    monkeypatch.setattr(
        MarkdownTranslator, "_normalize_markdown_blocks", fake_normalize
    )
    monkeypatch.setattr(MarkdownTranslator, "_collect_failed_nodes", lambda self, nodes: {})
    monkeypatch.setattr(
        MarkdownTranslator, "_align_placeholders", lambda self, orig, trans: trans
    )
    monkeypatch.setattr(
        MarkdownTranslator, "_fix_placeholder_typos", lambda self, text, valid: text
    )

    with pytest.raises(ValueError, match="unresolved placeholder"):
        asyncio.run(_run_translate(translator, text))


def test_failed_nodes_fall_back_to_origin_without_post_processing_mutation(
    monkeypatch,
) -> None:
    translator = _make_translator(retry_failed_nodes=False, retry_times=1)
    text = r"Before \(x + y\) after"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return re.sub(
            r"(<NODE_START_\d+>\n)(.*?)(\n</NODE_END_\d+>)",
            r"\1\3",
            group_text,
            flags=re.DOTALL,
        )

    async def fake_format(self, content, stage):
        if stage == "post":
            return content.replace(r"\(x + y\)", r"\(BROKEN\)")
        return content

    def fake_normalize(self, content):
        return content.replace(r"\(x + y\)", r"\(BROKEN\)")

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)
    monkeypatch.setattr(
        MarkdownTranslator, "_normalize_markdown_blocks", fake_normalize
    )
    monkeypatch.setattr(
        MarkdownTranslator, "_collect_failed_nodes", lambda self, nodes: dict(nodes)
    )

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text.strip() == text.strip()
    assert result.stats.failed_nodes == 1


def test_node_with_missing_footnote_placeholders_falls_back_to_original_text(
    monkeypatch,
) -> None:
    translator = _make_translator(retry_failed_nodes=False, retry_times=1)
    text = "Before[^1] middle[^2] after\n\n[^1]: First note\n\n[^2]: Second note\n"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return group_text.replace("__PH_FOOTREF_000004__ ", "").replace(
            " __PH_FOOTREF_000005__", ""
        )

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text.strip() == text.strip()
    assert result.stats.failed_nodes == 1


def test_node_with_missing_math_placeholders_falls_back_to_original_text(
    monkeypatch,
) -> None:
    translator = _make_translator(retry_failed_nodes=False, retry_times=1)
    text = "Before \\(x + y\\) and $z$ after\n"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return (
            group_text.replace("__PH_MATH_000001__", "")
            .replace("__PH_MATH_000002__", "")
            .replace("Before", "之前")
        )

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text.strip() == text.strip()
    assert result.stats.failed_nodes == 1


def test_translation_messages_include_placeholder_integrity_and_fallback_rules() -> None:
    messages = build_translation_messages("en", "zh", "@@NODE_START_0000@@\n__PH_FOOTREF_000001__\n@@NODE_END_0000@@")

    system_text = messages[0]["content"]
    user_text = messages[1]["content"]

    assert "multiset of placeholders" in system_text
    assert "If any placeholder would be lost or changed, output the ORIGINAL NODE CONTENT unchanged." in system_text
    assert "Footnote-reference placeholders such as __PH_FOOTREF_######__ are mandatory anchors" in user_text


def test_translated_headings_keep_original_levels_after_post_format(monkeypatch) -> None:
    translator = _make_translator()
    text = "# Title\n### I. INTRODUCTION\n### II. RELATED WORK\n"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return (
            "<NODE_START_0000>\n# 标题\n</NODE_END_0000>"
            "<NODE_START_0001>\n## I. 引言\n</NODE_END_0001>"
            "<NODE_START_0002>\n## II. 相关工作\n</NODE_END_0002>"
        )

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert "# 标题" in result.translated_text
    assert "### I. 引言" in result.translated_text
    assert "### II. 相关工作" in result.translated_text


def test_translate_handles_node_ids_beyond_four_digits(monkeypatch) -> None:
    translator = _make_translator(max_chunk_chars=10**9, retry_failed_nodes=False)
    text = "".join(f"# Heading {i}\n" for i in range(10005))

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return re.sub(
            r"(<NODE_START_\d+>\n)(.*?)(\n</NODE_END_\d+>)",
            lambda m: f"{m.group(1)}ZH: {m.group(2)}{m.group(3)}",
            group_text,
            flags=re.DOTALL,
        )

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert "ZH: # Heading 9999" in result.translated_text
    assert "ZH: # Heading 10000" in result.translated_text
    assert "ZH: # Heading 10004" in result.translated_text


def test_engine_format_markdown_returns_original_on_rumdl_timeout(monkeypatch) -> None:
    translator = _make_translator()
    translator._rumdl_path = "rumdl"

    def fake_run():
        raise subprocess.TimeoutExpired(cmd=["rumdl", "fmt"], timeout=5.0)

    monkeypatch.setattr(translator, "_rumdl_timeout_seconds", 5.0)
    monkeypatch.setattr("deepresearch_flow.translator.engine.subprocess.run", lambda *a, **k: fake_run())

    assert asyncio.run(translator._format_markdown("# Title\n", "post")) == "# Title\n"


def test_markdown_translator_respects_existing_httpx_httpcore_quiet_logging() -> None:
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
            logging.getLogger(name).setLevel(logging.WARNING)
        _make_translator()

        for name in quiet_names:
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        for name, level in old_levels.items():
            logging.getLogger(name).setLevel(level)


def test_preprocess_document_returns_expected_document_state() -> None:
    translator = _make_translator()
    text = "__PH_LINK_000001__\n"

    result = asyncio.run(
        translator.preprocess_document(
            text,
            fix_level="off",
            format_enabled=False,
        )
    )

    assert result.reference_text == text
    assert result.total_nodes == 1
    assert result.skip_count == 1
    assert result.initial_groups == []
    assert result.nodes[0].translated_text == text


def test_email_group_address_is_not_marked_failed_when_left_unchanged(monkeypatch) -> None:
    translator = _make_translator(retry_failed_nodes=False, retry_times=1, target_lang="zh")
    text = "{hanjiaming, jian.ding, xuenan, guisong.xia}@whu.edu.cn\n"

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return group_text

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text.strip() == text.strip()
    assert result.stats.failed_nodes == 0


@pytest.mark.parametrize(
    "text",
    [
        "{fanzhaoxin, luzhiwu, hejun}@ruc.edu.cn, __PH_AUTOLINK_000031__, __PH_AUTOLINK_000032__\n",
        "@inproceedings{segmatch2017,\n",
        "colset TH = product INT*INT*STRING*INT*INT;\n",
        "### A. 3D-RoFormer\n",
        "• FAST - ORB - DBoW2\n",
        r"104  $ \left|\begin{array}{c} \text{end} \\ \text{end} \\ \text{end}",
        "## CCS Concepts\n",
        "rsfs.royalsocietypublishing.org\n",
        "humidity: INT;\n",
        "~end pose\n",
        r"__PH_MATHBLOCK_000057__ \begin{aligned}h^{\prime}(t)&=\mathbf{A}h(t)+\mathbf{B}x(t)\end{aligned}",
    ],
)
def test_nontranslatable_structured_lines_are_not_marked_failed_when_left_unchanged(
    monkeypatch, text: str
) -> None:
    translator = _make_translator(retry_failed_nodes=False, retry_times=1, target_lang="zh")

    async def fake_translate_group(self, group_text, *args, **kwargs):
        return group_text

    async def fake_format(self, content, stage):
        return content

    monkeypatch.setattr(MarkdownTranslator, "_translate_group", fake_translate_group)
    monkeypatch.setattr(MarkdownTranslator, "_format_markdown", fake_format)

    result = asyncio.run(_run_translate(translator, text))

    assert result.translated_text.strip() == text.strip()
    assert result.stats.failed_nodes == 0
