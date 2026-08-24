"""Vector-index adapters for publication receipts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from pathlib import Path
import tempfile
from typing import Any, Callable

from .publication_models import PublicationBundle
from .publication_store import LocalFormalStore, safe_relative_path


@dataclass(frozen=True)
class LanceDBIndexer:
    """Callable incremental indexer backed by existing embed pipeline."""

    config: Any
    snapshot_db: Path
    static_root: Path
    vector_dir: Path
    embed_runner: Callable[..., Any] | None = None

    def __call__(self, bundle: PublicationBundle) -> None:
        from deepresearch_flow.paper.embed_pipeline import run_embed_pipeline

        base_dir = Path(bundle.work_dir).resolve() if bundle.work_dir is not None else None
        if base_dir is not None:
            base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".embedding-stage-", dir=str(base_dir) if base_dir is not None else None
        ) as staged_name:
            staged_root = Path(staged_name)
            staged_store = LocalFormalStore(staged_root)
            for resource in bundle.resources:
                relative = safe_relative_path(resource.relative_path)
                staged_store.put(relative, resource.content)
            runner = self.embed_runner or run_embed_pipeline
            result = runner(
                config=self.config,
                snapshot_db=self.snapshot_db,
                static_export_dir=staged_root,
                vector_dir=self.vector_dir,
                snapshot_paper_ids=(bundle.paper_id,),
            )
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                raise RuntimeError("LanceDBIndexer cannot run inside an active event loop")


__all__ = ["LanceDBIndexer"]
