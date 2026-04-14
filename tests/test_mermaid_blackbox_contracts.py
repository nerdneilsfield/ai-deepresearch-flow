from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.recognize import mermaid


def _make_span(text: str) -> mermaid.MermaidSpan:
    return mermaid.MermaidSpan(
        start=0,
        end=len(text),
        content=text,
        line=1,
        context="",
    )


def _run_fix(
    *,
    text: str,
    monkeypatch: pytest.MonkeyPatch,
    repaired_text: str | None = None,
    validate_side_effect=None,
    repair_side_effect=None,
) -> tuple[str, list[dict[str, object]], mermaid.MermaidFixStats]:
    async def fake_repair_batch(*_args, **_kwargs):
        if repair_side_effect is not None:
            raise repair_side_effect
        assert repaired_text is not None
        return {"abc:0": repaired_text}, None

    def fake_validate_mermaid(value: str) -> str | None:
        if validate_side_effect is not None:
            return validate_side_effect(value)
        if repaired_text is not None and value.rstrip("\n") == repaired_text.rstrip("\n"):
            return None
        return "parse error"

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")

    stats = mermaid.MermaidFixStats()
    span = _make_span(text)

    updated_text, error_records = asyncio.run(
        mermaid.fix_mermaid_text(
            text=text,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=object(),  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=1,
            context_chars=0,
            client=object(),  # type: ignore[arg-type]
            stats=stats,
            spans=[span],
        )
    )

    return updated_text, error_records, stats


@pytest.mark.parametrize(
    "original",
    [
        'flowchart LR\nA["区间[-π, π)"] --> B["ok"]\n',
        'flowchart TB\nA["a<br>b"] --> B["ok"]\n',
        'flowchart TD\nA["a|b|c"] --> B["ok"]\n',
        'flowchart RL\nA["100% & <tag>"] --> B["ok"]\n',
    ],
)
def test_cleanup_mermaid_keeps_valid_quoted_labels_and_direction_unchanged(original: str) -> None:
    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize("break_tag", ["<br>", "<br/>", "<br />"])
def test_cleanup_mermaid_preserves_valid_html_break_variants(break_tag: str) -> None:
    original = f'flowchart LR\nA["a{break_tag}b"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "flowchart LR\nA[区间[-π, π)] --> B[ok]\n",
            'flowchart LR\nA["区间[-π, π)]"] --> B[ok]',
        ),
        (
            "flowchart LR\nA[中括号[abc]] --> B[ok]\n",
            'flowchart LR\nA["中括号[abc]"] --> B[ok]',
        ),
        (
            'flowchart LR\nA["x"]B --> C["y"]\n',
            'flowchart LR\nA["x"]\nB --> C["y"]',
        ),
    ],
)
def test_cleanup_mermaid_minimally_repairs_broken_structure(
    original: str, expected: str
) -> None:
    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == expected.rstrip("\n")


def test_cleanup_mermaid_preserves_semantics_for_inner_quotes_and_nested_brackets() -> None:
    original = 'flowchart LR\nA["He said "hi" [abc]"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned != original
    assert "He said" in cleaned
    assert "hi" in cleaned
    assert "[abc]" in cleaned or "&#91;abc&#93;" in cleaned
    assert '--> B["ok"]' in cleaned


def test_cleanup_mermaid_handles_multiple_compacted_statements_in_one_snippet() -> None:
    original = 'flowchart LR\nA["x"]B --> C["y"]D --> E["z"]\n'
    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.count('A["x"]') == 1
    assert cleaned.count('C["y"]') == 1
    assert cleaned.count('E["z"]') == 1
    assert "A[\"x\"]B -->" not in cleaned
    assert "C[\"y\"]D -->" not in cleaned


def test_cleanup_mermaid_normalizes_crlf_to_valid_output() -> None:
    original = 'flowchart LR\r\nA["x"]\r\nB --> C["y"]\r\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert "\r" not in cleaned
    assert 'A["x"]' in cleaned
    assert 'B --> C["y"]' in cleaned


def test_cleanup_mermaid_handles_crlf_and_compacted_statement_together() -> None:
    original = 'flowchart TD\r\nA["x"]B --> C["y"]\r\nD --> E\r\n'
    cleaned = mermaid.cleanup_mermaid(original)

    assert "\r" not in cleaned
    assert cleaned.startswith("flowchart TD\n")
    assert 'A["x"]' in cleaned
    assert 'B --> C["y"]' in cleaned
    assert 'D --> E' in cleaned


def test_cleanup_mermaid_repairs_nested_brackets_with_inner_quotes_without_fixating_escape_style() -> None:
    original = 'flowchart LR\nA[outer[inner "quoted"] text] --> B[ok]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned != original
    assert cleaned.startswith("flowchart LR\nA[")
    assert "outer" in cleaned
    assert "inner" in cleaned
    assert "quoted" in cleaned
    assert 'B[ok]' in cleaned


