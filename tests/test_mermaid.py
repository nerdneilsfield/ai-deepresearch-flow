import asyncio
from dataclasses import dataclass
from pathlib import Path
from random import Random

import pytest

from deepresearch_flow.recognize import mermaid


@dataclass(frozen=True)
class MermaidCleanupSeed:
    kind: str
    original: str
    expected: str
    seed_id: str


@dataclass(frozen=True)
class MermaidRejectSeed:
    original: str
    repaired: str
    expected_error: str
    seed_id: str


MERMAID_CLEANUP_SEEDS = [
    MermaidCleanupSeed(
        kind="pass",
        original=(
            "flowchart LR\n"
            'B[模型训练] --> B1["训练集：KITTI 00序列库帧<br/>'
            '损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)"]\n'
            "B --> B2[对比方法]\n"
        ),
        expected=(
            "flowchart LR\n"
            'B[模型训练] --> B1["训练集：KITTI 00序列库帧<br/>'
            '损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)"]\n'
            "B --> B2[对比方法]\n"
        ),
        seed_id="quoted-html-label-with-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["区间[-π, π)"] --> B["ok"]\n',
        expected='flowchart LR\nA["区间[-π, π)"] --> B["ok"]\n',
        seed_id="quoted-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["中括号[abc]"] --> B["ok"]\n',
        expected='flowchart LR\nA["中括号[abc]"] --> B["ok"]\n',
        seed_id="quoted-square-brackets",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["a|b|c"] --> B["ok"]\n',
        expected='flowchart LR\nA["a|b|c"] --> B["ok"]\n',
        seed_id="pipes",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["路径/斜杠\\反斜杠"] --> B["ok"]\n',
        expected='flowchart LR\nA["路径/斜杠\\反斜杠"] --> B["ok"]\n',
        seed_id="slashes",
    ),
    MermaidCleanupSeed(
        kind="pass",
        original='flowchart LR\nA["100% & <tag>"] --> B["ok"]\n',
        expected='flowchart LR\nA["100% & <tag>"] --> B["ok"]\n',
        seed_id="percent-amp-angle-brackets",
    ),
]

MERMAID_IDEMPOTENT_REPAIR_INPUTS = [
    'flowchart LR\nA["x"]B --> C["y"]\n',
    "flowchart LR\nA[中括号[abc]] --> B[ok]\n",
    'flowchart LR\nA["He said "hi""] --> B["ok"]\n',
    'flowchart LR\nA["a<br>b"]B --> C["ok"]\n',
]

MERMAID_REJECT_SEEDS = [
    MermaidRejectSeed(
        original='flowchart LR\nA["x"]"] --> C["y"]\n',
        repaired='flowchart LR\nA["x"]"] --> C["y"]\n',
        expected_error="still invalid",
        seed_id="invalid-quoted-close",
    ),
    MermaidRejectSeed(
        original='flowchart LR\nA["x"]B --> C["y"]\n',
        repaired='flowchart LR\nA["x"]"] --> C["y"]\n',
        expected_error="still invalid",
        seed_id="repair-introduces-invalid-close",
    ),
]


def assert_mermaid_cleanup_idempotent(original: str) -> None:
    once = mermaid.cleanup_mermaid(original)
    twice = mermaid.cleanup_mermaid(once)
    assert twice == once


def _mermaid_rng(seed: int) -> Random:
    return Random(seed)


def _mermaid_header(seed: int) -> str:
    rng = _mermaid_rng(seed)
    diagram = rng.choice(("flowchart", "graph"))
    direction = rng.choice(("LR", "RL", "TD"))
    return f"{diagram} {direction}"


def _mermaid_nodes(seed: int, count: int) -> list[str]:
    return [f"N{seed}_{idx}" for idx in range(count)]


def _mermaid_tokens(seed: int, count: int) -> list[str]:
    return [f"seed_{seed}_{idx}" for idx in range(count)]


def _build_mermaid_pass_body(seed: int) -> tuple[str, tuple[str, ...], str]:
    header = _mermaid_header(seed)
    nodes = _mermaid_nodes(seed, 4)
    tokens = _mermaid_tokens(seed, 5)
    lines = [
        f'{nodes[0]}["{tokens[0]}[-π, π)"] --> {nodes[1]}["{tokens[1]}<br/>{tokens[2]}"]',
        f'{nodes[1]} --> {nodes[2]}["{tokens[3]}|pipe"]',
        f'{nodes[2]} --> {nodes[3]}["{tokens[4]} & <tag>"]',
    ]
    return "\n".join([header, *lines]) + "\n", tuple(tokens), header


