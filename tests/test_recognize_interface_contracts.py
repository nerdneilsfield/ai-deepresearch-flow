from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
import re

import pytest

from deepresearch_flow.recognize import math, mermaid


def _repair_count(stats: object) -> int:
    if hasattr(stats, "formulas_repaired"):
        return getattr(stats, "formulas_repaired")
    return getattr(stats, "diagrams_repaired")


def _failure_count(stats: object) -> int:
    if hasattr(stats, "formulas_failed"):
        return getattr(stats, "formulas_failed")
    return getattr(stats, "diagrams_failed")


@dataclass(frozen=True)
class FixMathFuzzSpan:
    text: str
    delimiter: str
    line: int
    status: str
    expected_replacement: str | None = None


@dataclass(frozen=True)
class FixMathFuzzCase:
    text: str
    spans: list[math.FormulaSpan]
    allowed_keys: set[tuple[int, str | None, int | None]] | None
    expected_text: str
    expected_repaired: int
    expected_failed: int
    repair_enabled: bool
    repair_map: dict[str, str]
    validation_errors: dict[str, list[str]]
    note: str


def _build_fix_math_fuzz_cases(count: int = 120, seed: int = 20240415) -> list[FixMathFuzzCase]:
    rng = random.Random(seed)
    prose_pool = [
        "intro prose stays untouched",
        "middle prose with punctuation, commas, and digits 123",
        "tail prose keeps mixed content around the formulas",
        "placeholder contamination __PH_AUTOLINK_000106__ should stay as prose",
        "JSON escape damage motif: x\\nabla x\\rho x\\tab x\\form",
        "cases/aligned damage motif: \\ begin{cases} and \\ end{aligned}",
    ]
    cases: list[FixMathFuzzCase] = []

    def make_pass_formula(case_id: int, slot: int) -> tuple[str, str]:
        if slot % 3 == 0:
            content = f"x_{case_id}_{slot}+y_{case_id}_{slot}"
            return "$", content
        if slot % 3 == 1:
            content = f"\\alpha_{case_id}_{slot}+\\beta_{case_id}_{slot}"
            return "$", content
        content = f"\\begin{{cases}}x_{case_id}_{slot}&=z_{case_id}_{slot}\\end{{cases}}"
        return "$", content

    def make_repair_formula(case_id: int, slot: int) -> tuple[str, str, str]:
        if slot % 3 == 0:
            original = f"\\begin{{cases}}x_{case_id}_{slot}&=y_{case_id}_{slot}"
            repaired = f"\\begin{{cases}}x_{case_id}_{slot}&=y_{case_id}_{slot}\\end{{cases}}"
            return "$", original, repaired
        if slot % 3 == 1:
            original = f"x_{case_id}_{slot}+y_{case_id}_{slot}+BROKEN"
            repaired = f"x_{case_id}_{slot}+y_{case_id}_{slot}"
            return "$", original, repaired
        original = f"\\begin{{aligned}}a_{case_id}_{slot}&=b_{case_id}_{slot}"
        repaired = f"\\begin{{aligned}}a_{case_id}_{slot}&=b_{case_id}_{slot}\\end{{aligned}}"
        return "$", original, repaired

    def make_reject_formula(case_id: int, slot: int) -> tuple[str, str]:
        if slot % 3 == 0:
            return "$", f"x\\nabla_{case_id}_{slot}"
        if slot % 3 == 1:
            return "$", f"__PH_AUTOLINK_{case_id:06d}__"
        return (
            "$",
            f"Downloaded on March 30, 2026 at 20:06:42 UTC from IEEE Xplore {case_id}_{slot}",
        )

    for case_id in range(count):
        lines = [prose_pool[case_id % len(prose_pool)]]
        formula_specs: list[tuple[str, str, str, str | None]] = []
        repair_map: dict[str, str] = {}
        validation_errors: dict[str, list[str]] = {}
        repair_enabled = case_id % 5 != 0

        mode = rng.choice(["pass", "repair", "reclassify", "reject", "mixed"])
        span_total = 1 + (case_id % 3)
        for slot in range(span_total):
            if mode == "pass":
                delimiter, content = make_pass_formula(case_id, slot)
                lines.append(f"Before {delimiter}{content}{delimiter} after")
                formula_specs.append((delimiter, content, "pass", None))
                validation_errors[content] = []
                continue

            if mode == "repair":
                delimiter, original, repaired = make_repair_formula(case_id, slot)
                lines.append(f"Before {delimiter}{original}{delimiter} after")
                formula_specs.append((delimiter, original, "repair", repaired))
                repair_map[original] = repaired
                validation_errors[original] = ["invalid"]
                continue

            if mode == "reclassify":
                delimiter, content = make_pass_formula(case_id, slot)
                lines.append(f"Before {delimiter}{content}{delimiter} after")
                formula_specs.append((delimiter, content, "reclassify", None))
                validation_errors[content] = ["invalid"]
                continue

            if mode == "reject":
                delimiter, content = make_reject_formula(case_id, slot)
                lines.append(f"Before {delimiter}{content}{delimiter} after")
                formula_specs.append((delimiter, content, "reject", None))
                validation_errors[content] = ["invalid"]
                continue

            delimiter, content = make_pass_formula(case_id, slot)
            lines.append(f"Before {delimiter}{content}{delimiter} after")
            formula_specs.append((delimiter, content, "pass", None))
            validation_errors[content] = []

        tail = prose_pool[(case_id + 1) % len(prose_pool)]
        lines.append(tail)
        text = "\n".join(lines)
        spans: list[math.FormulaSpan] = []
        spec_rows: list[tuple[str, str, str, str | None, int]] = []
        allowed_keys: set[tuple[int, str | None, int | None]] = set()
        for delimiter, content, status, repaired in formula_specs:
            wrapped = f"{delimiter}{content}{delimiter}"
            start = text.index(wrapped)
            line_no = text.count("\n", 0, start) + 1
            spec_rows.append((delimiter, content, status, repaired, line_no))
            spans.append(
                math.FormulaSpan(
                    start=start,
                    end=start + len(wrapped),
                    delimiter=delimiter,
                    content=content,
                    line=line_no,
                    context="",
                )
            )
            if status in {"pass", "repair", "reject"} and rng.random() < 0.85:
                allowed_keys.add((line_no, None, None))
            if status == "reclassify":
                continue
            if status == "repair" and repaired is not None:
                repair_map[content] = repaired
            validation_errors[content] = [] if status == "pass" else ["invalid"]

        final_allowed_keys = allowed_keys if rng.random() < 0.75 else None
        if final_allowed_keys is not None and not final_allowed_keys:
            final_allowed_keys = None
        allowed_specs = [
            (delimiter, content, status, repaired, line_no)
            for delimiter, content, status, repaired, line_no in spec_rows
            if final_allowed_keys is None or (line_no, None, None) in final_allowed_keys
        ]
        if not repair_enabled:
            expected_text = text
        else:
            expected_text = text
            for delimiter, content, status, repaired, _line_no in allowed_specs:
                if status == "repair" and repaired is not None:
                    expected_text = expected_text.replace(
                        f"{delimiter}{content}{delimiter}",
                        f"{delimiter}{repaired}{delimiter}",
                    )

        expected_repaired = sum(
            1
            for _delimiter, _content, status, _repaired, _line_no in allowed_specs
            if status == "repair" and repair_enabled
        )
        expected_failed = sum(
            1
            for _delimiter, _content, status, _repaired, _line_no in allowed_specs
            if status in {"reject", "reclassify"} or (status == "repair" and not repair_enabled)
        )

        cases.append(
            FixMathFuzzCase(
                text=text,
                spans=spans,
                allowed_keys=final_allowed_keys,
                expected_text=expected_text,
                expected_repaired=expected_repaired,
                expected_failed=expected_failed,
                repair_enabled=repair_enabled,
                repair_map=repair_map,
                validation_errors=validation_errors,
                note=f"{mode}-{case_id}",
            )
        )

    return cases


