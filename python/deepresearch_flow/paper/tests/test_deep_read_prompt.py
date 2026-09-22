from __future__ import annotations

from deepresearch_flow.paper.extract import build_stage_schema, normalize_response_keys
from deepresearch_flow.paper.schema import validate_schema
from deepresearch_flow.paper.template_registry import (
    load_schema_for_template,
)


def test_deep_read_schema_requires_archetype_enum() -> None:
    schema = load_schema_for_template("deep_read")

    assert "paper_archetype" in schema["required"]
    assert schema["properties"]["paper_archetype"] == {
        "type": "string",
        "enum": ["survey", "method", "system", "other"],
    }


def test_deep_read_stage_a_schema_accepts_minimal_archetype_payload() -> None:
    schema = load_schema_for_template("deep_read")
    stage_schema = build_stage_schema(
        schema,
        [
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "paper_archetype",
            "module_a",
        ],
    )
    assert stage_schema["properties"]["paper_archetype"] == {
        "type": "string",
        "enum": ["survey", "method", "system", "other"],
    }

    validator = validate_schema(stage_schema)

    errors = sorted(
        validator.iter_errors(
            {
                "paper_title": "A Survey of Test-Time Adaptation",
                "paper_authors": ["Alice Example"],
                "publication_date": "",
                "publication_venue": "",
                "paper_archetype": "survey",
                "module_a": "## 论文类型\n- 综述",
            }
        ),
        key=lambda err: list(err.path),
    )

    assert errors == []


def test_deep_read_full_schema_accepts_single_shot_payload_with_archetype() -> None:
    schema = load_schema_for_template("deep_read")
    validator = validate_schema(schema)

    payload = {
        "paper_title": "A Survey of Test-Time Adaptation",
        "paper_authors": ["Alice Example"],
        "publication_date": "",
        "publication_venue": "",
        "paper_archetype": "survey",
        "module_a": "module a",
        "module_b": "module b",
        "module_c1": "module c1",
        "module_c2": "module c2",
        "module_c3": "module c3",
        "module_c4": "module c4",
        "module_c5": "module c5",
        "module_c6": "module c6",
        "module_c7": "module c7",
        "module_c8": "module c8",
        "module_d": "module d",
        "module_e": "module e",
        "module_h": "module h",
    }
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))

    assert errors == []


def test_deep_read_full_schema_rejects_unknown_archetype() -> None:
    schema = load_schema_for_template("deep_read")
    validator = validate_schema(schema)

    payload = {
        "paper_title": "A Survey of Test-Time Adaptation",
        "paper_authors": ["Alice Example"],
        "publication_date": "",
        "publication_venue": "",
        "paper_archetype": "survey_or_method",
        "module_a": "module a",
        "module_b": "module b",
        "module_c1": "module c1",
        "module_c2": "module c2",
        "module_c3": "module c3",
        "module_c4": "module c4",
        "module_c5": "module c5",
        "module_c6": "module c6",
        "module_c7": "module c7",
        "module_c8": "module c8",
        "module_d": "module d",
        "module_e": "module e",
        "module_h": "module h",
    }

    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))

    assert errors


def test_deep_read_normalization_preserves_paper_archetype_metadata() -> None:
    schema = load_schema_for_template("deep_read")
    validator = validate_schema(schema)

    normalized = normalize_response_keys(
        {
            "paper_title": "A Method Paper",
            "paper_authors": ["Alice Example"],
            "publication_date": "",
            "publication_venue": "",
            "paperArchetype": "method",
            "module_a": "module a",
            "module_b": "module b",
            "module_c1": "module c1",
            "module_c2": "module c2",
            "module_c3": "module c3",
            "module_c4": "module c4",
            "module_c5": "module c5",
            "module_c6": "module c6",
            "module_c7": "module c7",
            "module_c8": "module c8",
            "module_d": "module d",
            "module_e": "module e",
            "module_h": "module h",
        },
        schema,
    )

    assert normalized["paper_archetype"] == "method"
    assert "paperArchetype" not in normalized
    assert normalize_response_keys(normalized, schema) == normalized
    assert sorted(validator.iter_errors(normalized), key=lambda err: list(err.path)) == []


def test_deep_read_normalization_still_rejects_invalid_archetype_values() -> None:
    schema = load_schema_for_template("deep_read")
    validator = validate_schema(schema)

    normalized = normalize_response_keys(
        {
            "paper_title": "A Borderline Paper",
            "paper_authors": ["Alice Example"],
            "publication_date": "",
            "publication_venue": "",
            "paperArchetype": "survey_or_method",
            "module_a": "module a",
            "module_b": "module b",
            "module_c1": "module c1",
            "module_c2": "module c2",
            "module_c3": "module c3",
            "module_c4": "module c4",
            "module_c5": "module c5",
            "module_c6": "module c6",
            "module_c7": "module c7",
            "module_c8": "module c8",
            "module_d": "module d",
            "module_e": "module e",
            "module_h": "module h",
        },
        schema,
    )

    errors = sorted(validator.iter_errors(normalized), key=lambda err: list(err.path))

    assert normalized["paper_archetype"] == "survey_or_method"
    assert errors
