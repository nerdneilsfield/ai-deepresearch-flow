from __future__ import annotations

from deepresearch_flow.paper.template_registry import load_prompt_templates


def test_simple_prompt_requires_abstract_and_key_points_sections() -> None:
    _system_prompt, user_prompt = load_prompt_templates(
        "simple",
        content="# Test Paper",
        schema="{}",
        output_language="zh",
    )
    instructions = user_prompt.split("Document content:", 1)[0]

    assert 'For the JSON field "summary"' in instructions
    assert "### 摘要" in instructions
    assert "### 关键要点" in instructions
    assert "8–12 independent" in instructions
    assert "flat Markdown bullets" in instructions
    assert "nested bullets" in instructions
    assert "Write a single-paragraph summary" not in instructions