FIX_MATH_FUZZ_CASES = _build_fix_math_fuzz_cases()


def test_fix_math_text_mixed_span_outcomes_preserve_surrounding_text(monkeypatch) -> None:
    original = "head $$bad1$$ middle $$bad2$$ tail"
    repaired_one = "x+y"
    repaired_two = "still broken"

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired_one, "abc:1": repaired_two}, None

    def fake_validate_formula(text: str, _display_mode: bool) -> list[str]:
        if text in {"bad1", "bad2"}:
            return ["invalid"]
        if text == repaired_one:
            return []
        if text == repaired_two:
            return ["invalid"]
        return []

    span1_start = original.index("$$bad1$$")
    span1_end = span1_start + len("$$bad1$$")
    span2_start = original.index("$$bad2$$")
    span2_end = span2_start + len("$$bad2$$")

    monkeypatch.setattr(math, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(math, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(math, "validate_formula", fake_validate_formula)

    stats = math.MathFixStats()
    updated, errors = asyncio.run(
        math.fix_math_text(
            text=original,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=object(),  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=2,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=[
                math.FormulaSpan(
                    start=span1_start,
                    end=span1_end,
                    delimiter="$$",
                    content="bad1",
                    line=1,
                    context="",
                ),
                math.FormulaSpan(
                    start=span2_start,
                    end=span2_end,
                    delimiter="$$",
                    content="bad2",
                    line=1,
                    context="",
                ),
            ],
        )
    )

    assert updated == "head $$x+y$$ middle $$bad2$$ tail"
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert errors[0]["line"] == 1
    assert errors[0]["field_path"] is None
    assert errors[0]["item_index"] is None
    assert errors[0]["errors"]
    assert updated.startswith("head ")
    assert " middle " in updated
    assert updated.endswith(" tail")
    assert _repair_count(stats) == 1
    assert _failure_count(stats) == 1


def test_fix_mermaid_text_mixed_span_outcomes_preserve_surrounding_text(monkeypatch) -> None:
    span1 = 'flowchart LR\nA["x"]"] --> C["y"]\n'
    span2 = 'flowchart LR\nB["u"]"] --> D["v"]\n'
    repaired_one = 'flowchart LR\nA["fixed"] --> B["ok"]\n'
    repaired_two = 'flowchart LR\nB["u"]"] --> D["v"]\n'
    original = f"intro\n{span1}between\n{span2}outro\n"

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired_one, "abc:1": repaired_two}, None

    def fake_validate_mermaid(text: str) -> str | None:
        normalized = text.rstrip("\n")
        if normalized in {original.rstrip("\n"), span1.rstrip("\n"), span2.rstrip("\n")}:
            return "invalid"
        if normalized == repaired_one.rstrip("\n"):
            return None
        if normalized == repaired_two.rstrip("\n"):
            return "invalid"
        if repaired_two.rstrip("\n") in normalized:
            return "invalid"
        if repaired_one.rstrip("\n") in normalized:
            return None
        return None

    span1_start = original.index(span1)
    span1_end = span1_start + len(span1)
    span2_start = original.index(span2)
    span2_end = span2_start + len(span2)

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)

    stats = mermaid.MermaidFixStats()
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
            batch_size=2,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=[
                mermaid.MermaidSpan(
                    start=span1_start,
                    end=span1_end,
                    content=span1,
                    line=2,
                    context="",
                ),
                mermaid.MermaidSpan(
                    start=span2_start,
                    end=span2_end,
                    content=span2,
                    line=4,
                    context="",
                ),
            ],
        )
    )

    assert updated.startswith("intro\n")
    assert "between\n" in updated
    assert updated.rstrip("\n").endswith("outro")
    assert repaired_one.rstrip("\n") in updated
    assert span2.rstrip("\n") in updated
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert errors[0]["line"] == 4
    assert errors[0]["field_path"] is None
    assert errors[0]["item_index"] is None
    assert errors[0]["errors"]
    assert _repair_count(stats) == 1
    assert _failure_count(stats) == 1


