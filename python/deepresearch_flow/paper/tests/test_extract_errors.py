from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from deepresearch_flow.paper.config import (
    DEFAULT_EXTRACT,
    DEFAULT_RENDER,
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
)
from deepresearch_flow.paper.extract import (
    ExtractionError,
    _select_doc_runtime,
    call_with_retries,
    extract_documents,
    filter_results_with_errors,
    merge_retry_error_entries,
)
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.schema import validate_schema


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


def test_filter_results_with_errors_drops_documents_with_unresolved_errors() -> None:
    entries = [
        {"source_path": "/tmp/paper-a.md", "paper_title": "A"},
        {"source_path": "/tmp/paper-b.md", "paper_title": "B"},
    ]

    filtered = filter_results_with_errors(
        entries,
        [
            {
                "source_path": "/tmp/paper-b.md",
                "provider": "provider",
                "model": "model",
                "error_type": "validation_error",
                "error_message": "bad output",
                "stage_name": None,
            }
        ],
    )

    assert filtered == [{"source_path": "/tmp/paper-a.md", "paper_title": "A"}]


def test_call_with_retries_falls_back_to_plain_json_when_model_lacks_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ProviderConfig(
        name="test-provider",
        type="ollama",
        base=[
            BaseConfig(
                url="http://localhost:11434",
                weight=1,
                key=[KeyConfig(value="test-key", weight=1)],
            )
        ],
        models=[
            ModelCapability(
                model_name="dummy-model",
                is_stream=False,
                is_support_json_schema=False,
                is_support_json_object=False,
            )
        ],
        api_version=None,
        deployment=None,
        project_id=None,
        location=None,
        credentials_path=None,
        anthropic_version=None,
        max_tokens=None,
        extra_headers={},
        system_prompt=None,
        user_prompt=None,
    )
    schema = {
        "type": "object",
        "required": ["paper_title", "paper_authors"],
        "properties": {
            "paper_title": {"type": "string"},
            "paper_authors": {"type": "array", "items": {"type": "string"}},
        },
    }
    validator = validate_schema(schema)

    async def fake_call_provider(*args, **kwargs):
        assert args[6] == "none"
        return '{"paper_title":"Test","paper_authors":["Alice"]}'

    monkeypatch.setattr("deepresearch_flow.paper.extract.call_provider", fake_call_provider)

    async def _run() -> dict[str, object]:
        async with httpx.AsyncClient() as client:
            return await call_with_retries(
                provider,
                "dummy-model",
                [{"role": "user", "content": "test"}],
                schema,
                None,
                timeout=1.0,
                structured_mode="none",
                max_retries=1,
                backoff_base_seconds=1.0,
                backoff_max_seconds=1.0,
                client=client,
                validator=validator,
            )

    result = asyncio.run(_run())
    assert result == {"paper_title": "Test", "paper_authors": ["Alice"]}


def test_select_doc_runtime_uses_selected_model_capability_for_structured_mode() -> None:
    provider = ProviderConfig(
        name="test-provider",
        type="openai_compatible",
        base=[
            BaseConfig(
                url="https://api.example.com/v1",
                weight=1,
                key=[KeyConfig(value="key-a", weight=1)],
            )
        ],
        models=[
            ModelCapability(
                model_name="schema-model",
                is_stream=True,
                is_support_json_schema=True,
                is_support_json_object=True,
            ),
            ModelCapability(
                model_name="plain-model",
                is_stream=True,
                is_support_json_schema=False,
                is_support_json_object=False,
            ),
        ],
        api_version=None,
        deployment=None,
        project_id=None,
        location=None,
        credentials_path=None,
        anthropic_version=None,
        max_tokens=None,
        extra_headers={},
        system_prompt=None,
        user_prompt=None,
    )
    config = PaperConfig(
        extract=replace(DEFAULT_EXTRACT, output=Path("out.json"), errors=Path("err.json")),
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="test-provider/plain-model", weight=1)],
    )

    routed_provider, model_name, structured_mode, _ = _select_doc_runtime(
        config=config,
        provider=provider,
        model_name="plain-model",
        model_selector=None,
        cooldown_seconds=1.0,
        verbose=False,
    )

    assert model_name == "plain-model"
    assert structured_mode == "none"
    assert routed_provider.models[0].model_name == "plain-model"


