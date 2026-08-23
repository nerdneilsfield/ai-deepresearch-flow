"""Vector-index adapters for publication receipts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any

from .publication_models import PublicationBundle


@dataclass(frozen=True)
class LanceDBIndexer:
    """Callable incremental indexer backed by existing embed pipeline."""

    config: Any
    snapshot_db: Path
    static_root: Path
    vector_dir: Path

    def __call__(self, bundle: PublicationBundle) -> None:
        del bundle
        from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

        result = run_embed_pipeline(
            config=self.config,
            snapshot_db=self.snapshot_db,
            static_export_dir=self.static_root,
            vector_dir=self.vector_dir,
        )
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError("LanceDBIndexer cannot run inside an active event loop")


__all__ = ["LanceDBIndexer"]
