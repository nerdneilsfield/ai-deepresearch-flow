"""Input/output coverage of three-round storage and legacy report compatibility."""

from __future__ import annotations

import pytest

from deepresearch_flow.paper.extract import build_stage_schema, compute_stage_needs_run
from deepresearch_flow.paper.schema import validate_schema
from deepresearch_flow.paper.template_registry import (
    get_stage_definitions,
    load_render_template,
    load_schema_for_template,
)


def test_three_rounds_cover_legacy_fields_once_and_validate_merged_output() -> None:
    stages = get_stage_definitions("deep_read")
    assert len(stages) == 3
    fields = [field for stage in stages for field in stage.fields]
    assert len(fields) == len(set(fields))
    schema = load_schema_for_template("deep_read")
    metadata = {
        "paper_title": "Example",
        "paper_authors": ["Example Author"],
        "publication_date": "",
        "publication_venue": "",
    }
    merged = dict(metadata)
    completed = set()
    for stage in stages:
        assert set(stage.depends_on or []) <= completed
        payload = dict(metadata)
        payload.update({key: "survey" if key == "paper_archetype" else key for key in stage.fields})
        validator = validate_schema(build_stage_schema(schema, list(payload)))
        assert list(validator.iter_errors(payload)) == []
        merged.update(payload)
        completed.add(stage.name)
    assert {"module_f", "module_g", "module_h"} <= set(fields)
    assert list(validate_schema(schema).iter_errors(merged)) == []


@pytest.mark.parametrize("language", ["zh", "en"])
def test_legacy_report_preserves_all_module_content_without_archetype(language: str) -> None:
    keys = [
        "module_a",
        "module_b",
        *[f"module_c{i}" for i in range(1, 9)],
        "module_d",
        "module_e",
        "module_f",
        "module_g",
        "module_h",
    ]
    payload = {key: f"unique-content-{key}-end" for key in keys}
    rendered = load_render_template("deep_read").render(
        paper_title="Old paper", paper_authors=["Author"], output_language=language, **payload
    )
    assert rendered.count("\n## ") == 3
    for value in payload.values():
        assert rendered.count(value) == 1


@pytest.mark.parametrize("selector", ["module_b", "module_c3", "module_h", "module_c8", "module_g"])
@pytest.mark.parametrize("kind", ["force", "retry"])
def test_legacy_module_selector_reruns_owning_round(selector: str, kind: str) -> None:
    schema = load_schema_for_template("deep_read")
    selected = []
    for stage in get_stage_definitions("deep_read"):
        payload = {key: "method" if key == "paper_archetype" else "content" for key in stage.fields}
        payload["paper_title"] = "Example"
        payload["paper_authors"] = ["Author"]
        validator = validate_schema(
            build_stage_schema(schema, ["paper_title", "paper_authors", *stage.fields])
        )
        needed = compute_stage_needs_run(
            stage_name=stage.name,
            stage_record=payload,
            stage_meta_entry={"prompt_hash": "current"},
            force=False,
            force_stage_set={selector} if kind == "force" else set(),
            is_retry_full=False,
            retry_stages={selector} if kind == "retry" else None,
            prompt_hash="current",
            stage_validator=validator,
        )
        if needed:
            selected.append(stage)
    assert len(selected) == 1
    assert selector in selected[0].fields