def _build_mermaid_repair_body(seed: int) -> tuple[str, str, tuple[str, ...], str]:
    header = _mermaid_header(seed)
    nodes = _mermaid_nodes(seed, 6)
    tokens = _mermaid_tokens(seed, 6)
    original_lines = [
        f'{nodes[0]}["{tokens[0]}"]{nodes[1]} --> {nodes[2]}["{tokens[1]}"]',
        f'{nodes[2]}["{tokens[2]} "hi""] --> {nodes[3]}["{tokens[3]}"]',
        f"{nodes[3]}[{tokens[3]}[-π, π)] --> {nodes[4]}[ok]",
        f'{nodes[4]}["{tokens[4]}<br>{tokens[5]}"] --> {nodes[5]}["mixed"]',
        f'{nodes[5]}["valid_{seed}"] --> {nodes[0]}["{tokens[0]}"]',
    ]
    repaired_lines = [
        f'{nodes[0]}["{tokens[0]}"] --> {nodes[1]}["{tokens[1]}"]',
        f'{nodes[2]}["{tokens[2]}\'hi\'"] --> {nodes[3]}["{tokens[3]}"]',
        f'{nodes[3]}["{tokens[3]}[-π, π)"] --> {nodes[4]}["ok"]',
        f'{nodes[4]}["{tokens[4]}<br/>{tokens[5]}"] --> {nodes[5]}["mixed"]',
        f'{nodes[5]}["valid_{seed}"] --> {nodes[0]}["{tokens[0]}"]',
    ]
    original = "\n".join([header, *original_lines]) + "\n"
    repaired = "\n".join([header, *repaired_lines]) + "\n"
    return original, repaired, tuple(tokens), header


def _build_mermaid_reject_body(seed: int) -> tuple[str, str, tuple[str, ...], str]:
    header = _mermaid_header(seed)
    nodes = _mermaid_nodes(seed, 4)
    tokens = _mermaid_tokens(seed, 4)
    original_lines = [
        f'{nodes[0]}["{tokens[0]}"]"] --> {nodes[1]}["{tokens[1]}"]',
        f'{nodes[1]}["{tokens[2]}"]{nodes[2]} --> {nodes[3]}["{tokens[3]}"]',
    ]
    repaired_lines = [
        f'{nodes[0]}["{tokens[0]}"]"] --> {nodes[1]}["{tokens[1]}"]',
    ]
    original = "\n".join([header, *original_lines]) + "\n"
    repaired = "\n".join([header, *repaired_lines]) + "\n"
    return original, repaired, tuple(tokens), header


def _build_extract_fuzz_case(seed: int) -> tuple[str, tuple[str, ...], int]:
    rng = _mermaid_rng(seed)
    context_chars = rng.choice((0, 4, 8, 16, 32))
    parts: list[str] = [f"intro_{seed}\n"]
    expected_bodies: list[str] = []
    block_count = 1 + seed % 3
    for block_idx in range(block_count):
        if (seed + block_idx) % 2 == 0:
            body, _, _ = _build_mermaid_pass_body(seed * 10 + block_idx)
        else:
            body, _, _, _ = _build_mermaid_repair_body(seed * 10 + block_idx)
        expected_bodies.append(body)
        parts.append(f"```mermaid\n{body}```\n")
        if block_idx == 0:
            parts.append("```python\nprint('ignore me')\n```\n")
    parts.append(f"tail_{seed}\n")
    return "".join(parts), tuple(expected_bodies), context_chars


def _assert_cleanup_preserves_core_tokens(
    cleaned: str, expected_tokens: tuple[str, ...], header: str
) -> None:
    assert cleaned.splitlines()[0] == header
    for token in expected_tokens:
        assert token in cleaned


@pytest.mark.parametrize(
    "seed",
    [pytest.param(seed, id=f"{seed.kind}:{seed.seed_id}") for seed in MERMAID_CLEANUP_SEEDS],
)
def test_cleanup_mermaid_seed_table(seed: MermaidCleanupSeed) -> None:
    cleaned = mermaid.cleanup_mermaid(seed.original)

    assert cleaned.rstrip("\n") == seed.expected.rstrip("\n")


