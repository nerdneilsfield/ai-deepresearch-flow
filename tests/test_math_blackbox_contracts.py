from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
import re

import pytest

from deepresearch_flow.recognize import math as math_module
from deepresearch_flow.recognize.math import (
    apply_replacements,
    cleanup_formula,
    extract_math_spans,
)


def _span_pairs(spans) -> list[tuple[str, str]]:
    return [(span.delimiter, span.content) for span in spans]


@dataclass(frozen=True)
class ExtractFuzzCase:
    text: str
    expected: list[tuple[str, str]]
    context_chars: int
    note: str


@dataclass(frozen=True)
class CleanupFuzzCase:
    original: str
    expected: str
    note: str


def _repair_command_spacing(text: str) -> str:
    return re.sub(r"\\\s+(?=[A-Za-z])", r"\\", text)


def _make_inline_formula(case_id: int, slot: int, suffix: str = "") -> str:
    tail = f"_{suffix}" if suffix else ""
    return f"x_{case_id}_{slot}{tail}+y_{case_id}_{slot}{tail}"


def _make_display_formula(case_id: int, slot: int, suffix: str = "") -> str:
    tail = f"_{suffix}" if suffix else ""
    return f"\n{case_id + slot}{tail} + z_{case_id}_{slot}{tail}\n\n"


def _build_extract_fuzz_cases(count: int = 120, seed: int = 20240413) -> list[ExtractFuzzCase]:
    rng = random.Random(seed)
    prose_pool = [
        "Intro text with commas, punctuation, and plain words.",
        "Second sentence keeps the surrounding prose stable.",
        "Hostile placeholder __PH_AUTOLINK_000106__ sits here.",
        "Broken delimiter bait starts with $ but never closes.",
        "Nested dollars $$ are only noise when they do not form math.",
        "A code fence follows next and should stay inert.",
        "JSON escape damage motif: x\\nabla x\\rho x\\tab.",
        "Cases/aligned damage motif: \\ begin{cases} and \\ end{aligned}.",
    ]
    cases: list[ExtractFuzzCase] = []
    for case_id in range(count):
        text_parts: list[str] = []
        expected: list[tuple[str, str]] = []
        context_chars = rng.randint(0, 18)
        mode = rng.choice(["pass", "mixed", "reject"])

        text_parts.append(prose_pool[case_id % len(prose_pool)])
        if mode == "reject":
            reject_fragments = [
                f"broken ${case_id} only",
                f"price is $5 and shell-like `$HOME` marker {case_id}",
                f"$$\nprose only {case_id}\n$$",
                f"```\n$ no extract {case_id} $\n```",
                f"__PH_AUTOLINK_{case_id:06d}__ and {prose_pool[(case_id + 1) % len(prose_pool)]}",
            ]
            text_parts.append(rng.choice(reject_fragments))
            text_parts.append(prose_pool[(case_id + 2) % len(prose_pool)])
            cases.append(
                ExtractFuzzCase(
                    text=" ".join(text_parts),
                    expected=[],
                    context_chars=context_chars,
                    note=f"reject-{case_id}",
                )
            )
            continue

        span_count = 2 + (case_id % 3)
        for slot in range(span_count):
            choice = rng.choice(["inline", "display", "broken", "placeholder", "fence"])
            if choice == "inline":
                content = _make_inline_formula(case_id, slot, suffix="pass")
                text_parts.append(f"${content}$")
                expected.append(("$", content))
            elif choice == "display":
                content = _make_inline_formula(case_id, slot, suffix="display")
                text_parts.append(f"${content}$")
                expected.append(("$", content))
            elif choice == "broken":
                text_parts.append(f"${_make_inline_formula(case_id, slot, suffix='broken')}")
                text_parts.append("and more prose without closing the delimiter.")
            elif choice == "placeholder":
                text_parts.append(
                    f"__PH_AUTOLINK_{case_id:06d}__ plus plain prose and a literal dollar sign."
                )
            else:
                text_parts.append("```\n$x+y$\n```")

        if mode == "mixed":
            text_parts.append(
                f"Trailing prose with control chars x\\nabla and x\\rho around case {case_id}."
            )

        cases.append(
            ExtractFuzzCase(
                text=" ".join(text_parts),
                expected=expected,
                context_chars=context_chars,
                note=f"{mode}-{case_id}",
            )
        )
    return cases


