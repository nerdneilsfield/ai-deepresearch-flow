import asyncio
from dataclasses import dataclass
import logging
from types import SimpleNamespace

import pytest

from deepresearch_flow.recognize import math
from deepresearch_flow.paper.providers.base import ProviderError

@dataclass(frozen=True)
class MathSpanSeed:
    kind: str
    text: str
    expected: list[tuple[str, str]]
    seed_id: str


@dataclass(frozen=True)
class MathCleanupRejectSeed:
    repaired: str
    delimiter: str
    cleaned: str
    error: str
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
        text="$$\nDownloaded on March 30, 2026 at 20:06:42 UTC from IEEE Xplore. Restrictions apply.\n## 5 IMPLEMENTING THE INDEX ON A GPU\nThe cost is $x+y$ in the text.\n$$\n",
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

MATH_CLEANUP_REJECT_SEEDS = [
    MathCleanupRejectSeed(
        repaired="$$broken$$",
        delimiter="$$",
        cleaned="still_broken",
        error="cleaned error",
        seed_id="still-invalid-after-cleanup",
    ),
]


def assert_math_cleanup_idempotent(original: str) -> None:
    once = math.cleanup_formula(original)
    twice = math.cleanup_formula(once)
    assert once == original
    assert twice == once


@pytest.mark.parametrize("original", CLEANUP_FORMULA_PASS_SEEDS)
def test_cleanup_formula_pass_seeds_are_unchanged(original: str) -> None:
    cleaned = math.cleanup_formula(original)

    assert cleaned == original


def test_extract_math_spans_skips_currency_and_code_like_dollar_sequences() -> None:
    text = "price is $5. code: `$HOME` and math $x+y$\n```\n$z$\n```\n$$\na+b\n$$\n"

    spans = math.extract_math_spans(text, 0)

    assert [(span.delimiter, span.content) for span in spans] == [
        ("$", "x+y"),
        ("$$", "\na+b\n"),
    ]


def test_extract_math_spans_skips_placeholder_polluted_math() -> None:
    text = (
        r"This is $\underline{\text{__PH_AUTOLINK_000106__}}$ end"
        "\n$$\n\\underline{\\text{__PH_AUTOLINK_000106__}}\n$$\n"
    )

    spans = math.extract_math_spans(text, 0)

    assert spans == []


def test_extract_math_spans_reclassifies_prose_like_display_blocks() -> None:
    text = (
        "$$\n"
        "Downloaded on March 30, 2026 at 20:06:42 UTC from IEEE Xplore. Restrictions apply.\n"
        "## 5 IMPLEMENTING THE INDEX ON A GPU\n"
        "The cost is $x+y$ in the text.\n"
        "$$\n"
    )

    spans = math.extract_math_spans(text, 0)

    assert spans == []


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(seed, id=f"{seed.kind}:{seed.seed_id}")
        for seed in MATH_SPAN_SEEDS
    ],
)
def test_extract_math_spans_seed_classification(seed: MathSpanSeed) -> None:
    spans = math.extract_math_spans(seed.text, 0)

    assert [(span.delimiter, span.content) for span in spans] == seed.expected


@pytest.mark.parametrize(("broken", "expected"), CLEANUP_FORMULA_REPAIR_SEEDS)
def test_cleanup_formula_repair_seeds(broken: str, expected: str) -> None:
    cleaned = math.cleanup_formula(broken)

    assert cleaned == expected


def test_cleanup_formula_does_not_treat_line_breaks_as_control_commands() -> None:
    assert math.cleanup_formula("x\nabla") == "x\nabla"
    assert math.cleanup_formula("x\rho") == "x\rho"


def test_cleanup_formula_preserves_legitimate_text_spaces() -> None:
    original = r"\text{a b} + \operatorname{a b}"

    cleaned = math.cleanup_formula(original)

    assert cleaned == original


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


@pytest.mark.parametrize(
    "seed",
    [pytest.param(seed, id=f"reject:{seed.seed_id}") for seed in MATH_CLEANUP_REJECT_SEEDS],
)
def test_finalize_repaired_formula_rejects_still_invalid_cleanup(
    monkeypatch, seed: MathCleanupRejectSeed
) -> None:
    monkeypatch.setattr(
        math,
        "validate_formula",
        lambda text, _display_mode: [seed.error] if text == seed.cleaned else ["parse error"],
    )
    monkeypatch.setattr(math, "cleanup_formula", lambda _text: seed.cleaned)

    repaired, errors = math._finalize_repaired_formula(seed.repaired, seed.delimiter)

    assert repaired == seed.cleaned
    assert errors == [seed.error]


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


def test_repair_batch_structured_fallback_succeeds_without_consuming_retries(
    monkeypatch,
) -> None:
    issue = math.FormulaIssue(
        issue_id="abc:0",
        span=math.FormulaSpan(
            start=0,
            end=0,
            delimiter="$$",
            content="broken",
            line=1,
            context="",
        ),
        errors=["parse error"],
        cleaned="broken",
        field_path=None,
        item_index=None,
    )

    class DummyRoutePool:
        def __init__(self) -> None:
            self.route = SimpleNamespace(
                provider=SimpleNamespace(max_tokens=1024),
                model=SimpleNamespace(model_name="demo-model"),
                key=SimpleNamespace(value="demo-key"),
            )
            self.mark_error_calls = 0

        async def get(self):
            return self.route

        async def mark_quota_exceeded(self, *_args, **_kwargs) -> bool:
            return False

        async def mark_error(self, *_args, **_kwargs) -> None:
            self.mark_error_calls += 1

    calls: list[str] = []

    async def fake_call_provider(
        _provider,
        _model_name,
        _messages,
        _schema,
        _api_key,
        _timeout,
        structured_mode,
        _client,
        *,
        max_tokens,
    ):
        assert max_tokens == 1024
        calls.append(structured_mode)
        if structured_mode != "none":
            raise ProviderError(
                "structured failed",
                retryable=False,
                structured_error=True,
            )
        return '{"items":[{"id":"abc:0","latex":"x"}]}'

    monkeypatch.setattr(math, "call_provider", fake_call_provider)
    monkeypatch.setattr(math, "structured_mode_for_model", lambda _model: "json_schema")

    route_pool = DummyRoutePool()
    repairs, error = asyncio.run(
        math.repair_batch(
            [issue],
            route_pool=route_pool,  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            client=None,  # type: ignore[arg-type]
        )
    )

    assert repairs == {"abc:0": "x"}
    assert error is None
    assert calls == ["json_schema", "none"]
    assert route_pool.mark_error_calls == 0


def test_build_repair_messages_math_prompt_prefers_local_syntax_repairs() -> None:
    issue = math.FormulaIssue(
        issue_id="abc:0",
        span=math.FormulaSpan(
            start=0,
            end=0,
            delimiter="$$",
            content=r"f(x)=\begin{cases}1\0\end{cases}",
            line=1,
            context="ctx",
        ),
        errors=["parse error"],
        cleaned=r"f(x)=\begin{cases}1\0\end{cases}",
        field_path=None,
        item_index=None,
    )

    messages = math.build_repair_messages([issue])
    system = messages[0]["content"]

    assert "Do not translate or paraphrase mathematical meaning" in system
    assert "Preserve all existing LaTeX commands" in system
    assert "Only repair local syntax issues" in system
    assert "Do not turn prose into math" in system
    assert "return it unchanged" in system


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
