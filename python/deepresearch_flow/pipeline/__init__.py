"""Administrative research pipeline foundations."""

from .artifacts import ArtifactStore
from .config import PipelineConfig, load_pipeline_config
from .ingestion import BatchIngestor, IngestionResult, IngestionService, UploadIngestion, UploadPart
from .matching import BibTeXMatcher, BibtexMatcher, MatchResult
from .state import (
    ALL_STEP_NAMES,
    JOB_STATUSES,
    PROCESSING_STEP_NAMES,
    STEP_NAMES,
    Lease,
    PipelineState,
)

__all__ = [
    "ArtifactStore",
    "JOB_STATUSES",
    "ALL_STEP_NAMES",
    "Lease",
    "PipelineConfig",
    "PipelineState",
    "STEP_NAMES",
    "PROCESSING_STEP_NAMES",
    "load_pipeline_config",
    "BatchIngestor",
    "IngestionResult",
    "UploadPart",
    "IngestionService",
    "UploadIngestion",
    "BibTeXMatcher",
    "BibtexMatcher",
    "MatchResult",
]

from .worker import (
    FIXED_STEP_SEQUENCE,
    FIXED_STEPS,
    PROCESSING_STEPS,
    PipelineAdapters,
    PipelineWorker,
    PreviewArtifacts,
    ProductionAdapters,
    WorkerResult,
    build_production_adapters,
    run_worker,
    run_production_worker,
    run_processing_worker,
    STEP_SEQUENCE,
    Worker,
    worker_entrypoint,
)

__all__ += [
    "FIXED_STEP_SEQUENCE",
    "FIXED_STEPS",
    "PROCESSING_STEPS",
    "PipelineAdapters",
    "PipelineWorker",
    "Worker",
    "PreviewArtifacts",
    "ProductionAdapters",
    "WorkerResult",
    "build_production_adapters",
    "run_worker",
    "run_production_worker",
    "run_processing_worker",
    "STEP_SEQUENCE",
    "worker_entrypoint",
]
