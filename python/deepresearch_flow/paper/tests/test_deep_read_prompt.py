from __future__ import annotations

from deepresearch_flow.paper.extract import build_stage_schema
from deepresearch_flow.paper.schema import schema_to_prompt, validate_schema
from deepresearch_flow.paper.template_registry import (
    load_prompt_templates,
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


def test_deep_read_single_shot_prompt_requires_paper_archetype_before_module_a() -> None:
    schema = load_schema_for_template("deep_read")

    _system_prompt, user_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
    )

    field_line = next(
        line for line in user_prompt.splitlines() if "JSON fields" in line
    )

    assert "paper_archetype" in field_line
    assert field_line.index("paper_archetype") < field_line.index("module_a")


def test_deep_read_stage_prompt_accepts_explicit_archetype_hint() -> None:
    schema = load_schema_for_template("deep_read")

    _system_prompt, user_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
        stage_name="module_c4",
        stage_fields=[
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "module_c4",
        ],
        previous_outputs='{"module_a":"existing summary"}',
        paper_archetype_hint="survey",
    )

    _plain_system_prompt, plain_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
        stage_name="module_c4",
        stage_fields=[
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "module_c4",
        ],
        previous_outputs='{"module_a":"existing summary"}',
    )

    assert "Paper archetype already determined: survey." in user_prompt
    assert "Paper archetype already determined: survey." not in plain_prompt


def test_deep_read_module_a_prompt_mentions_archetype_field_and_all_allowed_values() -> None:
    schema = load_schema_for_template("deep_read")

    _system_prompt, user_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
        stage_name="module_a",
        stage_fields=[
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "paper_archetype",
            "module_a",
        ],
        previous_outputs="{}",
    )
    prompt_before_schema = user_prompt.split("JSON Schema:", 1)[0]

    assert "paper_archetype" in prompt_before_schema
    assert "survey" in prompt_before_schema
    assert "method" in prompt_before_schema
    assert "system" in prompt_before_schema
    assert "other" in prompt_before_schema


def test_deep_read_module_a_prompt_requires_single_best_fit_and_brief_justification() -> None:
    schema = load_schema_for_template("deep_read")

    _system_prompt, user_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
        stage_name="module_a",
        stage_fields=[
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "paper_archetype",
            "module_a",
        ],
        previous_outputs="{}",
    )

    assert "single best-fit" in user_prompt
    assert "brief" in user_prompt


def test_deep_read_prompt_is_written_in_english_while_output_language_remains_explicit() -> None:
    schema = load_schema_for_template("deep_read")

    _system_prompt, user_prompt = load_prompt_templates(
        "deep_read",
        content="# Test Paper",
        schema=schema_to_prompt(schema),
        output_language="zh",
        stage_name="module_a",
        stage_fields=[
            "paper_title",
            "paper_authors",
            "publication_date",
            "publication_venue",
            "paper_archetype",
            "module_a",
        ],
        previous_outputs="{}",
    )
    prompt_before_schema = user_prompt.split("JSON Schema:", 1)[0]

    assert "Current stage: module_a." in prompt_before_schema
    assert "Output language: zh." in prompt_before_schema
    assert "Use that language for the final content" in prompt_before_schema
    assert "The prompt itself is written in English." in prompt_before_schema
    assert "Do not switch the answer to English unless output_language requests English." in prompt_before_schema
    assert prompt_before_schema.count("Output language") >= 2
    assert "当前阶段" not in prompt_before_schema
    assert "请仅输出 JSON" not in prompt_before_schema
    assert "输出语言" not in prompt_before_schema
