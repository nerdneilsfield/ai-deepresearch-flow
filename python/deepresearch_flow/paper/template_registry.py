"""Template registry for extract prompts and render output."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import importlib.resources as resources

from jinja2 import Environment
from pathlib import Path


@dataclass(frozen=True)
class TemplateBundle:
    name: str
    prompt_system: str
    prompt_user: str
    schema_file: str
    render_template: str


@dataclass(frozen=True)
class StageDefinition:
    name: str
    fields: list[str]
    depends_on: list[str] | None = None


_TEMPLATES: dict[str, TemplateBundle] = {
    "simple": TemplateBundle(
        name="simple",
        prompt_system="simple_system.j2",
        prompt_user="simple_user.j2",
        schema_file="default_paper_schema.json",
        render_template="default_paper.md.j2",
    ),
    "simple_phi": TemplateBundle(
        name="simple_phi",
        prompt_system="simple_phi_system.j2",
        prompt_user="simple_phi_user.j2",
        schema_file="default_paper_schema.json",
        render_template="default_paper.md.j2",
    ),
    "deep_read": TemplateBundle(
        name="deep_read",
        prompt_system="deep_read_system.j2",
        prompt_user="deep_read_user.j2",
        schema_file="deep_read_schema.json",
        render_template="deep_read.md.j2",
    ),
    "deep_read_phi": TemplateBundle(
        name="deep_read_phi",
        prompt_system="deep_read_phi_system.j2",
        prompt_user="deep_read_phi_user.j2",
        schema_file="deep_read_phi_schema.json",
        render_template="deep_read_phi.md.j2",
    ),
    "eight_questions": TemplateBundle(
        name="eight_questions",
        prompt_system="eight_questions_system.j2",
        prompt_user="eight_questions_user.j2",
        schema_file="eight_questions_schema.json",
        render_template="eight_questions.md.j2",
    ),
    "eight_questions_phi": TemplateBundle(
        name="eight_questions_phi",
        prompt_system="eight_questions_phi_system.j2",
        prompt_user="eight_questions_phi_user.j2",
        schema_file="eight_questions_schema.json",
        render_template="eight_questions.md.j2",
    ),
    "three_pass": TemplateBundle(
        name="three_pass",
        prompt_system="three_pass_system.j2",
        prompt_user="three_pass_user.j2",
        schema_file="three_pass_schema.json",
        render_template="three_pass.md.j2",
    ),
}

_STAGES: dict[str, list[StageDefinition]] = {
    "deep_read": [
        StageDefinition("module_a", ["module_a"]),
        StageDefinition("module_b", ["module_b"]),
        StageDefinition("module_c1", ["module_c1"]),
        StageDefinition("module_c2", ["module_c2"]),
        StageDefinition("module_c3", ["module_c3"]),
        StageDefinition("module_c4", ["module_c4"]),
        StageDefinition("module_c5", ["module_c5"]),
        StageDefinition("module_c6", ["module_c6"]),
        StageDefinition("module_c7", ["module_c7"]),
        StageDefinition("module_c8", ["module_c8"]),
        StageDefinition("module_d", ["module_d"]),
        StageDefinition("module_e", ["module_e"]),
        StageDefinition("module_f", ["module_f"]),
        StageDefinition("module_g", ["module_g"]),
        StageDefinition("module_h", ["module_h"]),
    ],
    "deep_read_phi": [
        StageDefinition("module_m1", ["module_m1"]),
        StageDefinition("module_m2", ["module_m2"]),
        StageDefinition("module_m3", ["module_m3"]),
        StageDefinition("module_m4", ["module_m4"]),
        StageDefinition("module_m5", ["module_m5"]),
        StageDefinition("module_m6", ["module_m6"]),
    ],
    "eight_questions": [
        StageDefinition(
            "questions_1to4",
            ["question1", "question2", "question3", "question4"],
        ),
        StageDefinition(
            "questions_5to8",
            ["question5", "question6", "question7", "question8"],
        ),
    ],
    "eight_questions_phi": [
        StageDefinition(
            "questions_1to4",
            ["question1", "question2", "question3", "question4"],
        ),
        StageDefinition(
            "questions_5to8",
            ["question5", "question6", "question7", "question8"],
        ),
    ],
    "three_pass": [
        StageDefinition("step1_summary", ["step1_summary"]),
        StageDefinition("step2_analysis", ["step2_analysis"]),
        StageDefinition("step3_analysis", ["step3_analysis"]),
    ],
}


def list_template_names() -> list[str]:
    return sorted(_TEMPLATES.keys())


def list_template_names_in_registry_order() -> list[str]:
    return list(_TEMPLATES.keys())


def get_stage_definitions(template_name: str) -> list[StageDefinition]:
    return _STAGES.get(template_name, [])


def get_template_bundle(name: str) -> TemplateBundle:
    try:
        return _TEMPLATES[name]
    except KeyError as exc:
        available = ", ".join(list_template_names())
        raise ValueError(f"Unknown template '{name}'. Available: {available}") from exc


def load_schema_for_template(name: str) -> dict[str, Any]:
    bundle = get_template_bundle(name)
    schema_path = resources.files("deepresearch_flow.paper.schemas").joinpath(bundle.schema_file)
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_prompt_templates(
    name: str,
    *,
    content: str,
    schema: str,
    output_language: str,
    stage_name: str | None = None,
    stage_fields: list[str] | None = None,
    previous_outputs: str | None = None,
) -> tuple[str, str]:
    bundle = get_template_bundle(name)
    env = Environment()
    stage_fields = stage_fields or []
    previous_outputs = previous_outputs or ""
    system_text = _render_prompt_template(
        "deepresearch_flow.paper.prompt_templates",
        bundle.prompt_system,
        env,
        {
            "output_language": output_language,
            "stage_name": stage_name,
            "stage_fields": stage_fields,
            "previous_outputs": previous_outputs,
        },
    )
    user_text = _render_prompt_template(
        "deepresearch_flow.paper.prompt_templates",
        bundle.prompt_user,
        env,
        {
            "content": content,
            "schema": schema,
            "output_language": output_language,
            "stage_name": stage_name,
            "stage_fields": stage_fields,
            "previous_outputs": previous_outputs,
        },
    )
    return system_text, user_text


def load_render_template(name: str):
    bundle = get_template_bundle(name)
    template_path = resources.files("deepresearch_flow.paper.templates").joinpath(
        bundle.render_template
    )
    with template_path.open("r", encoding="utf-8") as handle:
        return Environment().from_string(handle.read())


def load_custom_prompt_templates(
    system_path: Path,
    user_path: Path,
    context: dict[str, Any],
) -> tuple[str, str]:
    env = Environment()
    system_text = env.from_string(system_path.read_text(encoding="utf-8")).render(**context)
    user_text = env.from_string(user_path.read_text(encoding="utf-8")).render(**context)
    return system_text, user_text


def _render_prompt_template(
    package: str, filename: str, env: Environment, context: dict[str, Any]
) -> str:
    template_path = resources.files(package).joinpath(filename)
    with template_path.open("r", encoding="utf-8") as handle:
        return env.from_string(handle.read()).render(**context)
