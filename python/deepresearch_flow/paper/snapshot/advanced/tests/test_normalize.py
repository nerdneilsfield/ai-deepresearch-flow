from __future__ import annotations

from deepresearch_flow.paper.snapshot.advanced.normalize import NormalizedQuery, normalize


def test_nfc_and_whitespace() -> None:
    q = normalize("  Vision   Transformer\n\t Pre-training  ")
    assert q.normalized == "Vision Transformer Pre-training"


def test_empty_query_returns_empty_fields() -> None:
    q = normalize("   ")
    assert q.normalized == ""
    assert q.fts_expr == ""
    assert q.lang == "en"


def test_language_detect_zh() -> None:
    q = normalize("视觉 transformer 预训练")
    assert q.lang in {"zh", "mixed"}


def test_language_detect_en() -> None:
    q = normalize("vision transformer pretraining")
    assert q.lang == "en"


def test_language_detect_mixed() -> None:
    q = normalize("transformer 预训练 model")
    assert q.lang == "mixed"


def test_fts_expr_nonempty_for_non_empty_query() -> None:
    q = normalize("vision transformer")
    assert q.fts_expr != ""


def test_returns_frozen_dataclass() -> None:
    q = normalize("hello")
    assert isinstance(q, NormalizedQuery)
    try:
        q.raw = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("NormalizedQuery should be frozen")
