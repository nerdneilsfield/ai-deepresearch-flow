from __future__ import annotations

from pathlib import Path

from deepresearch_flow.paper.extract import (
    ExtractionError,
    merge_retry_error_entries,
)


def test_merge_retry_error_entries_replaces_only_retried_stages() -> None:
    baseline = [
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage a failure",
            "stage_name": "stage_a",
        },
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage b failure",
            "stage_name": "stage_b",
        },
    ]

    merged = merge_retry_error_entries(
        baseline_entries=baseline,
        new_errors=[],
        attempted_full_paths=set(),
        attempted_stage_map={"/tmp/paper-a.md": {"stage_a"}},
    )

    assert merged == [baseline[1]]


def test_merge_retry_error_entries_replaces_full_doc_entries_with_new_failures() -> None:
    baseline = [
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage a failure",
            "stage_name": "stage_a",
        },
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage b failure",
            "stage_name": "stage_b",
        },
        {
            "source_path": "/tmp/paper-b.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage c failure",
            "stage_name": "stage_c",
        },
    ]

    merged = merge_retry_error_entries(
        baseline_entries=baseline,
        new_errors=[
            ExtractionError(
                path=Path("/tmp/paper-a.md"),
                provider="new-provider",
                model="new-model",
                error_type="validation_error",
                error_message="new stage b failure",
                stage_name="stage_b",
            )
        ],
        attempted_full_paths={"/tmp/paper-a.md"},
        attempted_stage_map={},
    )

    assert merged == [
        baseline[2],
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "new-provider",
            "model": "new-model",
            "error_type": "validation_error",
            "error_message": "new stage b failure",
            "stage_name": "stage_b",
        },
    ]


def test_merge_retry_error_entries_keeps_unattempted_entries_after_signal() -> None:
    baseline = [
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage a failure",
            "stage_name": "stage_a",
        },
        {
            "source_path": "/tmp/paper-b.md",
            "provider": "old-provider",
            "model": "old-model",
            "error_type": "old_error",
            "error_message": "old stage b failure",
            "stage_name": "stage_b",
        },
    ]

    merged = merge_retry_error_entries(
        baseline_entries=baseline,
        new_errors=[
            ExtractionError(
                path=Path("/tmp/paper-a.md"),
                provider="new-provider",
                model="new-model",
                error_type="timeout_error",
                error_message="new stage a failure",
                stage_name="stage_a",
            )
        ],
        attempted_full_paths=set(),
        attempted_stage_map={"/tmp/paper-a.md": {"stage_a"}},
    )

    assert merged == [
        baseline[1],
        {
            "source_path": "/tmp/paper-a.md",
            "provider": "new-provider",
            "model": "new-model",
            "error_type": "timeout_error",
            "error_message": "new stage a failure",
            "stage_name": "stage_a",
        },
    ]
