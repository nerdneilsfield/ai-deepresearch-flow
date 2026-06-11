"""Utility commands for capability probes and maintenance."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any

import click
import coloredlogs
import httpx

from deepresearch_flow.paper.config import (
    BaseConfig,
    KeyConfig,
    MainModelConfig,
    ModelCapability,
    PaperConfig,
    ProviderConfig,
    load_config,
    resolve_api_key_configs,
)
from deepresearch_flow.paper.llm import call_provider
from deepresearch_flow.paper.routing import (
    RuntimeRoute,
    parse_model_selector,
    provider_window_error_as_click,
    select_runtime_route,
)
from deepresearch_flow.paper.providers.base import ProviderError

logger = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(level=level, fmt="%(asctime)s %(levelname)s %(message)s")


@click.group()
def utils() -> None:
    """Utility commands."""


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _is_explicit_unsupported_error(message: str) -> bool:
    normalized = (message or "").lower()
    return ("not support" in normalized) or ("not valid" in normalized)


def _serialize_key_entry(key: KeyConfig) -> str:
    parts = [
        f"value = {_toml_string(key.value)}",
        f"weight = {key.weight}",
    ]
    if key.quota_duration is not None:
        parts.append(f"quota_duration = {key.quota_duration}")
    if key.reset_time is not None:
        parts.append(f"reset_time = {_toml_string(key.reset_time)}")
    if key.quota_error_tokens:
        tokens = ", ".join(_toml_string(token) for token in key.quota_error_tokens)
        parts.append(f"quota_error_tokens = [{tokens}]")
    return "{ " + ", ".join(parts) + " }"


def _serialize_base_entry(base: BaseConfig) -> str:
    parts = [
        f"url = {_toml_string(base.url)}",
        f"weight = {base.weight}",
    ]
    if base.active_windows:
        windows = ", ".join(_toml_string(window) for window in base.active_windows)
        parts.append(f"active_windows = [{windows}]")
    if base.active_timezone is not None:
        parts.append(f"active_timezone = {_toml_string(base.active_timezone)}")
    keys = ", ".join(_serialize_key_entry(key) for key in base.key)
    parts.append(f"key = [{keys}]")
    return "{ " + ", ".join(parts) + " }"


def _serialize_model_entry(model: ModelCapability) -> str:
    return (
        "{ "
        + ", ".join(
            [
                f"model_name = {_toml_string(model.model_name)}",
                f"is_stream = {_toml_bool(model.is_stream)}",
                f"is_support_json_schema = {_toml_bool(model.is_support_json_schema)}",
                f"is_support_json_object = {_toml_bool(model.is_support_json_object)}",
            ]
        )
        + " }"
    )


def _serialize_main_model_entry(item: MainModelConfig) -> str:
    return "{ " + f"model = {_toml_string(item.model)}, weight = {item.weight}" + " }"


def dump_weighted_config(config: PaperConfig) -> str:
    lines: list[str] = []

    lines.append("main_model = [")
    for item in config.main_model:
        lines.append(f"  {_serialize_main_model_entry(item)},")
    lines.append("]")
    lines.append("")

    lines.append("[extract]")
    lines.append(f"output = {_toml_string(config.extract.output)}")
    lines.append(f"errors = {_toml_string(config.extract.errors)}")
    lines.append(f"max_concurrency = {config.extract.max_concurrency}")
    lines.append(f"max_retries = {config.extract.max_retries}")
    lines.append(f"timeout = {config.extract.timeout}")
    lines.append(f"backoff_base_seconds = {config.extract.backoff_base_seconds}")
    lines.append(f"backoff_max_seconds = {config.extract.backoff_max_seconds}")
    lines.append(f"pause_threshold_seconds = {config.extract.pause_threshold_seconds}")
    lines.append(f"truncate_strategy = {_toml_string(config.extract.truncate_strategy)}")
    lines.append(f"truncate_max_chars = {config.extract.truncate_max_chars}")
    lines.append(f"cost_estimate = {_toml_bool(config.extract.cost_estimate)}")
    lines.append(f"stage_dag = {_toml_bool(config.extract.stage_dag)}")
    if config.extract.schema_path is not None:
        lines.append(f"schema_path = {_toml_string(config.extract.schema_path)}")
    lines.append("")

    lines.append("[render]")
    if config.render.template_path is not None:
        lines.append(f"template_path = {_toml_string(config.render.template_path)}")
    lines.append("")

    for provider in config.providers:
        lines.append("[[providers]]")
        lines.append(f"name = {_toml_string(provider.name)}")
        lines.append(f"type = {_toml_string(provider.type)}")
        if provider.api_version is not None:
            lines.append(f"api_version = {_toml_string(provider.api_version)}")
        if provider.deployment is not None:
            lines.append(f"deployment = {_toml_string(provider.deployment)}")
        if provider.project_id is not None:
            lines.append(f"project_id = {_toml_string(provider.project_id)}")
        if provider.location is not None:
            lines.append(f"location = {_toml_string(provider.location)}")
        if provider.credentials_path is not None:
            lines.append(f"credentials_path = {_toml_string(provider.credentials_path)}")
        if provider.anthropic_version is not None:
            lines.append(f"anthropic_version = {_toml_string(provider.anthropic_version)}")
        if provider.max_tokens is not None:
            lines.append(f"max_tokens = {provider.max_tokens}")
        if provider.system_prompt is not None:
            lines.append(f"system_prompt = {_toml_string(provider.system_prompt)}")
        if provider.user_prompt is not None:
            lines.append(f"user_prompt = {_toml_string(provider.user_prompt)}")
        if provider.extra_headers is not None:
            headers = ", ".join(
                f"{_toml_string(key)} = {_toml_string(value)}"
                for key, value in provider.extra_headers.items()
            )
            lines.append(f"extra_headers = {{ {headers} }}")
        lines.append("base = [")
        for base in provider.base:
            lines.append(f"  {_serialize_base_entry(base)},")
        lines.append("]")
        lines.append("models = [")
        for model in provider.models:
            lines.append(f"  {_serialize_model_entry(model)},")
        lines.append("]")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _probe_async(route: RuntimeRoute, mode: str) -> str:
    messages = [
        {"role": "system", "content": "Return only the requested structured output."},
        {"role": "user", "content": "probe"},
    ]
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "string"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    if mode == "json_object":
        schema = None

    async def _run() -> str:
        resolved_key = resolve_api_key_configs([route.key])[0].value
        async with httpx.AsyncClient() as client:
            return await call_provider(
                route.provider,
                route.model.model_name,
                messages,
                schema,
                resolved_key,
                timeout=30.0,
                structured_mode=mode,
                client=client,
                max_tokens=route.provider.max_tokens,
            )

    return asyncio.run(_run())


def probe_model_mode(route: RuntimeRoute, mode: str) -> bool:
    content = _probe_async(route, mode)
    if mode == "json_schema":
        json.loads(content)
    elif mode == "json_object":
        json.loads(content)
    return True


def _update_model_flags(config: PaperConfig, updates: dict[str, dict[str, bool]]) -> PaperConfig:
    new_providers: list[ProviderConfig] = []
    for provider in config.providers:
        new_models: list[ModelCapability] = []
        for model in provider.models:
            key = f"{provider.name}/{model.model_name}"
            if key in updates:
                flags = updates[key]
                new_models.append(
                    replace(
                        model,
                        is_support_json_schema=flags.get(
                            "json_schema", model.is_support_json_schema
                        ),
                        is_support_json_object=flags.get(
                            "json_object", model.is_support_json_object
                        ),
                    )
                )
            else:
                new_models.append(model)
        new_providers.append(replace(provider, models=new_models))
    return replace(config, providers=new_providers)


@utils.command("test-mode")
@click.option(
    "-c",
    "--config",
    "config_path",
    default="config.toml",
    type=click.Path(path_type=Path),
    help="Path to config.toml",
)
@click.option(
    "-m",
    "--model",
    "model_refs",
    multiple=True,
    required=True,
    help="provider/model reference to probe (repeatable)",
)
@click.option("--write-back", is_flag=True, help="Write detected probe results back to config.toml")
def test_mode(config_path: Path, model_refs: tuple[str, ...], write_back: bool) -> None:
    config = load_config(str(config_path))
    results: dict[str, dict[str, bool]] = {}

    for model_ref in model_refs:
        if "/" not in model_ref:
            raise click.ClickException("--model must be in provider/model format")
        try:
            selector = parse_model_selector(model_ref, config)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if selector.kind != "single" or not selector.fixed_model:
            raise click.ClickException("test-mode only accepts provider/model references")
        try:
            with provider_window_error_as_click():
                route = select_runtime_route(config, selector)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        route_key = f"{route.provider.name}/{route.model.model_name}"
        results[route_key] = {}
        for mode in ("json_schema", "json_object"):
            try:
                supported = probe_model_mode(route, mode)
            except Exception as exc:  # noqa: BLE001 - report probe failure to the CLI
                if _is_explicit_unsupported_error(str(exc)):
                    supported = False
                    results[route_key][mode] = supported
                    click.echo(f"{route_key} {mode}: unsupported ({exc})")
                    continue
                click.echo(f"{route_key} {mode}: probe failed ({exc})", err=True)
                raise click.ClickException(f"Probe failed for {route_key} {mode}") from exc
            results[route_key][mode] = supported
            click.echo(f"{route_key} {mode}: {'supported' if supported else 'unsupported'}")

    if write_back:
        updated = _update_model_flags(config, results)
        config_path.write_text(dump_weighted_config(updated), encoding="utf-8")
        click.echo(f"Wrote probe results back to {config_path}")
