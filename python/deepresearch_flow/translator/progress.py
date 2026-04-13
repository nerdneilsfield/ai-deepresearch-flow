"""Per-stage progress reporting for the translator scheduler."""

from __future__ import annotations

import asyncio

from tqdm import tqdm


class ProgressReporter:
    """Multi-bar progress display with per-stage tracking."""

    def __init__(self, doc_total: int, stages: list[str]) -> None:
        self.doc_bar = tqdm(
            total=doc_total,
            desc="documents",
            unit="doc",
            position=0,
        )
        self.stage_bars: dict[str, tqdm] = {}
        for index, stage in enumerate(stages):
            self.stage_bars[stage] = tqdm(
                total=0,
                desc=stage,
                unit="group",
                position=index + 1,
                leave=False,
            )
        self._lock = asyncio.Lock()
        self._closed = False

    async def add_groups(self, stage: str, count: int) -> None:
        if count <= 0 or stage not in self.stage_bars:
            return
        async with self._lock:
            bar = self.stage_bars[stage]
            bar.total = (bar.total or 0) + count
            bar.refresh()

    async def advance_groups(self, stage: str, count: int) -> None:
        if count <= 0 or stage not in self.stage_bars:
            return
        async with self._lock:
            self.stage_bars[stage].update(count)

    async def advance_docs(self, count: int = 1) -> None:
        if count <= 0:
            return
        async with self._lock:
            self.doc_bar.update(count)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            for bar in self.stage_bars.values():
                bar.close()
            self.doc_bar.close()
            self._closed = True
