import asyncio

import pytest

from deepresearch_flow.recognize import mermaid


MERMAID_PASS_SEEDS = [
    pytest.param(
        (
            "flowchart LR\n"
            "B[模型训练] --> B1[\"训练集：KITTI 00序列库帧<br/>"
            "损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)\"]\n"
            "B --> B2[对比方法]\n"
        ),
        id="pass:quoted-html-label-with-brackets",
    ),
]


MERMAID_LABEL_PASS_OR_REPAIR_SEEDS = [
    pytest.param(
        "区间[-π, π)",
        'A["区间[-π, π)"] --> B["ok"]',
        "pass",
        id="pass:quoted-brackets",
    ),
    pytest.param(
        "中括号[abc]",
        'A["中括号[abc]"] --> B["ok"]',
        "pass",
        id="pass:quoted-square-brackets",
    ),
    pytest.param(
        "a|b|c",
        'A["a|b|c"] --> B["ok"]',
        "pass",
        id="pass:pipes",
    ),
    pytest.param(
        'He said "hi"',
        'A["He said \'hi\'"] --> B["ok"]',
        "repair",
        id="repair:inner-double-quotes",
    ),
    pytest.param(
        "路径/斜杠\\反斜杠",
        'A["路径/斜杠\\反斜杠"] --> B["ok"]',
        "pass",
        id="pass:slashes",
    ),
    pytest.param(
        "100% & <tag>",
        'A["100% & <tag>"] --> B["ok"]',
        "pass",
        id="pass:percent-amp-angle-brackets",
    ),
]


MERMAID_REPAIR_SEEDS = [
    pytest.param(
        'flowchart LR\nA["x"]B --> C["y"]\n',
        'flowchart LR\nA["x"]\nB --> C["y"]',
        id="repair:compacted-statement",
    ),
    pytest.param(
        "flowchart LR\nB[数据增强：z轴随机旋转[-π, π)] --> C[ok]\n",
        'flowchart LR\nB["数据增强：z轴随机旋转[-π, π)]"] --> C[ok]',
        id="repair:single-unquoted-nested-bracket-label",
    ),
    pytest.param(
        "flowchart LR\nA[区间[-π, π)] --> B[ok]\n",
        'flowchart LR\nA["区间[-π, π)]"] --> B[ok]',
        id="repair:interval-brackets",
    ),
    pytest.param(
        "flowchart LR\nA[中括号[abc]] --> B[ok]\n",
        'flowchart LR\nA["中括号[abc]"] --> B[ok]',
        id="repair:nested-square-brackets",
    ),
]

MERMAID_IDEMPOTENT_REPAIR_INPUTS = [
    'flowchart LR\nA["x"]B --> C["y"]\n',
    "flowchart LR\nA[中括号[abc]] --> B[ok]\n",
    'flowchart LR\nA["He said "hi""] --> B["ok"]\n',
    'flowchart LR\nA["a<br>b"]B --> C["ok"]\n',
]


@pytest.mark.parametrize("original", MERMAID_PASS_SEEDS)
def test_cleanup_mermaid_preserves_pass_seeds(original: str) -> None:
    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


def test_fix_mermaid_text_accepts_valid_repair_without_recleanup(monkeypatch) -> None:
    original = (
        "flowchart LR\n"
        "B[模型训练] --> B1[训练集：KITTI 00序列库帧<br>"
        "损失：懒三元组损失<br>数据增强：z轴随机旋转[-π,π)]\n"
        "B --> B2[对比方法]\n"
    )
    repaired = (
        "flowchart LR\n"
        "B[模型训练] --> B1[\"训练集：KITTI 00序列库帧<br/>"
        "损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)\"]\n"
        "B --> B2[对比方法]\n"
    )

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired}, None

    def fake_validate_mermaid(text: str) -> str | None:
        if text == original:
            return "parse error"
        if text == repaired or text == repaired.strip():
            return None
        return "unexpected mutation"

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)

    stats = mermaid.MermaidFixStats()
    span = mermaid.MermaidSpan(
        start=0,
        end=len(original),
        content=original,
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        mermaid.fix_mermaid_text(
            text=original,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=None,  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=1,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=[span],
        )
    )

    assert updated.rstrip("\n") == repaired.rstrip("\n")
    assert errors == []
    assert stats.diagrams_repaired == 1


@pytest.mark.parametrize(
    ("label", "expected_fragment", "classification"),
    MERMAID_LABEL_PASS_OR_REPAIR_SEEDS,
)
def test_cleanup_mermaid_preserves_quoted_special_character_labels(
    label: str, expected_fragment: str, classification: str
) -> None:
    original = f'flowchart LR\nA["{label}"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert expected_fragment in cleaned
    if classification == "pass":
        assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize("break_tag", ["<br>", "<br/>", "<br />"])
def test_cleanup_mermaid_preserves_html_break_label_variants(break_tag: str) -> None:
    original = f'flowchart LR\nA["a{break_tag}b"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize(("original", "expected"), MERMAID_REPAIR_SEEDS)
def test_cleanup_mermaid_repair_seeds(original: str, expected: str) -> None:
    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned == expected


@pytest.mark.parametrize("original", MERMAID_IDEMPOTENT_REPAIR_INPUTS)
def test_cleanup_mermaid_is_idempotent_across_seed_repairs(original: str) -> None:
    once = mermaid.cleanup_mermaid(original)
    twice = mermaid.cleanup_mermaid(once)

    assert twice == once


def test_cleanup_mermaid_is_idempotent_for_compacted_and_bracketed_labels() -> None:
    original = (
        "flowchart LR\n"
        'A["区间[-π, π)"]B --> C["ok"]\n'
    )

    once = mermaid.cleanup_mermaid(original)
    twice = mermaid.cleanup_mermaid(once)

    assert twice == once


def test_fix_mermaid_text_rejects_still_invalid_repair(monkeypatch) -> None:
    original = 'flowchart LR\nA["x"]"] --> C["y"]\n'
    repaired = 'flowchart LR\nA["x"]"] --> C["y"]\n'

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired}, None

    def fake_validate_mermaid(text: str) -> str | None:
        if text.rstrip("\n") == original.rstrip("\n"):
            return "still invalid"
        return None

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)
    monkeypatch.setattr(mermaid, "cleanup_mermaid", lambda text: text)

    stats = mermaid.MermaidFixStats()
    span = mermaid.MermaidSpan(
        start=0,
        end=len(original),
        content=original,
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        mermaid.fix_mermaid_text(
            text=original,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=None,  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=1,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=[span],
        )
    )

    assert updated == original
    assert len(errors) == 1
    assert errors[0]["errors"][-1] == "still invalid"
    assert stats.diagrams_failed == 1