def _build_cleanup_fuzz_cases(count: int = 120, seed: int = 20240414) -> list[CleanupFuzzCase]:
    rng = random.Random(seed)
    clean_templates = [
        r"\Rightarrow \Big \Re \Im",
        r"\text{a b} + \operatorname{a b}",
        r"\begin{cases}x&=y\\z&=w\end{cases}",
        r"\begin{aligned}a&=b\\c&=d\end{aligned}",
        r"\frac{1}{2} \left( x + y \right)",
        r"\mathbf{B}_{\text{patch}}^{\prime} + \alpha",
    ]
    control_char_templates = [
        "x\nabla",
        "x\rho",
        "x\tab",
        "x\x0corm",
    ]
    placeholder_templates = [
        "__PH_AUTOLINK_000106__ + \\text{a b}",
        "__PH_URL_000207__ and \\operatorname{a b}",
        "prefix __PH_CODE_000017__ suffix",
    ]
    cases: list[CleanupFuzzCase] = []
    for case_id in range(count):
        mode = rng.choice(["pass", "repair", "reject", "hostile"])
        base = clean_templates[case_id % len(clean_templates)]
        if mode == "pass":
            original = base
            expected = base
            note = f"pass-{case_id}"
        elif mode == "repair":
            mutated = base
            for token in rng.sample(
                ["begin", "end", "Rightarrow", "Big", "Re", "Im", "left", "right", "AA"],
                k=min(4, 3 + case_id % 3),
            ):
                mutated = mutated.replace(f"\\{token}", f"\\ {token}")
            if "cases" in mutated and "aligned" in mutated:
                mutated = mutated.replace("\\ begin", "\\  begin")
            original = mutated
            expected = _repair_command_spacing(mutated)
            note = f"repair-{case_id}"
        elif mode == "reject":
            original = rng.choice(control_char_templates)
            expected = original
            note = f"reject-{case_id}"
        else:
            original = (
                f"{rng.choice(control_char_templates)} + {rng.choice(placeholder_templates)} + "
                f"\\ begin{{cases}} x &= y \\ end{{cases}}"
            )
            expected = _repair_command_spacing(original)
            note = f"hostile-{case_id}"
        cases.append(CleanupFuzzCase(original=original, expected=expected, note=note))
    return cases


EXTRACT_FUZZ_CASES = _build_extract_fuzz_cases()
CLEANUP_FUZZ_CASES = _build_cleanup_fuzz_cases()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("The value is $x+y$.", [("$", "x+y")], id="inline"),
        pytest.param("A display block:\n$$\na+b\n$$\n", [("$$", "\na+b\n")], id="display"),
        pytest.param("a $x+y$, b $z^2$.", [("$", "x+y"), ("$", "z^2")], id="adjacent-punctuation"),
        pytest.param(
            "The shell variable is `$HOME` and the price is $5 only.",
            [],
            id="non-math-dollar",
        ),
        pytest.param(
            "start $x+y$ and broken $z+w",
            [("$", "x+y")],
            id="valid-then-unmatched",
        ),
        pytest.param(
            "$invalid with spaces and $valid$ trailing",
            [("$", "valid")],
            id="invalid-then-valid",
        ),
        pytest.param(
            "、$\\mathcal{L}_{L1}$ 与 $x+y$",
            [("$", r"\mathcal{L}_{L1}"), ("$", "x+y")],
            id="leading-punctuation",
        ),
    ],
)
def test_extract_math_spans_returns_expected_spans(
    text: str, expected: list[tuple[str, str]]
) -> None:
    assert _span_pairs(extract_math_spans(text, 0)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "$",
        "$$$",
        "$$$$",
        "price $5 only",
        "shell var $HOME only",
        "unmatched end x+y$",
        "unmatched start $x+y",
    ],
)
def test_extract_math_spans_ignores_unmatched_or_non_math_dollar_sequences(text: str) -> None:
    assert extract_math_spans(text, 0) == []


