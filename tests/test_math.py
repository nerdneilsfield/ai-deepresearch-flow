import asyncio

from deepresearch_flow.recognize import math


def test_cleanup_formula_preserves_cases_linebreaks() -> None:
    original = (
        r"f_{h}(\Delta h)=\begin{cases}"
        r"p_{h}, & \min\left(\Delta h,1-\Delta h\right)>t_{h}\\"
        r"0, & \text{otherwise}"
        r"\end{cases},"
    )

    cleaned = math.cleanup_formula(original)

    assert cleaned == original


def test_cleanup_formula_preserves_latex_commands_with_n_and_right() -> None:
    original = (
        r"f_{c}(c_{1},c_{2})=\begin{cases}"
        r"p_{c}, & c_{1}\neq c_{2}\\"
        r"0, & \text{otherwise}"
        r"\end{cases}."
    )

    cleaned = math.cleanup_formula(original)

    assert cleaned == original


def test_cleanup_formula_recovers_control_char_escaped_commands() -> None:
    broken = (
        "\\sigma_{v}(v_{i},v_{j})="
        "\begin{cases}"
        "\\exp\\left(-\\frac{\\delta(h_{i},h_{j})+\\delta(w_{i},w_{j})+"
        "\\delta(d_{i},d_{j})}{3}"
        "\\right), & "
        "\text{若 } l_{i}=l_{j}\\\\"
        "0, & "
        "\text{其他情况}"
        "\\end{cases}"
    )
    expected = (
        r"\sigma_{v}(v_{i},v_{j})=\begin{cases}"
        r"\exp\left(-\frac{\delta(h_{i},h_{j})+\delta(w_{i},w_{j})+"
        r"\delta(d_{i},d_{j})}{3}\right), & "
        r"\text{若 } l_{i}=l_{j}\\"
        r"0, & \text{其他情况}"
        r"\end{cases}"
    )

    cleaned = math.cleanup_formula(broken)

    assert cleaned == expected


def test_cleanup_formula_does_not_treat_line_breaks_as_control_commands() -> None:
    assert math.cleanup_formula("x\nabla") == "x\nabla"
    assert math.cleanup_formula("x\rho") == "x\rho"


def test_cleanup_formula_preserves_standard_uppercase_commands() -> None:
    original = r"\Rightarrow \Big \Re \Im"

    cleaned = math.cleanup_formula(original)

    assert cleaned == original


def test_cleanup_formula_handles_nested_braced_text_commands() -> None:
    original = r"\text{\textbf{t e r m}}"

    cleaned = math.cleanup_formula(original)

    assert cleaned == original


def test_strip_wrapping_delimiters_rejects_empty_payload() -> None:
    assert math.strip_wrapping_delimiters("$$", "$$") == "$$"


def test_finalize_repaired_formula_keeps_errors_aligned_with_returned_formula(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        math,
        "validate_formula",
        lambda text, _display_mode: ["stripped error"]
        if text == "stripped"
        else ["cleaned error"]
        if text == "cleaned"
        else [],
    )
    monkeypatch.setattr(math, "cleanup_formula", lambda _text: "cleaned")

    repaired, errors = math._finalize_repaired_formula("$$stripped$$", "$$")

    assert repaired == "cleaned"
    assert errors == ["cleaned error"]


def test_fix_math_text_handles_cancelled_error_results(monkeypatch) -> None:
    async def fake_repair_batch(*_args, **_kwargs):
        raise asyncio.CancelledError("cancelled")

    monkeypatch.setattr(math, "validate_formula", lambda *_args, **_kwargs: ["parse error"])
    monkeypatch.setattr(math, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(math, "short_hash", lambda _path: "abc")

    stats = math.MathFixStats()
    span = math.FormulaSpan(
        start=0,
        end=len("$$broken$$"),
        delimiter="$$",
        content="broken",
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        math.fix_math_text(
            text="$$broken$$",
            file_path="demo.json",
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
            spans=[span],
        )
    )

    assert updated == "$$broken$$"
    assert len(errors) == 1
    assert any(err.startswith("batch_exception:") for err in errors[0]["errors"])


def test_fix_math_text_accepts_valid_repair_without_recleanup(monkeypatch) -> None:
    original = (
        r"$$f_{c}(c_{1},c_{2})=\begin{cases}p_{c}&c_{1}\neq c_{2}\0&\text{otherwise}\end{cases}.$$"
    )
    repaired = (
        r"f_{c}(c_{1},c_{2})=\begin{cases}"
        r"p_{c}, & c_{1}\neq c_{2}\\"
        r"0, & \text{otherwise}"
        r"\end{cases}."
    )

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired}, None

    def fake_validate_formula(text: str, _display_mode: bool) -> list[str]:
        if text == original[2:-2]:
            return ["parse error"]
        if text == repaired or text == repaired.strip():
            return []
        return ["unexpected mutation"]

    monkeypatch.setattr(math, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(math, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(math, "validate_formula", fake_validate_formula)

    stats = math.MathFixStats()
    span = math.FormulaSpan(
        start=0,
        end=len(original),
        delimiter="$$",
        content=original[2:-2],
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        math.fix_math_text(
            text=original,
            file_path="demo.json",
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

    assert updated == f"$${repaired}$$"
    assert errors == []
    assert stats.formulas_repaired == 1
