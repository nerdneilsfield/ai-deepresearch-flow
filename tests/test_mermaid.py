import asyncio
from dataclasses import dataclass

import pytest

from deepresearch_flow.recognize import mermaid

@dataclass(frozen=True)
class MermaidCleanupSeed:
    kind: str
    original: str
    expected: str
    seed_id: str


MERMAID_CLEANUP_SEEDS = [
    MermaidCleanupSeed(
        kind="pass",
        original=(
            "flowchart LR\n"
            "B[模型训练] --> B1[\"训练集：KITTI 00序列库帧<br/>"
            "损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)\"]\n"
            "B --> B2[对比方法]\n"
        ),
        expected=(
            "flowchart LR\n"
            "B[模型训练] --> B1[\"训练集：KITTI 00序列库帧<br/>"
            "损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)\"]\n"
            "B --> B2[对比方法]\n"
        ),
        seed_id="quoted-html-label-with-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["区间[-π, π)"] --> B["ok"]\n',
        expected='flowchart LR\nA["区间[-π, π)"] --> B["ok"]\n',
        seed_id="quoted-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["中括号[abc]"] --> B["ok"]\n',
        expected='flowchart LR\nA["中括号[abc]"] --> B["ok"]\n',
        seed_id="quoted-square-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["a|b|c"] --> B["ok"]\n',
        expected='flowchart LR\nA["a|b|c"] --> B["ok"]\n',
        seed_id="pipes",
    ),
    MermaidCleanupSeed(
        kind="repair",
        original='flowchart LR\nA["He said "hi""] --> B["ok"]\n',
        expected='flowchart LR\nA["He said \'hi\'"] --> B["ok"]\n',
        seed_id="inner-double-quotes",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["路径/斜杠\\反斜杠"] --> B["ok"]\n',
        expected='flowchart LR\nA["路径/斜杠\\反斜杠"] --> B["ok"]\n',
        seed_id="slashes",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["100% & <tag>"] --> B["ok"]\n',
        expected='flowchart LR\nA["100% & <tag>"] --> B["ok"]\n',
        seed_id="percent-amp-angle-brackets",
    ),
    MermaidCleanupSeed(
        kind="repair",
        original='flowchart LR\nA["x"]B --> C["y"]\n',
        expected='flowchart LR\nA["x"]\nB --> C["y"]',
        seed_id="compacted-statement",
    ),
    MermaidCleanupSeed(
        kind="repair",
        original="flowchart LR\nB[数据增强：z轴随机旋转[-π, π)] --> C[ok]\n",
        expected='flowchart LR\nB["数据增强：z轴随机旋转[-π, π)]"] --> C[ok]',
        seed_id="single-unquoted-nested-bracket-label",
    ),
    MermaidCleanupSeed(
        kind="repair",
        original="flowchart LR\nA[区间[-π, π)] --> B[ok]\n",
        expected='flowchart LR\nA["区间[-π, π)]"] --> B[ok]',
        seed_id="interval-brackets",
    ),
    MermaidCleanupSeed(
        kind="repair",
        original="flowchart LR\nA[中括号[abc]] --> B[ok]\n",
        expected='flowchart LR\nA["中括号[abc]"] --> B[ok]',
        seed_id="nested-square-brackets",
    ),
]

MERMAID_IDEMPOTENT_REPAIR_INPUTS = [
    'flowchart LR\nA["x"]B --> C["y"]\n',
    "flowchart LR\nA[中括号[abc]] --> B[ok]\n",
    'flowchart LR\nA["He said "hi""] --> B["ok"]\n',
    'flowchart LR\nA["a<br>b"]B --> C["ok"]\n',
]


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(seed, id=f"{seed.kind}:{seed.seed_id}")
        for seed in MERMAID_CLEANUP_SEEDS
    ],
)
def test_cleanup_mermaid_seed_table(seed: MermaidCleanupSeed) -> None:
    cleaned = mermaid.cleanup_mermaid(seed.original)

    assert cleaned.rstrip("\n") == seed.expected.rstrip("\n")


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


@pytest.mark.parametrize("break_tag", ["<br>", "<br/>", "<br />"])
def test_cleanup_mermaid_preserves_html_break_label_variants(break_tag: str) -> None:
    original = f'flowchart LR\nA["a{break_tag}b"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize("original", MERMAID_IDEMPOTENT_REPAIR_INPUTS)
def test_cleanup_mermaid_is_idempotent_across_seed_repairs(original: str) -> None:
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