def test_extract_documents_retry_failure_drops_stale_output_entry(
    tmp_path: Path, monkeypatch
) -> None:
    doc_a = tmp_path / "paper-a.md"
    doc_b = tmp_path / "paper-b.md"
    doc_a.write_text("doc a", encoding="utf-8")
    doc_b.write_text("doc b", encoding="utf-8")

    output_path = tmp_path / "paper_infos.json"
    errors_path = tmp_path / "paper_errors.json"
    output_path.write_text(
        f"""\
{{
  "template_tag": "simple",
  "papers": [
    {{
      "source_path": "{doc_a.resolve()}",
      "paper_title": "A",
      "paper_authors": ["Alice"]
    }},
    {{
      "source_path": "{doc_b.resolve()}",
      "paper_title": "B",
      "paper_authors": ["Bob"]
    }}
  ]
}}
""",
        encoding="utf-8",
    )
    errors_path.write_text(
        f"""\
[
  {{
    "source_path": "{doc_b.resolve()}",
    "provider": "old-provider",
    "model": "old-model",
    "error_type": "validation_error",
    "error_message": "old failure",
    "stage_name": null
  }}
]
""",
        encoding="utf-8",
    )

    provider = ProviderConfig(
        name="test-provider",
        type="ollama",
        base=[
            BaseConfig(
                url="http://localhost:11434",
                weight=1,
                key=[KeyConfig(value="test-key", weight=1)],
            )
        ],
        models=[
            ModelCapability(
                model_name="dummy-model",
                is_stream=False,
                is_support_json_schema=False,
                is_support_json_object=False,
            )
        ],
        api_version=None,
        deployment=None,
        project_id=None,
        location=None,
        credentials_path=None,
        anthropic_version=None,
        max_tokens=None,
        extra_headers={},
        system_prompt=None,
        user_prompt=None,
    )
    config = PaperConfig(
        extract=replace(
            DEFAULT_EXTRACT,
            output=str(output_path),
            errors=str(errors_path),
            max_concurrency=1,
            max_retries=1,
        ),
        render=DEFAULT_RENDER,
        providers=[provider],
        main_model=[MainModelConfig(model="test-provider/dummy-model", weight=1)],
    )
    schema = {
        "type": "object",
        "required": ["paper_title", "paper_authors"],
        "properties": {
            "paper_title": {"type": "string"},
            "paper_authors": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": True,
    }
    validator = validate_schema(schema)

    async def fake_call_with_retries(*args, **kwargs):
        raise ProviderError("still failing", error_type="validation_error")

    monkeypatch.setattr(
        "deepresearch_flow.paper.extract.call_with_retries",
        fake_call_with_retries,
    )
    monkeypatch.setattr(
        "deepresearch_flow.paper.extract.log_extraction_failure",
        lambda *args, **kwargs: None,
    )

    asyncio.run(
        extract_documents(
            inputs=(str(doc_a), str(doc_b)),
            glob_pattern=None,
            provider=provider,
            model="dummy-model",
            schema=schema,
            validator=validator,
            config=config,
            output_path=output_path,
            errors_path=errors_path,
            split=False,
            split_dir=None,
            force=False,
            force_stages=[],
            retry_failed=True,
            retry_failed_stages=False,
            retry_list_path=None,
            stage_dag=False,
            start_idx=0,
            end_idx=-1,
            dry_run=False,
            max_concurrency_override=1,
            timeout_seconds=1.0,
            prompt_template="simple",
            output_language="en",
            custom_prompt=False,
            prompt_system_path=None,
            prompt_user_path=None,
            render_md=False,
            render_output_dir=None,
            render_template_path=None,
            render_template_name=None,
            render_template_dir=None,
            sleep_every=None,
            sleep_time=None,
            verbose=False,
        )
    )

    output_data = output_path.read_text(encoding="utf-8")
    errors_data = errors_path.read_text(encoding="utf-8")

    assert str(doc_a.resolve()) in output_data
    assert str(doc_b.resolve()) not in output_data
    assert str(doc_b.resolve()) in errors_data
    assert "still failing" in errors_data
