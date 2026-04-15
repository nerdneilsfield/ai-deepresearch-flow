from __future__ import annotations

import asyncio

from deepresearch_flow.recognize import cli


class _FakeProgress:
    def __init__(self, total: int = 0) -> None:
        self.total = total
        self.refresh_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1


def test_increase_progress_total_updates_total_and_refreshes() -> None:
    progress = _FakeProgress(total=2)

    asyncio.run(cli._increase_progress_total(progress, asyncio.Lock(), 3))

    assert progress.total == 5
    assert progress.refresh_calls == 1


def test_increase_progress_total_ignores_non_positive_increments() -> None:
    progress = _FakeProgress(total=2)

    asyncio.run(cli._increase_progress_total(progress, asyncio.Lock(), 0))

    assert progress.total == 2
    assert progress.refresh_calls == 0
