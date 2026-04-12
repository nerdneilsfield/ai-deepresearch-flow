from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch_flow.paper.utils import (
    compute_source_hash,
    discover_markdown,
    estimate_tokens,
    extract_json_from_text,
    parse_json,
    read_text,
    short_hash,
    split_output_name,
    stable_hash,
    truncate_content,
    unique_split_name,
)


def test_discover_markdown_handles_files_directories_and_globs(tmp_path: Path) -> None:
    top = tmp_path / "top.md"
    top.write_text("# top", encoding="utf-8")
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested = nested_dir / "child.md"
    nested.write_text("# child", encoding="utf-8")
    other = tmp_path / "note.txt"
    other.write_text("x", encoding="utf-8")

    found = discover_markdown([str(top), str(tmp_path)], None)
    assert found == sorted({top.resolve(), nested.resolve()})

    non_recursive = discover_markdown([str(tmp_path)], "*.md", recursive=False)
    assert non_recursive == [top.resolve()]


def test_discover_markdown_raises_for_non_markdown_file_and_missing_path(tmp_path: Path) -> None:
    other = tmp_path / "note.txt"
    other.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="not a markdown file"):
        discover_markdown([str(other)], None)

    with pytest.raises(FileNotFoundError, match="Input path not found"):
        discover_markdown([str(tmp_path / "missing")], None)


def test_read_text_falls_back_to_latin1(tmp_path: Path) -> None:
    path = tmp_path / "latin1.md"
    path.write_bytes("caf\xe9".encode("latin-1"))

    assert read_text(path) == "café"


def test_hash_helpers_are_stable_and_length_bounded() -> None:
    assert compute_source_hash("abc") == compute_source_hash("abc")
    assert stable_hash("abc") == stable_hash("abc")
    assert len(short_hash("abc")) == 8
    assert short_hash("abc") == stable_hash("abc")[:8]


def test_truncate_content_strategies_and_invalid_strategy() -> None:
    content = "abcdefghij"

    assert truncate_content(content, 0, "head") == (content, None)
    assert truncate_content(content, 20, "head") == (content, None)

    truncated, meta = truncate_content(content, 4, "head")
    assert truncated == "abcd"
    assert meta == {"strategy": "head", "original_chars": 10, "kept_chars": 4}

    head_tail, head_tail_meta = truncate_content(content, 6, "head_tail")
    assert head_tail == "abc\n\n...\n\nhij"
    assert head_tail_meta == {
        "strategy": "head_tail",
        "original_chars": 10,
        "kept_chars": 6,
    }

    with pytest.raises(ValueError, match="Unknown truncate strategy"):
        truncate_content(content, 4, "middle")


def test_estimate_tokens_has_minimum_of_one() -> None:
    assert estimate_tokens(0) == 1
    assert estimate_tokens(3) == 1
    assert estimate_tokens(20) == 5


def test_extract_json_from_text_handles_fenced_and_embedded_json() -> None:
    assert extract_json_from_text('{"ok": true}') == '{"ok": true}'
    assert extract_json_from_text("```json\n{\"ok\": true}\n```") == '{"ok": true}'
    assert extract_json_from_text("prefix {\"ok\": true} suffix") == '{"ok": true}'

    with pytest.raises(ValueError, match="No JSON object found"):
        extract_json_from_text("plain text only")


def test_parse_json_handles_direct_embedded_and_repaired_payloads(monkeypatch) -> None:
    assert parse_json('{"ok": 1}') == {"ok": 1}
    assert parse_json("Answer: {\"ok\": 2}") == {"ok": 2}

    monkeypatch.setattr(
        "deepresearch_flow.paper.utils.json_repair.loads",
        lambda text, skip_json_loads: {"fixed": text, "skip": skip_json_loads},
    )
    repaired = parse_json("```json\n{'bad': 1,}\n```")
    assert repaired == {"fixed": "{'bad': 1,}", "skip": True}


def test_parse_json_raises_when_repair_does_not_return_object(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepresearch_flow.paper.utils.json_repair.loads",
        lambda text, skip_json_loads: ["not", "a", "dict"],
    )

    with pytest.raises(ValueError, match="did not produce an object"):
        parse_json("```json\n{'bad': 1,}\n```")


def test_split_output_name_and_unique_split_name() -> None:
    assert split_output_name(Path("/tmp/paper/output.md")) == "paper"
    assert split_output_name(Path("/tmp/paper.md")) == "paper"

    used: set[str] = set()
    first = unique_split_name("paper", used, "source-a")
    second = unique_split_name("paper", used, "source-b")

    assert first == "paper"
    assert second.startswith("paper_")
    assert len(second) == len("paper_") + 8