@pytest.mark.parametrize(
    ("module", "fix_fn", "span_factory", "stats_factory", "original", "repaired"),
    [
        pytest.param(
            math,
            math.fix_math_text,
            lambda text, start, end, line: math.FormulaSpan(
                start=start,
                end=end,
                delimiter="$$",
                content=text[start + 2 : end - 2],
                line=line,
                context="",
            ),
            math.MathFixStats,
            "lead $$bad1$$ center $$bad2$$ tail",
            "x+y",
            id="math",
        ),
        pytest.param(
            mermaid,
            mermaid.fix_mermaid_text,
            lambda text, start, end, line: mermaid.MermaidSpan(
                start=start,
                end=end,
                content=text[start:end],
                line=line,
                context="",
            ),
            mermaid.MermaidFixStats,
            'intro\nflowchart LR\nA["x"]"] --> C["y"]\nbetween\nflowchart LR\nB["u"]"] --> D["v"]\noutro\n',
            'flowchart LR\nA["fixed"] --> B["ok"]\n',
            id="mermaid",
        ),
    ],
)
def test_fix_text_handles_missing_repair_for_one_of_multiple_spans(
    monkeypatch,
    module,
    fix_fn,
    span_factory,
    stats_factory,
    original,
    repaired,
) -> None:
    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired}, None

    def fake_validate(text: str, _display_mode: bool | None = None):
        normalized = text.rstrip("\n")
        if module is math:
            if text in {"bad1", "bad2"}:
                return ["invalid"]
            if text == repaired:
                return []
            return []
        if normalized in {
            original.rstrip("\n"),
            'flowchart LR\nA["x"]"] --> C["y"]',
            'flowchart LR\nB["u"]"] --> D["v"]',
        }:
            return "invalid"
        if normalized == repaired.rstrip("\n"):
            return None
        return None

    if module is math:
        span1_start = original.index("$$bad1$$")
        span2_start = original.index("$$bad2$$")
        span1_end = span1_start + len("$$bad1$$")
        span2_end = span2_start + len("$$bad2$$")
        spans = [
            span_factory(original, span1_start, span1_end, 1),
            span_factory(original, span2_start, span2_end, 1),
        ]
        monkeypatch.setattr(math, "validate_formula", fake_validate)
    else:
        span1 = 'flowchart LR\nA["x"]"] --> C["y"]\n'
        span2 = 'flowchart LR\nB["u"]"] --> D["v"]\n'
        span1_start = original.index(span1)
        span2_start = original.index(span2)
        span1_end = span1_start + len(span1)
        span2_end = span2_start + len(span2)
        spans = [
            span_factory(original, span1_start, span1_end, 2),
            span_factory(original, span2_start, span2_end, 4),
        ]
        monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate)

    monkeypatch.setattr(module, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(module, "short_hash", lambda _path: "abc")

    stats = stats_factory()
    updated, errors = asyncio.run(
        fix_fn(
            text=original,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=None if module is mermaid else object(),  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=2,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=spans,
        )
    )

    if module is math:
        assert updated == "lead $$x+y$$ center $$bad2$$ tail"
        assert updated.startswith("lead ")
        assert " center " in updated
        assert updated.endswith(" tail")
    else:
        assert updated.startswith("intro\n")
        assert "between\n" in updated
        assert updated.rstrip("\n").endswith("outro")
        assert repaired.rstrip("\n") in updated
        assert span2.rstrip("\n") in updated

    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert errors[0]["line"] in {1, 4}
    assert errors[0]["field_path"] is None
    assert errors[0]["item_index"] is None
    assert errors[0]["errors"]
    assert _repair_count(stats) == 1
    assert _failure_count(stats) == 1


@pytest.mark.parametrize(
    ("module", "fix_fn", "span_factory", "stats_factory"),
    [
        pytest.param(
            math,
            math.fix_math_text,
            lambda text, start, end, line: math.FormulaSpan(
                start=start,
                end=end,
                delimiter="$$",
                content=text[start + 2 : end - 2],
                line=line,
                context="",
            ),
            math.MathFixStats,
            id="math",
        ),
        pytest.param(
            mermaid,
            mermaid.fix_mermaid_text,
            lambda text, start, end, line: mermaid.MermaidSpan(
                start=start,
                end=end,
                content=text[start:end],
                line=line,
                context="",
            ),
            mermaid.MermaidFixStats,
            id="mermaid",
        ),
    ],
)
def test_fix_text_rejects_invalid_repaired_output(
    monkeypatch, module, fix_fn, span_factory, stats_factory
) -> None:
    if module is math:
        original = "$$bad$$"
        repaired = "still broken"

        async def fake_repair_batch(*_args, **_kwargs):
            return {"abc:0": repaired}, None

        def fake_validate_formula(text: str, _display_mode: bool) -> list[str]:
            if text == "bad":
                return ["invalid"]
            if text == repaired:
                return ["invalid"]
            return []

        span = span_factory(original, 0, len(original), 1)
        monkeypatch.setattr(math, "validate_formula", fake_validate_formula)
    else:
        original = 'flowchart LR\nA["x"]"] --> C["y"]\n'
        repaired = 'flowchart LR\nA["fixed"] --> B["ok"]\n'

        async def fake_repair_batch(*_args, **_kwargs):
            return {"abc:0": repaired}, None

        def fake_validate_mermaid(text: str) -> str | None:
            normalized = text.rstrip("\n")
            if normalized == original.rstrip("\n"):
                return "invalid"
            if normalized == repaired.rstrip("\n"):
                return "invalid"
            return None

        span = span_factory(original, 0, len(original), 1)
        monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)

    monkeypatch.setattr(module, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(module, "short_hash", lambda _path: "abc")

    stats = stats_factory()
    updated, errors = asyncio.run(
        fix_fn(
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

    assert updated.rstrip("\n") == original.rstrip("\n")
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert errors[0]["line"] == 1
    assert errors[0]["field_path"] is None
    assert errors[0]["item_index"] is None
    assert errors[0]["errors"]
    assert _repair_count(stats) == 0
    assert _failure_count(stats) == 1


@pytest.mark.parametrize(
    ("module", "fix_fn", "span_factory", "stats_factory"),
    [
        pytest.param(
            math,
            math.fix_math_text,
            lambda text, start, end, line: math.FormulaSpan(
                start=start,
                end=end,
                delimiter="$$",
                content=text[start + 2 : end - 2],
                line=line,
                context="",
            ),
            math.MathFixStats,
            id="math",
        ),
        pytest.param(
            mermaid,
            mermaid.fix_mermaid_text,
            lambda text, start, end, line: mermaid.MermaidSpan(
                start=start,
                end=end,
                content=text[start:end],
                line=line,
                context="",
            ),
            mermaid.MermaidFixStats,
            id="mermaid",
        ),
    ],
)
def test_fix_text_returns_original_when_repair_batch_is_cancelled(
    monkeypatch, module, fix_fn, span_factory, stats_factory
) -> None:
    if module is math:
        original = "$$bad$$"

        async def fake_repair_batch(*_args, **_kwargs):
            raise asyncio.CancelledError("cancelled")

        def fake_validate_formula(text: str, _display_mode: bool) -> list[str]:
            return ["invalid"] if text == "bad" else []

        monkeypatch.setattr(math, "validate_formula", fake_validate_formula)
    else:
        original = 'flowchart LR\nA["x"]"] --> C["y"]\n'

        async def fake_repair_batch(*_args, **_kwargs):
            raise asyncio.CancelledError("cancelled")

        def fake_validate_mermaid(text: str) -> str | None:
            return "invalid" if text.rstrip("\n") == original.rstrip("\n") else None

        monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)

    monkeypatch.setattr(module, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(module, "short_hash", lambda _path: "abc")

    stats = stats_factory()
    updated, errors = asyncio.run(
        fix_fn(
            text=original,
            file_path="demo.md",
            line_offset=1,
            field_path=None,
            item_index=None,
            route_pool=object(),  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            batch_size=1,
            context_chars=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
            spans=[span_factory(original, 0, len(original), 1)],
        )
    )

    assert updated.rstrip("\n") == original.rstrip("\n")
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert errors[0]["line"] == 1
    assert errors[0]["field_path"] is None
    assert errors[0]["item_index"] is None
    assert errors[0]["errors"]
    assert _repair_count(stats) == 0
    assert _failure_count(stats) == 1


def test_fix_math_text_blackbox_fuzz_contracts(monkeypatch) -> None:
    assert len(FIX_MATH_FUZZ_CASES) >= 100

    monkeypatch.setattr(math, "short_hash", lambda _path: "abc")

    for idx, case in enumerate(FIX_MATH_FUZZ_CASES):

        def fake_validate_formula(text: str, _display_mode: bool) -> list[str]:
            return case.validation_errors.get(text, [])

        async def fake_repair_batch(*_args, **_kwargs):
            repairs: dict[str, str] = {}
            batch = _args[0]
            for issue in batch:
                repaired = case.repair_map.get(issue.span.content)
                if repaired is not None:
                    repairs[issue.issue_id] = repaired
            return repairs, None

        monkeypatch.setattr(math, "validate_formula", fake_validate_formula)
        monkeypatch.setattr(math, "repair_batch", fake_repair_batch)

        stats = math.MathFixStats()
        allowed_total = sum(
            1
            for span in case.spans
            if case.allowed_keys is None or (span.line, None, None) in case.allowed_keys
        )

        updated, errors = asyncio.run(
            math.fix_math_text(
                text=case.text,
                file_path="demo.md",
                line_offset=1,
                field_path=None,
                item_index=None,
                route_pool=object(),  # type: ignore[arg-type]
                timeout=1.0,
                max_retries=0,
                batch_size=2,
                context_chars=0,
                client=None,  # type: ignore[arg-type]
                stats=stats,
                repair_enabled=case.repair_enabled,
                spans=case.spans,
                allowed_keys=case.allowed_keys,
            )
        )

        assert updated == case.expected_text, f"case={idx} note={case.note}"
        assert allowed_total == stats.formulas_total
        assert stats.formulas_repaired == case.expected_repaired
        assert stats.formulas_failed == case.expected_failed
        assert len(errors) == case.expected_failed

        for error in errors:
            assert error["path"] == "demo.md"
            assert error["field_path"] is None
            assert error["item_index"] is None
            assert error["errors"]
