from __future__ import annotations

from dataclasses import dataclass
import logging

import pytest

from deepresearch_flow.recognize import math


@dataclass(frozen=True)
class MathSpanSeed:
    kind: str
    text: str
    expected: list[tuple[str, str]]
    seed_id: str


MATH_SPAN_SEEDS = [
    MathSpanSeed(
        kind="pass",
        text="plain inline math $x+y$ only",
        expected=[("$", "x+y")],
        seed_id="inline-math",
    ),
    MathSpanSeed(
        kind="reclassify",
        text="price is $5. code: `$HOME` and math $x+y$",
        expected=[("$", "x+y")],
        seed_id="currency-and-code-pass-real-inline-math",
    ),
    MathSpanSeed(
        kind="reclassify",
        text="shell var only $HOME should stay prose",
        expected=[],
        seed_id="shell-var-only",
    ),
    MathSpanSeed(
        kind="reclassify",
        text="This is $\\underline{\\text{__PH_AUTOLINK_000106__}}$ end",
        expected=[],
        seed_id="inline-placeholder-pollution",
    ),
    MathSpanSeed(
        kind="reclassify",
        text=(
            "$$\nDownloaded on March 30, 2026 at 20:06:42 UTC from IEEE Xplore. "
            "Restrictions apply.\n## 5 IMPLEMENTING THE INDEX ON A GPU\n"
            "The cost is $x+y$ in the text.\n$$\n"
        ),
        expected=[],
        seed_id="prose-display-block",
    ),
    MathSpanSeed(
        kind="pass",
        text="valid block:\n$$\na+b\n$$\n",
        expected=[("$$", "\na+b\n")],
        seed_id="display-block",
    ),
]


CLEANUP_FORMULA_PASS_SEEDS = [
    pytest.param(
        (
            r"f_{h}(\Delta h)=\begin{cases}"
            r"p_{h}, & \min\left(\Delta h,1-\Delta h\right)>t_{h}\\"
            r"0, & \text{otherwise}"
            r"\end{cases},"
        ),
        id="pass:cases-linebreaks",
    ),
    pytest.param(
        (
            r"f_{c}(c_{1},c_{2})=\begin{cases}"
            r"p_{c}, & c_{1}\neq c_{2}\\"
            r"0, & \text{otherwise}"
            r"\end{cases}."
        ),
        id="pass:latex-commands-with-neq-and-right",
    ),
    pytest.param(r"\Rightarrow \Big \Re \Im", id="pass:uppercase-standard-commands"),
    pytest.param(r"\L \O \S \P \AA", id="pass:single-letter-uppercase-commands"),
    pytest.param(r"\text{\textbf{t e r m}}", id="pass:nested-braced-text"),
]


CLEANUP_FORMULA_REPAIR_SEEDS = [
    pytest.param(
        (
            "\\sigma_{v}(v_{i},v_{j})="
            "\begin{cases}"
            "\\exp\\left(-\\frac{\\delta(h_{i},h_{j})+\\delta(w_{i},w_{j})+"
            "\\delta(d_{i},d_{j})}{3}"
            "\\right), & "
            "\text{若 } l_{i}=l_{j}\\\\"
            "0, & "
            "\text{其他情况}"
            "\\end{cases}"
        ),
        (
            r"\sigma_{v}(v_{i},v_{j})=\begin{cases}"
            r"\exp\left(-\frac{\delta(h_{i},h_{j})+\delta(w_{i},w_{j})+"
            r"\delta(d_{i},d_{j})}{3}\right), & "
            r"\text{若 } l_{i}=l_{j}\\"
            r"0, & \text{其他情况}"
            r"\end{cases}"
        ),
        id="repair:json-control-char-damage",
    ),
    pytest.param(
        r"\ begin{cases} x \ end{cases} \ Rightarrow y \ AA",
        r"\begin{cases} x \end{cases} \Rightarrow y \AA",
        id="repair:spaced-known-commands",
    ),
]


def assert_math_cleanup_idempotent(original: str) -> None:
    once = math.cleanup_formula(original)
    twice = math.cleanup_formula(once)
    assert once == original
    assert twice == once


@pytest.mark.parametrize("original", CLEANUP_FORMULA_PASS_SEEDS)
def test_cleanup_formula_pass_seeds_are_unchanged(original: str) -> None:
    assert math.cleanup_formula(original) == original


@pytest.mark.parametrize(
    "seed",
    [pytest.param(seed, id=f"{seed.kind}:{seed.seed_id}") for seed in MATH_SPAN_SEEDS],
)
def test_extract_math_spans_seed_classification(seed: MathSpanSeed) -> None:
    spans = math.extract_math_spans(seed.text, 0)

    assert [(span.delimiter, span.content) for span in spans] == seed.expected


@pytest.mark.parametrize(("broken", "expected"), CLEANUP_FORMULA_REPAIR_SEEDS)
def test_cleanup_formula_repair_seeds(broken: str, expected: str) -> None:
    assert math.cleanup_formula(broken) == expected


def test_cleanup_formula_does_not_treat_line_breaks_as_control_commands() -> None:
    assert math.cleanup_formula("x\nabla") == "x\nabla"
    assert math.cleanup_formula("x\rho") == "x\rho"


def test_cleanup_formula_preserves_legitimate_text_spaces() -> None:
    original = r"\text{a b} + \operatorname{a b}"

    assert math.cleanup_formula(original) == original


@pytest.mark.parametrize(
    "original",
    [
        r"\Rightarrow \Big \Re \Im",
        (
            r"f_{h}(\Delta h)=\begin{cases}"
            r"p_{h}, & \min\left(\Delta h,1-\Delta h\right)>t_{h}\\"
            r"0, & \text{otherwise}"
            r"\end{cases},"
        ),
        r"\text{\textbf{term}}",
    ],
)
def test_cleanup_formula_is_idempotent_for_valid_inputs(original: str) -> None:
    assert_math_cleanup_idempotent(original)


def test_strip_wrapping_delimiters_rejects_empty_payload() -> None:
    assert math.strip_wrapping_delimiters("$$", "$$") == "$$"


def test_apply_replacements_rejects_overlapping_spans() -> None:
    with pytest.raises(ValueError, match="overlap"):
        math.apply_replacements("abcdef", [(1, 3, "X"), (2, 4, "Y")])


def test_iter_batches_warns_for_oversized_singleton(caplog) -> None:
    issue = math.FormulaIssue(
        issue_id="abc:0",
        span=math.FormulaSpan(
            start=0,
            end=0,
            delimiter="$$",
            content="x" * 120,
            line=1,
            context="",
        ),
        errors=["parse error"],
        cleaned="x" * 120,
        field_path=None,
        item_index=None,
    )

    with caplog.at_level(logging.WARNING):
        batches = list(math.iter_batches([issue], batch_size=1, max_batch_chars=10))

    assert batches == [[issue]]
    assert any("exceeds max_batch_chars" in record.message for record in caplog.records)
