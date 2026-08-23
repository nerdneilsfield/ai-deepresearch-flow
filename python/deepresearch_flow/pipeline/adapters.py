"""Production adapter construction for the administrative pipeline.

The worker owns orchestration.  This module owns construction of real OCR,
paper extraction, and translation calls from the existing service config
files.  Tests may still pass :class:`PipelineAdapters` directly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .steps import PipelineAdapters, as_markdown, as_summary, invoke


class ProductionAdapters(PipelineAdapters):
    """Construct real adapters from OCR and paper/service configuration paths."""

    @classmethod
    def from_config(
        cls,
        *,
        paper_config_path: str | Path | None = None,
        ocr_config_path: str | Path | None = None,
        ocr_backend: Any | None = None,
        staging_root: str | Path | None = None,
        extract_template: str | None = None,
        output_language: str = "en",
        ocr_model_map: Mapping[str, str] | tuple[tuple[str, str], ...] = (),
    ) -> "ProductionAdapters":
        """Build real adapters from service configuration paths.

        Test doubles may replace ``ocr_backend``; extraction and translation
        always use existing public provider APIs and never caller callables.
        """
        if paper_config_path is None:
            raise ValueError("production adapters require paper_config_path")
        from deepresearch_flow.paper.config import load_config

        paper_config = load_config(str(paper_config_path))

        if ocr_backend is None:
            if ocr_config_path is None:
                raise ValueError("production OCR construction requires ocr_config_path or ocr_backend")
            from deepresearch_flow.ocr.config import load_ocr_config
            from deepresearch_flow.ocr.factory import create_backend

            ocr_backend = create_backend(load_ocr_config(Path(ocr_config_path)).backend)

        staging_path = Path(staging_root) if staging_root is not None else None
        model_mapping = dict(ocr_model_map)

        schema_for_worker = None
        if paper_config is not None:
            from deepresearch_flow.paper.schema import load_schema, validate_schema
            from deepresearch_flow.paper.template_registry import load_schema_for_template

            schema_name = extract_template or "simple"
            schema_for_worker = (
                load_schema(paper_config.extract.schema_path)
                if paper_config.extract.schema_path and extract_template is None
                else load_schema_for_template(schema_name)
            )
            worker_validator = validate_schema(schema_for_worker)
        else:
            worker_validator = None

        async def ocr(pdf_path: Path, model_key: str | None = None, **_: Any) -> str:
            selected_model = _resolve_ocr_model(model_key, model_mapping)
            method = ocr_backend.ocr
            try:
                parameters = inspect.signature(method).parameters
            except (TypeError, ValueError):
                parameters = {}
            accepts_model = "model" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            model_parameter = "model" if accepts_model else "model_key" if "model_key" in parameters else None
            if model_parameter is None:
                configured_model = getattr(ocr_backend, "_model", getattr(ocr_backend, "model", None))
                if configured_model != selected_model:
                    raise ValueError("OCR backend cannot accept selected model")
                return as_markdown(await invoke(method, pdf_path))
            return as_markdown(await invoke(method, pdf_path, **{model_parameter: selected_model}))

        async def source_repair(markdown: str, **_: Any) -> str:
            from deepresearch_flow.recognize.organize import fix_markdown_text

            return await fix_markdown_text(markdown, "standard", False)

        async def math_repair(markdown: str, **_: Any) -> str:
            from deepresearch_flow.recognize.math import cleanup_formula, extract_math_spans

            updated = markdown
            for span in reversed(extract_math_spans(markdown, 80)):
                cleaned = cleanup_formula(span.content)
                if cleaned != span.content:
                    updated = (
                        updated[: span.start]
                        + span.delimiter
                        + cleaned
                        + span.delimiter
                        + updated[span.end :]
                    )
            return updated

        async def organize(markdown: str, **_: Any) -> str:
            from deepresearch_flow.recognize.organize import fix_markdown_text

            return await fix_markdown_text(markdown, "standard", True)

        async def summary_repair(summary: dict[str, Any], **_: Any) -> dict[str, Any]:
            if schema_for_worker is None:
                return summary
            from deepresearch_flow.paper.extract import normalize_response_keys

            return normalize_response_keys(summary, schema_for_worker)

        async def translation_repair(markdown: str, **_: Any) -> str:
            from deepresearch_flow.translator.fixers import fix_markdown

            return fix_markdown(markdown, "standard")

        async def default_validate(summary: dict[str, Any], **_: Any) -> bool:
            return isinstance(summary, dict) and (
                worker_validator is None or not list(worker_validator.iter_errors(summary))
            )

        async def extract(markdown: str, model_key: str | None = None, **kwargs: Any) -> dict[str, Any]:
            selector, provider, model_name = _resolve_model(
                paper_config, model_key, "extract"
            )
            template = extract_template or _first_template(kwargs.get("templates")) or "simple"
            from deepresearch_flow.paper.extract import extract_documents
            from deepresearch_flow.paper.schema import load_schema, validate_schema
            from deepresearch_flow.paper.template_registry import load_schema_for_template

            schema = (
                load_schema(paper_config.extract.schema_path)
                if paper_config.extract.schema_path and extract_template is None
                else load_schema_for_template(template)
            )
            validator = validate_schema(schema)
            root = staging_path or Path.cwd() / ".pipeline-staging"
            job_key = _safe_job_key(str(kwargs.get("job_id") or "standalone"))
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink():
                raise ValueError("extraction staging root must not be a symlink")
            root = root.resolve()
            job_directory = root / job_key
            if job_directory.exists() and job_directory.is_symlink():
                raise ValueError("extraction staging job directory must not be a symlink")
            stage = job_directory / "extract"
            stage.mkdir(parents=True, exist_ok=True)
            try:
                input_path = stage / "source.md"
                output_path = stage / "paper_infos.json"
                errors_path = stage / "paper_errors.json"
                input_path.write_text(markdown, encoding="utf-8")
                await extract_documents(
                    inputs=(str(input_path),),
                    glob_pattern=None,
                    provider=provider,
                    model=model_name,
                    schema=schema,
                    validator=validator,
                    config=paper_config,
                    output_path=output_path,
                    errors_path=errors_path,
                    split=False,
                    split_dir=None,
                    force=True,
                    force_stages=[],
                    retry_failed=False,
                    retry_failed_stages=False,
                    retry_list_path=None,
                    stage_dag=False,
                    start_idx=0,
                    end_idx=-1,
                    dry_run=False,
                    max_concurrency_override=1,
                    timeout_seconds=paper_config.extract.timeout,
                    prompt_template=template,
                    output_language=kwargs.get("output_language", output_language),
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
                    model_selector=selector,
                )
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                papers = payload.get("papers") if isinstance(payload, dict) else None
                if not isinstance(papers, list) or len(papers) != 1:
                    raise ValueError("paper extraction returned no single-job summary")
                return as_summary(papers[0])
            finally:
                if stage.exists() and not stage.is_symlink() and stage.parent == job_directory:
                    shutil.rmtree(stage)
                    if job_directory.exists() and not job_directory.is_symlink() and not any(job_directory.iterdir()):
                        job_directory.rmdir()

        async def translate(
            markdown: str,
            model_key: str | None = None,
            **kwargs: Any,
        ) -> str:
            selector, provider, model_name = _resolve_model(
                paper_config, model_key, "translate"
            )
            if provider is None or model_name is None:
                from deepresearch_flow.paper.routing import select_runtime_route

                route = select_runtime_route(paper_config, selector)
                provider = replace(route.provider, base=[route.base], models=[route.model])
                model_name = route.model.model_name
            from deepresearch_flow.paper.config import resolve_api_keys
            from deepresearch_flow.paper.routing import RoutePool
            from deepresearch_flow.translator.config import TranslateConfig
            from deepresearch_flow.translator.engine import MarkdownTranslator
            import httpx

            # Resolve env-backed credentials at adapter construction/use time;
            # RoutePool performs the same resolution for its selected route.
            resolve_api_keys(provider.api_keys)
            route_pool = RoutePool.from_selector(paper_config, selector, cooldown_seconds=1.0)
            target_language = str(kwargs.get("target_language") or output_language)
            translator_engine = MarkdownTranslator(
                TranslateConfig(
                    target_lang=target_language,
                    retry_times=max(1, paper_config.extract.max_retries),
                )
            )
            max_tokens = provider.max_tokens if provider.type == "claude" else None
            async with httpx.AsyncClient() as client:
                result = await translator_engine.translate(
                    text=markdown,
                    provider=provider,
                    model=model_name,
                    client=client,
                    api_keys=provider.api_keys,
                    timeout=paper_config.extract.timeout,
                    semaphore=asyncio.Semaphore(1),
                    throttle=None,
                    max_tokens=max_tokens,
                    fix_level=str(kwargs.get("fix_level") or "standard"),
                    progress=None,
                    fallback_provider=None,
                    fallback_model=None,
                    fallback_max_tokens=None,
                    fallback_provider_2=None,
                    fallback_model_2=None,
                    fallback_max_tokens_2=None,
                    fallback_retry_times=None,
                    fallback_retry_times_2=None,
                    format_enabled=True,
                    request_log=None,
                    dump_callback=None,
                    group_concurrency=1,
                    route_pool=route_pool,
                    fallback_route_pool=None,
                    fallback_route_pool_2=None,
                )
            return as_markdown(getattr(result, "translated_text", result))

        return cls(
            ocr=ocr,
            source_repair=source_repair,
            math_repair=math_repair,
            organize=organize,
            extract=extract,
            validate=default_validate,
            validation=default_validate,
            summary_repair=summary_repair,
            translate=translate,
            translation_repair=translation_repair,
        )


def _first_template(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def _safe_job_key(job_id: str) -> str:
    try:
        return str(uuid.UUID(job_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"deepresearch-flow:{job_id}"))


def _resolve_ocr_model(model_key: str | None, mapping: Mapping[str, str]) -> str:
    if not isinstance(model_key, str) or not model_key:
        raise ValueError("OCR model selection is required")
    if "/" in model_key:
        return model_key
    selected = mapping.get(model_key)
    if not selected:
        raise ValueError("OCR model selection cannot be mapped")
    return selected


def _resolve_model(
    paper_config: Any,
    model_key: str | None,
    step: str,
) -> tuple[Any, Any | None, str | None]:
    if not isinstance(model_key, str) or "/" not in model_key:
        raise ValueError(f"{step} model must be selected as provider/model")
    from deepresearch_flow.paper.routing import parse_model_selector, resolve_model_capability

    selector = parse_model_selector(model_key, paper_config)
    if selector.kind == "pool":
        return selector, None, None
    assert selector.fixed_model is not None
    provider_name, model_name = selector.fixed_model.split("/", 1)
    provider, _ = resolve_model_capability(provider_name, model_name, paper_config.providers)
    return selector, provider, model_name


def build_production_adapters(**kwargs: Any) -> ProductionAdapters:
    """Supervisor-facing real adapter constructor."""

    return ProductionAdapters.from_config(**kwargs)


__all__ = ["ProductionAdapters", "build_production_adapters"]
