"""Utility helpers for paper extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

import json_repair


def discover_markdown(
    inputs: Iterable[str],
    glob_pattern: str | None,
    recursive: bool = True,
) -> list[Path]:
    files: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if path.is_file():
            if path.suffix.lower() != ".md":
                raise ValueError(f"Input file is not a markdown file: {path}")
            files.add(path.resolve())
            continue

        if path.is_dir():
            if glob_pattern:
                matches = path.rglob(glob_pattern) if recursive else path.glob(glob_pattern)
            else:
                matches = path.rglob("*.md") if recursive else path.glob("*.md")
            for match in matches:
                if match.is_file():
                    files.add(match.resolve())
            continue

        raise FileNotFoundError(f"Input path not found: {path}")

    return sorted(files)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def compute_source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()


def truncate_content(
    content: str, max_chars: int, strategy: str
) -> tuple[str, dict[str, int | str] | None]:
    if max_chars <= 0 or len(content) <= max_chars:
        return content, None

    if strategy == "head":
        truncated = content[:max_chars]
        meta = {
            "strategy": "head",
            "original_chars": len(content),
            "kept_chars": max_chars,
        }
        return truncated, meta

    if strategy == "head_tail":
        head_len = max_chars // 2
        tail_len = max_chars - head_len
        truncated = content[:head_len] + "\n\n...\n\n" + content[-tail_len:]
        meta = {
            "strategy": "head_tail",
            "original_chars": len(content),
            "kept_chars": max_chars,
        }
        return truncated, meta

    raise ValueError(f"Unknown truncate strategy: {strategy}")


def estimate_tokens(char_count: int) -> int:
    return max(1, char_count // 4)


def extract_json_from_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
    if text.startswith("{") and text.endswith("}"):
        return text

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)

    raise ValueError("No JSON object found in response")


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = extract_json_from_text(text)
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            data = json_repair.loads(extracted, skip_json_loads=True)
            if not isinstance(data, dict):
                raise ValueError("Repaired JSON did not produce an object")
            return data


def split_output_name(path: Path) -> str:
    if path.name.lower() == "output.md" and path.parent.name:
        return path.parent.name
    return path.stem


def unique_split_name(base: str, used: set[str], source: str) -> str:
    if base not in used:
        used.add(base)
        return base
    suffix = short_hash(source)
    candidate = f"{base}_{suffix}"
    used.add(candidate)
    return candidate
