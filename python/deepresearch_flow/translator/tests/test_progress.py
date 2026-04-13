"""Black-box tests for ProgressReporter."""

from __future__ import annotations

import asyncio

import pytest

from deepresearch_flow.translator.progress import ProgressReporter


def test_reporter_creation_with_stages() -> None:
    async def run() -> None:
        reporter = ProgressReporter(doc_total=10, stages=["initial", "retry"])
        assert reporter.doc_bar.total == 10
        assert "initial" in reporter.stage_bars
        assert "retry" in reporter.stage_bars
        await reporter.close()

    asyncio.run(run())


def test_reporter_add_and_advance_groups() -> None:
    async def run() -> None:
        reporter = ProgressReporter(doc_total=5, stages=["initial"])
        await reporter.add_groups("initial", 20)
        assert reporter.stage_bars["initial"].total == 20
        await reporter.advance_groups("initial", 5)
        assert reporter.stage_bars["initial"].n == 5
        await reporter.close()

    asyncio.run(run())


def test_reporter_advance_docs() -> None:
    async def run() -> None:
        reporter = ProgressReporter(doc_total=3, stages=[])
        await reporter.advance_docs()
        assert reporter.doc_bar.n == 1
        await reporter.close()

    asyncio.run(run())


def test_reporter_unknown_stage_is_ignored() -> None:
    async def run() -> None:
        reporter = ProgressReporter(doc_total=1, stages=["initial"])
        await reporter.add_groups("nonexistent", 5)
        await reporter.advance_groups("nonexistent", 1)
        await reporter.close()

    asyncio.run(run())


def test_reporter_close_is_idempotent() -> None:
    async def run() -> None:
        reporter = ProgressReporter(doc_total=1, stages=["initial"])
        await reporter.close()
        await reporter.close()

    asyncio.run(run())
