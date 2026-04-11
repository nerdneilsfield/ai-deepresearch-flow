import asyncio

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
