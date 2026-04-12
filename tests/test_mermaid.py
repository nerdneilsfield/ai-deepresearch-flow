import asyncio

import pytest

from deepresearch_flow.recognize import mermaid


def test_cleanup_mermaid_preserves_quoted_html_labels_with_brackets() -> None:
    original = (
        "flowchart LR\n"
        "B[模型训练] --> B1[\"训练集：KITTI 00序列库帧<br/>"
        "损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)\"]\n"
        "B --> B2[对比方法]\n"
    )

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
    ("label", "expected_fragment"),
    [
        ("区间[-π, π)", 'A["区间[-π, π)"] --> B["ok"]'),
        ("中括号[abc]", 'A["中括号[abc]"] --> B["ok"]'),
        ("a|b|c", 'A["a|b|c"] --> B["ok"]'),
    ],
)
def test_cleanup_mermaid_preserves_quoted_special_character_labels(
    label: str, expected_fragment: str
) -> None:
    original = f'flowchart LR\nA["{label}"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert expected_fragment in cleaned
    assert cleaned.rstrip("\n") == original.rstrip("\n")


def test_cleanup_mermaid_repairs_compacted_statement_boundary() -> None:
    original = 'flowchart LR\nA["x"]B --> C["y"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned == 'flowchart LR\nA["x"]\nB --> C["y"]'


def test_cleanup_mermaid_repairs_unquoted_labels_with_nested_brackets() -> None:
    original = "flowchart LR\nB[数据增强：z轴随机旋转[-π, π)] --> C[ok]\n"

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned == 'flowchart LR\nB["数据增强：z轴随机旋转[-π, π)]"] --> C[ok]'


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