@pytest.mark.parametrize(
    "text",
    [
        (
            "$$\n"
            "Downloaded on March 30, 2026 at 20:06:42 UTC from IEEE Xplore.\n"
            "Restrictions apply.\n"
            "## 5 IMPLEMENTING THE INDEX ON A GPU\n"
            "The cost is $x+y$ in the text.\n"
            "$$\n"
        ),
        (
            "$$\n"
            "This is prose with a placeholder __PH_AUTOLINK_000106__ and inline math $x+y$.\n"
            "More prose.\n"
            "$$\n"
        ),
        (
            "$$\n"
            "\\underline{\\text{__PH_AUTOLINK_000106__}}\n"
            "A prose line with $x+y$ and $z^2$.\n"
            "$$\n"
        ),
        ("$$\n\\underline{\\text{__PH_AUTOLINK_000106__}}\n$$\n"),
    ],
)
def test_extract_math_spans_rejects_prose_or_placeholder_pollution(text: str) -> None:
    assert extract_math_spans(text, 0) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "坏起点 $abc 中文很多很多很多很多很多很多 $x+y$ 结尾",
            [("$", "x+y")],
            id="prose-between-delimiters-1",
        ),
        pytest.param(
            "坏起点 $abc and lots of prose words words words words words $x+y$ end",
            [("$", "x+y")],
            id="prose-between-delimiters-2",
        ),
        pytest.param(
            "P'$，不进行替换，以包含最多 c 个单元格。将生成的补丁 $\\mathbf{B}_{\\text{patch}}^{\\prime} 并继续说明 $x+y$",
            [("$", "x+y")],
            id="prose-swallowing-chunk",
        ),
        pytest.param(
            "这里有 $坏 公式 和 很多 中文 描述 直到 $\\alpha+\\beta$ 结束",
            [("$", r"\alpha+\beta")],
            id="one-giant-span",
        ),
        pytest.param(
            "a $good$ b $bad prose with punctuation，中文，and symbols $\\log_2(t)$ c",
            [("$", "good"), ("$", r"\log_2(t)")],
            id="mixed-valid-and-prose",
        ),
        pytest.param(
            "x $a b c d e f g h i j $\\frac{1}{2}$ y",
            [("$", r"\frac{1}{2}")],
            id="prose-before-fraction",
        ),
        pytest.param(
            "text $a very long prose sentence with many many many words and then \\alpha$ tail",
            [],
            id="long-prose-with-greek",
        ),
    ],
)
def test_extract_math_spans_rejects_prose_swallowing_extremes(
    text: str, expected: list[tuple[str, str]]
) -> None:
    assert _span_pairs(extract_math_spans(text, 0)) == expected


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        pytest.param(
            r"\Rightarrow \Big \Re \Im",
            r"\Rightarrow \Big \Re \Im",
            id="preserve-valid-commands",
        ),
        pytest.param(
            r"\text{a b} + \operatorname{a b}",
            r"\text{a b} + \operatorname{a b}",
            id="preserve-legitimate-spaces",
        ),
        pytest.param(
            r"\ begin{cases} x \ end{cases} \ Rightarrow y \ AA",
            r"\begin{cases} x \end{cases} \Rightarrow y \AA",
            id="repair-obvious-command-spacing",
        ),
    ],
)
def test_cleanup_formula_preserves_valid_formula_and_repairs_local_damage(
    original: str, expected: str
) -> None:
    assert cleanup_formula(original) == expected


def test_fix_math_text_keeps_valid_display_formula_with_cjk_punctuation(monkeypatch) -> None:
    original = (
        "$$"
        r"\text{参数：} t_h=0.1 \ (\text{阈值})，p_h=0.05 \ (\text{惩罚值})，\text{语义惩罚} f_c"
        "$$"
    )

    monkeypatch.setattr(
        math_module,
        "validate_formula",
        lambda text, _display_mode: [] if text == original[2:-2] else ["unexpected mutation"],
    )
    monkeypatch.setattr(math_module, "short_hash", lambda _path: "abc")

    stats = math_module.MathFixStats()
    updated, errors = asyncio.run(
        math_module.fix_math_text(
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
            spans=[
                math_module.FormulaSpan(
                    start=0,
                    end=len(original),
                    delimiter="$$",
                    content=original[2:-2],
                    line=1,
                    context="",
                )
            ],
        )
    )

    assert updated == original
    assert errors == []
    assert stats.formulas_failed == 0


def test_apply_replacements_rejects_overlapping_spans() -> None:
    with pytest.raises(ValueError, match="overlap"):
        apply_replacements("abcdef", [(1, 3, "X"), (2, 4, "Y")])


@pytest.mark.parametrize(
    "replacements",
    [
        [(1, 4, "X"), (1, 3, "Y")],
        [(1, 4, "X"), (1, 5, "Y")],
        [(0, 3, "X"), (0, 2, "Y"), (3, 5, "Z")],
    ],
)
def test_apply_replacements_rejects_same_start_or_containment_overlaps(
    replacements: list[tuple[int, int, str]],
) -> None:
    with pytest.raises(ValueError, match="overlap"):
        apply_replacements("abcdef", replacements)


def test_apply_replacements_allows_touching_spans() -> None:
    assert apply_replacements("abcdef", [(0, 2, "X"), (2, 4, "Y")]) == "XYef"


def test_extract_math_spans_blackbox_fuzz_contracts() -> None:
    assert len(EXTRACT_FUZZ_CASES) >= 100

    for idx, case in enumerate(EXTRACT_FUZZ_CASES):
        spans = extract_math_spans(case.text, case.context_chars)
        got = _span_pairs(spans)
        assert got == case.expected, f"case={idx} note={case.note} text={case.text!r}"

        for span in spans:
            recovered = case.text[span.start : span.end]
            assert recovered == f"{span.delimiter}{span.content}{span.delimiter}"
            left = max(0, span.start - case.context_chars)
            right = min(len(case.text), span.end + case.context_chars)
            assert span.context == case.text[left:right]
            assert span.start < span.end
            assert span.delimiter in {"$", "$$"}


def test_cleanup_formula_blackbox_fuzz_contracts() -> None:
    assert len(CLEANUP_FUZZ_CASES) >= 100

    for idx, case in enumerate(CLEANUP_FUZZ_CASES):
        repaired = cleanup_formula(case.original)
        assert repaired == case.expected, f"case={idx} note={case.note} original={case.original!r}"
        assert cleanup_formula(repaired) == repaired