def test_fix_mermaid_text_accepts_valid_repair_and_preserves_unrelated_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        "flowchart LR\n"
        'A[区间[-π, π)] --> B["ok"]\n'
        "D --> E\n"
    )
    repaired = (
        "flowchart LR\n"
        'A["区间[-π, π)]"] --> B["ok"]\n'
        "D --> E\n"
    )

    updated_text, error_records, stats = _run_fix(
        text=original,
        repaired_text=repaired,
        monkeypatch=monkeypatch,
    )

    assert updated_text.rstrip("\n") == repaired.rstrip("\n")
    assert "D --> E" in updated_text
    assert error_records == []
    assert stats.diagrams_failed == 0


def test_fix_mermaid_text_repairs_one_statement_among_multiple_valid_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        "flowchart LR\n"
        'A["stable"] --> B["ok"]\n'
        "C[区间[-π, π)] --> D[ok]\n"
        'E["stable"] --> F["ok"]\n'
    )
    repaired = (
        "flowchart LR\n"
        'A["stable"] --> B["ok"]\n'
        'C["区间[-π, π)]"] --> D[ok]\n'
        'E["stable"] --> F["ok"]\n'
    )

    updated_text, error_records, stats = _run_fix(
        text=original,
        repaired_text=repaired,
        monkeypatch=monkeypatch,
    )

    assert updated_text.rstrip("\n") == repaired.rstrip("\n")
    assert 'A["stable"] --> B["ok"]' in updated_text
    assert 'E["stable"] --> F["ok"]' in updated_text
    assert error_records == []
    assert stats.diagrams_failed == 0


def test_fix_mermaid_text_keeps_direction_when_repairing_non_lr_flowchart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        "flowchart TB\n"
        'A["stable"] --> B["ok"]\n'
        "C[区间[-π, π)] --> D[ok]\n"
    )
    repaired = (
        "flowchart TB\n"
        'A["stable"] --> B["ok"]\n'
        'C["区间[-π, π)]"] --> D[ok]\n'
    )

    updated_text, error_records, stats = _run_fix(
        text=original,
        repaired_text=repaired,
        monkeypatch=monkeypatch,
    )

    assert updated_text.startswith("flowchart TB\n")
    assert updated_text.rstrip("\n") == repaired.rstrip("\n")
    assert error_records == []
    assert stats.diagrams_failed == 0


def test_fix_mermaid_text_keeps_original_when_repair_batch_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = 'flowchart LR\nA["x"]B --> C["y"]\n'

    updated_text, error_records, stats = _run_fix(
        text=original,
        monkeypatch=monkeypatch,
        repair_side_effect=asyncio.CancelledError("cancelled"),
    )

    assert updated_text == original
    assert len(error_records) == 1
    assert error_records[0]["path"] == "demo.md"
    assert error_records[0]["mermaid"] == original
    assert stats.diagrams_failed == 1
    assert stats.diagrams_repaired == 0


def test_fix_mermaid_text_rejects_invalid_repaired_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = 'flowchart LR\nA["x"]B --> C["y"]\n'
    invalid_repaired = 'flowchart LR\nA["x"]"] --> C["y"]\n'

    def validate(value: str) -> str | None:
        if value.rstrip("\n") == invalid_repaired.rstrip("\n"):
            return "still invalid"
        return "parse error"

    updated_text, error_records, stats = _run_fix(
        text=original,
        monkeypatch=monkeypatch,
        repaired_text=invalid_repaired,
        validate_side_effect=validate,
    )

    assert updated_text == original
    assert len(error_records) == 1
    assert error_records[0]["mermaid"] == original
    assert stats.diagrams_failed == 1
    assert stats.diagrams_repaired == 0


def test_fix_mermaid_text_rejects_partially_mutated_repaired_output_with_mixed_valid_and_broken_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        "flowchart LR\n"
        'A["stable"] --> B["ok"]\n'
        'C["x"]B --> D["y"]\n'
        'E["stable"] --> F["ok"]\n'
    )
    invalid_repaired = (
        "flowchart LR\n"
        'A["stable"] --> B["ok"]\n'
        'C["x"]\n'
        'B --> D["y"]\n'
        'E["stable"] --> F["ok"]\n'
    )

    def validate(value: str) -> str | None:
        if value.rstrip("\n") == invalid_repaired.rstrip("\n"):
            return "still invalid"
        return "parse error"

    updated_text, error_records, stats = _run_fix(
        text=original,
        monkeypatch=monkeypatch,
        repaired_text=invalid_repaired,
        validate_side_effect=validate,
    )

    assert updated_text == original
    assert len(error_records) == 1
    assert error_records[0]["mermaid"] == original
    assert stats.diagrams_failed == 1
    assert stats.diagrams_repaired == 0
