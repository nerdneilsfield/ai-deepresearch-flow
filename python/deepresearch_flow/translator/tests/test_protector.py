from __future__ import annotations

import hashlib

import pytest

from deepresearch_flow.recognize import math as recognize_math
from deepresearch_flow.translator.config import TranslateConfig
from deepresearch_flow.translator.placeholder import PlaceHolderStore
from deepresearch_flow.translator.protector import MarkdownProtector


@pytest.mark.parametrize(
    "text",
    [
        "Before $x+y$ after",
        "Before\n$$\na+b\n$$\nAfter",
        r"Before \[x+y\] after",
        r"Before \(x+y\) after",
        "Mix $x+y$ and\n$$\na+b\n$$\nand text",
    ],
)
def test_protect_unprotect_roundtrip_is_lossless_for_math_variants(text: str) -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()

    protected = protector.protect(text, cfg, store)

    assert protector.unprotect(protected, store) == text


def test_protect_unprotect_preserves_math_span_fingerprints() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = "Mix $x+y$ and\n$$\na+b\n$$\nand also \\[c+d\\] with \\(e+f\\)."

    def fingerprints(value: str) -> list[tuple[str, str]]:
        spans = recognize_math.extract_math_spans(value, 0)
        return [
            (
                span.delimiter,
                hashlib.sha256(span.content.encode("utf-8")).hexdigest(),
            )
            for span in spans
        ]

    protected = protector.protect(text, cfg, store)
    restored = protector.unprotect(protected, store)

    assert restored == text
    assert fingerprints(restored) == fingerprints(text)


def test_paren_math_is_frozen_and_restored_unchanged() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = r"Before \(x + y\) after"

    protected = protector.protect(text, cfg, store)

    assert protected != text
    assert "__PH_MATH_" in protected
    assert protector.unprotect(protected, store) == text


def test_paren_math_scanner_handles_double_backslash_before_close() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = r"Before \(x \\) y\) after"

    protected = protector.protect(text, cfg, store)

    assert protected == "Before __PH_MATH_000001__ after"
    assert protector.unprotect(protected, store) == text


def test_inline_code_is_frozen_before_other_inline_protectors() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = r"Before `\(x+y\)` and `https://example.com` after"

    protected = protector.protect(text, cfg, store)

    assert store.kind_counts().get("CODE") == 2
    assert store.kind_counts().get("MATH") is None
    assert store.kind_counts().get("URL") is None
    assert protector.unprotect(protected, store) == text


def test_inline_dollar_math_is_frozen_and_restored_unchanged() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = "Before $x + y$ after"

    protected = protector.protect(text, cfg, store)

    assert "__PH_MATH_" in protected
    assert protector.unprotect(protected, store) == text


def test_bracket_math_is_frozen_and_restored_unchanged() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = r"Before \[x + y\] after"

    protected = protector.protect(text, cfg, store)

    assert "__PH_MATHBLOCK_" in protected
    assert protector.unprotect(protected, store) == text


def test_display_math_is_frozen_and_restored_unchanged() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = "Before\n$$\nx + y\n$$\nAfter"

    protected = protector.protect(text, cfg, store)

    assert "__PH_MATHBLOCK_" in protected
    assert protector.unprotect(protected, store) == text


def test_embedded_fence_like_line_keeps_entire_code_fence_together() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = (
        "```python\n"
        "print('start')\n"
        "```json\n"
        "<td>example</td>\n"
        "print('end')\n"
        "```\n"
    )

    protected = protector.protect(text, cfg, store)

    assert store.kind_counts().get("CODEFENCE") == 1
    assert protector.unprotect(protected, store) == text


def test_unknown_placeholder_like_tokens_are_detected() -> None:
    store = PlaceHolderStore()
    store.add("CODE", "`x`")
    text = "before __PH_UNKNOWN_999999__ after"

    assert store.find_unresolved_placeholder_tokens(text) == [
        "__PH_UNKNOWN_999999__"
    ]
    assert store.has_unresolved_placeholder_tokens(text)


def test_literal_placeholder_like_source_roundtrips_without_false_positive() -> None:
    store = PlaceHolderStore()
    source = "before __PH_LITERAL_000001__ after"

    store.record_source_placeholder_like_tokens(source)

    assert store.restore_all_checked(source) == source


def test_unprotect_raises_on_real_unresolved_placeholder_residuals() -> None:
    protector = MarkdownProtector()
    store = PlaceHolderStore()
    cfg = TranslateConfig()
    text = r"Before \(x + y\) after"

    protected = protector.protect(text, cfg, store)
    tampered = protected + " __PH_UNKNOWN_999999__"

    with pytest.raises(ValueError, match="unresolved placeholder"):
        protector.unprotect(tampered, store)


def test_restore_checked_rejects_known_placeholder_residuals() -> None:
    store = PlaceHolderStore()
    math_placeholder = store.add("MATH", r"\(x+y\)")
    code_placeholder = store.add("CODE", f"`{math_placeholder}`")

    with pytest.raises(ValueError, match="unresolved placeholder"):
        store.restore_all_checked(code_placeholder)
