"""Small public value objects and adapter invocation helpers for pipeline steps."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .artifacts import Artifact


class AdapterProtocol(Protocol):
    """Marker protocol for Supervisor-provided adapter bundles."""


@dataclass
class PipelineAdapters:
    """Callable adapter bundle used by production and black-box tests."""

    ocr: Callable[..., Any] | None = None
    source_repair: Callable[..., Any] | None = None
    math_repair: Callable[..., Any] | None = None
    organize: Callable[..., Any] | None = None
    extract: Callable[..., Any] | None = None
    validate: Callable[..., Any] | None = None
    validation: Callable[..., Any] | None = None
    summary_repair: Callable[..., Any] | None = None
    translate: Callable[..., Any] | None = None
    translation_repair: Callable[..., Any] | None = None


@dataclass(frozen=True)
class PreviewArtifacts:
    """Protected completion artifacts exposed to Supervisor callers."""

    pdf: Path
    source_markdown: Path
    summary_json: Path
    translated_markdown: Path
    digest: str
    bibtex_status: str
    protected: tuple[Artifact, ...] = ()

    @property
    def preview_digest(self) -> str:
        return self.digest


@dataclass(frozen=True)
class WorkerResult:
    job_id: str
    status: str
    preview_digest: str | None = None
    preview: PreviewArtifacts | None = None
    failed_step: str | None = None
    error_type: str | None = None
    retryable: bool | None = None


def as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def as_markdown(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    pages = getattr(value, "pages", None)
    if isinstance(pages, list):
        return "\n\n".join(str(getattr(page, "markdown", "")) for page in pages)
    if isinstance(value, Mapping):
        markdown = value.get("markdown") or value.get("text") or value.get("content")
        if isinstance(markdown, str):
            return markdown
    return str(value)


def as_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("extract adapter must return a JSON object")


async def invoke(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call sync/async adapters while passing only supported arguments."""
    try:
        signature = inspect.signature(func)
        parameters = signature.parameters
        accepts_var_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()
        )
        accepted_kwargs = (
            kwargs
            if accepts_var_kwargs
            else {key: value for key, value in kwargs.items() if key in parameters}
        )
        positional = [
            param
            for param in parameters.values()
            if param.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        call_args = (
            args
            if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in parameters.values())
            else args[: len(positional)]
        )
        positional_names = {param.name for param in positional[: len(call_args)]}
        accepted_kwargs = {
            key: value for key, value in accepted_kwargs.items() if key not in positional_names
        }
    except (TypeError, ValueError):
        accepted_kwargs = kwargs
        call_args = args

    # Synchronous OCR/extraction/provider adapters must not occupy event-loop
    # thread: heartbeat task needs run while remote call is in flight.
    if inspect.iscoroutinefunction(func):
        result = func(*call_args, **accepted_kwargs)
    else:
        # A daemon thread keeps synchronous provider I/O off event loop.  The
        # result is polled instead of using executor callbacks: Supervisor's
        # short-lived event loops may otherwise block after file I/O.
        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def run_sync() -> None:
            try:
                outcome["value"] = func(*call_args, **accepted_kwargs)
            except BaseException as exc:  # propagate provider failure on loop
                outcome["error"] = exc
            finally:
                finished.set()

        threading.Thread(target=run_sync, name="pipeline-adapter", daemon=True).start()
        while not finished.is_set():
            await asyncio.sleep(0.01)
        if "error" in outcome:
            raise outcome["error"]
        result = outcome.get("value")
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = [
    "AdapterProtocol",
    "PipelineAdapters",
    "PreviewArtifacts",
    "WorkerResult",
    "as_bytes",
    "as_markdown",
    "as_summary",
    "invoke",
]
