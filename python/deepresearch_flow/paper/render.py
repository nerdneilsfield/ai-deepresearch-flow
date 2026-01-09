"""Markdown rendering helpers for paper outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import importlib.resources as resources

from jinja2 import Environment, FileSystemLoader, Template

from deepresearch_flow.paper.template_registry import load_render_template
from deepresearch_flow.paper.utils import split_output_name, unique_split_name


def load_default_template() -> Template:
    template_path = resources.files("deepresearch_flow.paper.templates").joinpath(
        "default_paper.md.j2"
    )
    with template_path.open("r", encoding="utf-8") as handle:
        return Environment().from_string(handle.read())


def resolve_render_template(
    template_path: str | None,
    template_name: str | None,
    template_dir: str | None,
) -> Template:
    if sum(bool(item) for item in (template_path, template_name, template_dir)) > 1:
        raise ValueError(
            "Use only one of template path, template name, or template directory"
        )
    if template_dir:
        template_path = str(Path(template_dir) / "render.j2")
    if template_path:
        return Environment(loader=FileSystemLoader(Path(template_path).parent)).get_template(
            Path(template_path).name
        )
    if template_name:
        return load_render_template(template_name)
    return load_default_template()


def render_papers(
    papers: Iterable[dict[str, Any]],
    output_dir: Path,
    template: Template,
    fallback_language: str,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    count = 0
    for paper in papers:
        source_path = paper.get("source_path")
        base = paper.get("paper_title") or "paper"
        if source_path:
            base = split_output_name(Path(source_path))
        filename = unique_split_name(base, used, source_path or base)
        context = dict(paper)
        if not context.get("output_language"):
            context["output_language"] = fallback_language
        markdown = template.render(**context)
        (output_dir / f"{filename}.md").write_text(markdown, encoding="utf-8")
        count += 1
    return count
