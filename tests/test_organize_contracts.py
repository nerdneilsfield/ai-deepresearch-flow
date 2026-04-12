from __future__ import annotations

import asyncio
import subprocess

from deepresearch_flow.recognize import organize


def test_format_with_preserved_headings_keeps_original_hash_depth(monkeypatch) -> None:
    original = "# Title\n### I. INTRODUCTION\n### II. RELATED WORK\n"
    current = original

    async def fake_format_markdown(text: str) -> str:
        assert text == current
        return text.replace("### I. INTRODUCTION", "## I. INTRODUCTION")

    monkeypatch.setattr(organize, "_format_markdown", fake_format_markdown)

    restored = asyncio.run(organize._format_with_preserved_headings(original, current))
    assert restored == original


def test_organize_format_markdown_returns_original_on_rumdl_timeout(monkeypatch) -> None:
    monkeypatch.setattr(organize, "_RUMDL_PATH", "rumdl")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["rumdl", "fmt"], timeout=5.0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert asyncio.run(organize._format_markdown("# Title\n")) == "# Title\n"