def test_fix_mermaid_text_accepts_valid_repair_without_recleanup(monkeypatch) -> None:
    original = (
        "flowchart LR\n"
        "B[模型训练] --> B1[训练集：KITTI 00序列库帧<br>"
        "损失：懒三元组损失<br>数据增强：z轴随机旋转[-π,π)]\n"
        "B --> B2[对比方法]\n"
    )
    repaired = (
        "flowchart LR\n"
        'B[模型训练] --> B1["训练集：KITTI 00序列库帧<br/>'
        '损失：懒三元组损失<br/>数据增强：z轴随机旋转[-π, π)"]\n'
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


@pytest.mark.parametrize("break_tag", ["<br>", "<br/>", "<br />"])
def test_cleanup_mermaid_preserves_html_break_label_variants(break_tag: str) -> None:
    original = f'flowchart LR\nA["a{break_tag}b"] --> B["ok"]\n'

    cleaned = mermaid.cleanup_mermaid(original)

    assert cleaned.rstrip("\n") == original.rstrip("\n")


@pytest.mark.parametrize("original", MERMAID_IDEMPOTENT_REPAIR_INPUTS)
def test_cleanup_mermaid_is_idempotent_across_seed_repairs(original: str) -> None:
    assert_mermaid_cleanup_idempotent(original)


@pytest.mark.parametrize("seed", range(100))
def test_extract_mermaid_spans_fuzz_preserves_fenced_blocks(seed: int) -> None:
    document, expected_bodies, context_chars = _build_extract_fuzz_case(seed)

    spans = mermaid.extract_mermaid_spans(document, context_chars)

    assert len(spans) == len(expected_bodies)
    assert [span.content for span in spans] == list(expected_bodies)
    for span in spans:
        assert document[span.start : span.end] == span.content
        assert span.line == document.count("\n", 0, span.start) + 1
        assert span.content in span.context
        assert len(span.context) >= len(span.content)


@pytest.mark.parametrize("seed", range(100))
def test_cleanup_mermaid_fuzz_preserves_valid_and_repairs_local_defects(seed: int) -> None:
    if seed % 3 == 0:
        original, expected_tokens, header = _build_mermaid_pass_body(seed)
        cleaned = mermaid.cleanup_mermaid(original)
        assert cleaned.rstrip("\n") == original.rstrip("\n")
    else:
        original, _, expected_tokens, header = _build_mermaid_repair_body(seed)
        cleaned = mermaid.cleanup_mermaid(original)
        assert cleaned.rstrip("\n") != original.rstrip("\n")
        _assert_cleanup_preserves_core_tokens(cleaned, expected_tokens, header)

    assert_mermaid_cleanup_idempotent(original)


@pytest.mark.parametrize("seed", range(100))
def test_fix_mermaid_text_fuzz_handles_pass_repair_and_reject_paths(monkeypatch, seed: int) -> None:
    kind = ("pass", "repair", "reject")[seed % 3]
    if kind == "pass":
        original_text, expected_tokens, header = _build_mermaid_pass_body(seed)
        repaired_text = original_text
        expected_error = None
    elif kind == "repair":
        original_text, repaired_text, expected_tokens, header = _build_mermaid_repair_body(seed)
        expected_error = None
    else:
        original_text, repaired_text, expected_tokens, header = _build_mermaid_reject_body(seed)
        expected_error = "still invalid"

    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": repaired_text}, None

    def fake_validate_mermaid(text: str) -> str | None:
        normalized = text.rstrip("\n")
        original_normalized = original_text.rstrip("\n")
        repaired_normalized = repaired_text.rstrip("\n")
        if normalized == original_normalized:
            return None if kind == "pass" else "parse error"
        if normalized == repaired_normalized:
            return None if kind == "repair" else expected_error
        return "unexpected mutation"

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)
    monkeypatch.setattr(mermaid, "cleanup_mermaid", lambda text: text)

    stats = mermaid.MermaidFixStats()
    span = mermaid.MermaidSpan(
        start=0,
        end=len(original_text),
        content=original_text,
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        mermaid.fix_mermaid_text(
            text=original_text,
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

    if kind == "pass":
        assert updated.rstrip("\n") == original_text.rstrip("\n")
        assert errors == []
        assert stats.diagrams_total == 1
        assert stats.diagrams_invalid == 0
        assert stats.diagrams_repaired == 0
        assert stats.diagrams_failed == 0
    elif kind == "repair":
        assert updated.rstrip("\n") == repaired_text.rstrip("\n")
        assert errors == []
        assert stats.diagrams_total == 1
        assert stats.diagrams_invalid == 1
        assert stats.diagrams_repaired == 1
        assert stats.diagrams_failed == 0
    else:
        assert updated.rstrip("\n") == original_text.rstrip("\n")
        assert len(errors) == 1
        assert errors[0]["errors"][-1] == expected_error
        assert stats.diagrams_total == 1
        assert stats.diagrams_invalid == 1
        assert stats.diagrams_repaired == 0
        assert stats.diagrams_failed == 1

    for token in expected_tokens:
        assert token in updated


def test_fix_mermaid_text_handles_cancelled_error_results(monkeypatch) -> None:
    async def fake_repair_batch(*_args, **_kwargs):
        raise asyncio.CancelledError("cancelled")

    monkeypatch.setattr(mermaid, "validate_mermaid", lambda *_args, **_kwargs: "parse error")
    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")

    original = 'flowchart LR\nA["x"]B --> C["y"]\n'
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

    assert updated == original
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert stats.diagrams_failed == 1


def test_repair_all_diagrams_global_handles_cancelled_validation_results(
    monkeypatch,
) -> None:
    original = 'flowchart LR\nA["x"] --> B["y"]\n'
    span = mermaid.MermaidSpan(
        start=0,
        end=len(original),
        content=original,
        line=1,
        context="",
    )
    issue = mermaid.MermaidIssue(
        issue_id="abc:1:0",
        span=span,
        errors=["not_validated"],
        field_path=None,
        item_index=None,
    )
    task = mermaid.DiagramTask(
        file_path=Path("demo.md"),
        file_line_offset=1,
        field_path=None,
        item_index=None,
        span=span,
        issue=issue,
    )

    async def fake_to_thread(*_args, **_kwargs):
        raise asyncio.CancelledError("cancelled")

    monkeypatch.setattr(mermaid.asyncio, "to_thread", fake_to_thread)

    stats = mermaid.MermaidFixStats()
    replacements, errors = asyncio.run(
        mermaid.repair_all_diagrams_global(
            tasks=[task],
            batch_size=1,
            max_concurrent_batches=1,
            route_pool=object(),  # type: ignore[arg-type]
            timeout=1.0,
            max_retries=0,
            client=None,  # type: ignore[arg-type]
            stats=stats,
        )
    )

    assert replacements == {}
    assert len(errors) == 1
    assert errors[0]["path"] == "demo.md"
    assert stats.diagrams_failed == 1


@pytest.mark.parametrize(
    "seed",
    [pytest.param(seed, id=f"reject:{seed.seed_id}") for seed in MERMAID_REJECT_SEEDS],
)
def test_fix_mermaid_text_rejects_invalid_repairs(monkeypatch, seed: MermaidRejectSeed) -> None:
    async def fake_repair_batch(*_args, **_kwargs):
        return {"abc:0": seed.repaired}, None

    def fake_validate_mermaid(text: str) -> str | None:
        if text.rstrip("\n") == seed.repaired.rstrip("\n"):
            return seed.expected_error
        if text.rstrip("\n") == seed.original.rstrip("\n"):
            return "parse error"
        return None

    monkeypatch.setattr(mermaid, "repair_batch", fake_repair_batch)
    monkeypatch.setattr(mermaid, "short_hash", lambda _path: "abc")
    monkeypatch.setattr(mermaid, "validate_mermaid", fake_validate_mermaid)
    monkeypatch.setattr(mermaid, "cleanup_mermaid", lambda text: text)

    stats = mermaid.MermaidFixStats()
    span = mermaid.MermaidSpan(
        start=0,
        end=len(seed.original),
        content=seed.original,
        line=1,
        context="",
    )

    updated, errors = asyncio.run(
        mermaid.fix_mermaid_text(
            text=seed.original,
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

    assert updated == seed.original
    assert len(errors) == 1
    assert errors[0]["errors"][-1] == seed.expected_error
    assert stats.diagrams_failed == 1
