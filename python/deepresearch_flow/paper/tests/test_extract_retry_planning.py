from __future__ import annotations

from jsonschema import Draft7Validator

from deepresearch_flow.paper.extract import (
    build_document_validation_error,
    plan_sequential_stage_tasks,
)
from deepresearch_flow.paper.template_registry import (
    get_stage_definitions,
    StageDefinition,
)


def test_plan_sequential_stage_tasks_only_queues_targeted_retry_stages() -> None:
    planned = plan_sequential_stage_tasks(
        stage_definitions=[
            StageDefinition("stage_a", ["field_a"]),
            StageDefinition("stage_b", ["field_b"]),
            StageDefinition("stage_c", ["field_c"]),
        ],
        metadata_fields=["paper_title"],
        stages={
            "stage_a": {"field_a": "ok"},
            "stage_b": {"field_b": "ok"},
            "stage_c": {"field_c": "ok"},
        },
        stage_meta={
            "stage_a": {"prompt_hash": "same"},
            "stage_b": {"prompt_hash": "same"},
            "stage_c": {"prompt_hash": "same"},
        },
        prompt_hash_map={"stage_a": "same", "stage_b": "same", "stage_c": "same"},
        stage_validator_map={
            "stage_a": Draft7Validator({"type": "object"}),
            "stage_b": Draft7Validator({"type": "object"}),
            "stage_c": Draft7Validator({"type": "object"}),
        },
        force=False,
        force_stage_set=set(),
        retry_stages_mode=True,
        is_retry_full=False,
        retry_stages={"stage_b"},
    )

    assert [stage.name for stage in planned] == ["stage_b"]
    assert [stage.fields for stage in planned] == [["paper_title", "field_b"]]


def test_plan_sequential_stage_tasks_keeps_missing_stage_queued() -> None:
    planned = plan_sequential_stage_tasks(
        stage_definitions=[
            StageDefinition("stage_a", ["field_a"]),
            StageDefinition("stage_b", ["field_b"]),
            StageDefinition("stage_c", ["field_c"]),
        ],
        metadata_fields=["paper_title"],
        stages={
            "stage_a": {"field_a": "ok"},
            "stage_c": {"field_c": "ok"},
        },
        stage_meta={
            "stage_a": {"prompt_hash": "same"},
            "stage_c": {"prompt_hash": "same"},
        },
        prompt_hash_map={"stage_a": "same", "stage_b": "same", "stage_c": "same"},
        stage_validator_map={
            "stage_a": Draft7Validator({"type": "object"}),
            "stage_b": Draft7Validator({"type": "object"}),
            "stage_c": Draft7Validator({"type": "object"}),
        },
        force=False,
        force_stage_set=set(),
        retry_stages_mode=True,
        is_retry_full=False,
        retry_stages={"stage_b"},
    )

    assert [stage.name for stage in planned] == ["stage_b"]


def test_plan_sequential_stage_tasks_keeps_full_retry_as_full_queue() -> None:
    planned = plan_sequential_stage_tasks(
        stage_definitions=[
            StageDefinition("stage_a", ["field_a"]),
            StageDefinition("stage_b", ["field_b"]),
            StageDefinition("stage_c", ["field_c"]),
        ],
        metadata_fields=["paper_title"],
        stages={
            "stage_a": {"field_a": "ok"},
            "stage_b": {"field_b": "ok"},
            "stage_c": {"field_c": "ok"},
        },
        stage_meta={
            "stage_a": {"prompt_hash": "same"},
            "stage_b": {"prompt_hash": "same"},
            "stage_c": {"prompt_hash": "same"},
        },
        prompt_hash_map={"stage_a": "same", "stage_b": "same", "stage_c": "same"},
        stage_validator_map={
            "stage_a": Draft7Validator({"type": "object"}),
            "stage_b": Draft7Validator({"type": "object"}),
            "stage_c": Draft7Validator({"type": "object"}),
        },
        force=False,
        force_stage_set=set(),
        retry_stages_mode=True,
        is_retry_full=True,
        retry_stages={"stage_b"},
    )

    assert [stage.name for stage in planned] == ["stage_a", "stage_b", "stage_c"]


def test_plan_sequential_stage_tasks_queues_prompt_hash_mismatch_even_if_not_targeted() -> None:
    planned = plan_sequential_stage_tasks(
        stage_definitions=[
            StageDefinition("stage_a", ["field_a"]),
            StageDefinition("stage_b", ["field_b"]),
        ],
        metadata_fields=["paper_title"],
        stages={
            "stage_a": {"field_a": "ok"},
            "stage_b": {"field_b": "ok"},
        },
        stage_meta={
            "stage_a": {"prompt_hash": "stale"},
            "stage_b": {"prompt_hash": "same"},
        },
        prompt_hash_map={"stage_a": "fresh", "stage_b": "same"},
        stage_validator_map={
            "stage_a": Draft7Validator({"type": "object"}),
            "stage_b": Draft7Validator({"type": "object"}),
        },
        force=False,
        force_stage_set=set(),
        retry_stages_mode=True,
        is_retry_full=False,
        retry_stages={"stage_b"},
    )

    assert [stage.name for stage in planned] == ["stage_a", "stage_b"]


def test_build_document_validation_error_is_not_stage_scoped() -> None:
    error = build_document_validation_error(
        path="/tmp/paper.md",
        provider="test-provider",
        model="test-model",
        message="Schema validation failed: missing field",
    )

    assert error.stage_name is None


def test_deep_read_stage_a_requests_archetype_and_module_a() -> None:
    stage_definitions = get_stage_definitions("deep_read")

    assert stage_definitions
    assert stage_definitions[0].name == "module_a"
    assert stage_definitions[0].fields == ["paper_archetype", "module_a"]


def test_deep_read_non_module_a_stages_depend_on_module_a() -> None:
    stage_definitions = get_stage_definitions("deep_read")

    assert stage_definitions
    for stage_def in stage_definitions[1:]:
        assert stage_def.depends_on is not None
        assert "module_a" in stage_def.depends_on
