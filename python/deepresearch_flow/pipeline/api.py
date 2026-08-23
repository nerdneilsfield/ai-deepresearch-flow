"""Authenticated HTTP API for the optional administrative pipeline.

This module is deliberately a small adapter around the durable pipeline
services.  HTTP handlers expose identifiers and review metadata only; queue
leases, work paths, and provider configuration never cross this boundary.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from deepresearch_flow.paper.snapshot.auth import BearerAuthError, verify_bearer

from .artifacts import Artifact, ArtifactStore
from .config import PipelineConfig
from .ingestion import BatchIngestor, UploadPart
from .state import PipelineState

logger = logging.getLogger(__name__)

_PAGE_DEFAULT = 20
_PAGE_MAX = 100
_ARTIFACTS: dict[str, tuple[str, str, str]] = {
    "pdf": ("preview_pdf", "application/pdf", "paper.pdf"),
    "preview_pdf": ("preview_pdf", "application/pdf", "paper.pdf"),
    "source_markdown": ("preview_source_md", "text/markdown; charset=utf-8", "source.md"),
    "source": ("preview_source_md", "text/markdown; charset=utf-8", "source.md"),
    "source_md": ("preview_source_md", "text/markdown; charset=utf-8", "source.md"),
    "preview_source_md": ("preview_source_md", "text/markdown; charset=utf-8", "source.md"),
    "summary_json": ("preview_summary_json", "application/json", "summary.json"),
    "summary": ("preview_summary_json", "application/json", "summary.json"),
    "preview_summary_json": ("preview_summary_json", "application/json", "summary.json"),
    "translated_markdown": (
        "preview_translated_md",
        "text/markdown; charset=utf-8",
        "translated.md",
    ),
    "translated": (
        "preview_translated_md",
        "text/markdown; charset=utf-8",
        "translated.md",
    ),
    "translated_md": (
        "preview_translated_md",
        "text/markdown; charset=utf-8",
        "translated.md",
    ),
    "preview_translated_md": (
        "preview_translated_md",
        "text/markdown; charset=utf-8",
        "translated.md",
    ),
}


def _json_error(error: str, detail: str, status: int) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status)


def _authorized(request: Request, token: str) -> bool:
    if not token:
        return False
    try:
        verify_bearer(request.headers.get("authorization"), token)
    except BearerAuthError:
        return False
    return True


def _auth_error() -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


def _ctx(request: Request) -> tuple[PipelineConfig, PipelineState, ArtifactStore]:
    return (
        request.app.state.pipeline_config,
        request.app.state.pipeline_state,
        request.app.state.pipeline_artifacts,
    )


def _parse_page(request: Request) -> tuple[int, int] | JSONResponse:
    raw_page = request.query_params.get("page")
    raw_size = request.query_params.get("page_size", request.query_params.get("limit"))
    if raw_page is None and request.query_params.get("offset") is not None:
        try:
            offset = int(request.query_params.get("offset", "0"))
            size = int(raw_size or _PAGE_DEFAULT)
            raw_page = str(offset // size + 1) if size > 0 and offset >= 0 else "0"
        except ValueError:
            raw_page = "0"
    try:
        page = int(raw_page or "1")
        page_size = int(raw_size or str(_PAGE_DEFAULT))
    except ValueError:
        return _json_error("bad_request", "page and page_size must be integers", 400)
    if page <= 0 or page_size <= 0:
        return _json_error("bad_request", "page and page_size must be positive", 400)
    if page_size > _PAGE_MAX:
        return _json_error("bad_request", f"page_size exceeds limit ({_PAGE_MAX})", 400)
    return page, page_size


def _public_worker_status(request: Request) -> dict[str, Any]:
    provider = getattr(request.app.state, "worker_status_provider", None)
    if callable(provider):
        try:
            value = provider()
            if isinstance(value, Mapping):
                status = str(value.get("status") or "offline")
                if status not in {"online", "degraded", "offline"}:
                    status = "offline"
                return {
                    "status": status,
                    "last_heartbeat_at": value.get("last_heartbeat_at"),
                    "age_seconds": value.get("age_seconds"),
                    "active_jobs": value.get("active_jobs", 0),
                }
        except Exception:
            logger.exception("pipeline worker status provider failed")
    state: PipelineState = request.app.state.pipeline_state
    try:
        return state.worker_status_snapshot(
            offline_after_seconds=float(request.app.state.pipeline_config.heartbeat_seconds * 2)
        )
    except Exception:
        logger.exception("pipeline worker heartbeat status unavailable")
        return {"status": "offline", "last_heartbeat_at": None, "age_seconds": None, "active_jobs": 0}


def _display_filename(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].strip()
    if not name:
        return None
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:255] or None


def _public_error(value: object) -> str | None:
    if value is None:
        return None
    message = str(value).strip()
    if not message:
        return None
    # Worker errors are already typed, but adapters may still include an
    # absolute path or credential-shaped text.  Keep useful short messages
    # while refusing those values at the HTTP boundary.
    lowered = message.casefold()
    if len(message) > 500 or "/" in message or "\\" in message or any(
        marker in lowered for marker in ("secret", "token", "password", "api_key")
    ):
        return "step failed"
    return message


def _public_error_type(value: object) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or len(candidate) > 80 or not re.fullmatch(r"[A-Za-z0-9_.-]+", candidate):
        return "step_failed"
    return candidate


def _public_config(config: PipelineConfig, worker_status: dict[str, Any]) -> dict[str, Any]:
    """Return allowlists and limits, excluding all configured paths."""
    return {
        "enabled": bool(config.enabled),
        "models": {
            "ocr": {"allowlist": list(config.ocr.allowlist), "default": config.ocr.default},
            "extract": {
                "allowlist": list(config.extract.allowlist),
                "default": config.extract.default,
            },
            "translate": {
                "allowlist": list(config.translate.allowlist),
                "default": config.translate.default,
            },
        },
        "limits": {
            "pdfs_per_batch": config.pdfs_per_batch,
            "max_pdf_bytes": config.max_pdf_bytes,
            "max_batch_bytes": config.max_batch_bytes,
            "bibtex_max_bytes": config.bibtex_max_bytes,
        },
        "translation_language": config.translation_language,
        "worker": worker_status,
    }


def _job_summary(job: Mapping[str, Any]) -> dict[str, Any]:
    input_info = job.get("input")
    attempts = job.get("attempts") or []
    failed = next(
        (item for item in reversed(attempts) if str(item.get("status")) == "failed"), None
    )
    steps = job.get("steps") or []
    completed = sum(1 for item in steps if str(item.get("status")) == "complete")
    entries = job.get("bibtex_entries")
    entry_key = job.get("bibtex_entry_key")
    if not entries:
        bibtex_status = "not_provided"
    elif entry_key:
        bibtex_status = "matched"
    elif str(job.get("status")) == "needs_attention":
        bibtex_status = "needs_attention"
    else:
        bibtex_status = "unmatched"
    return {
        "id": str(job["id"]),
        "batch_id": job.get("batch_id"),
        "status": str(job.get("status")),
        "revision": int(job.get("revision", 0)),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "terminal_at": job.get("terminal_at"),
        "filename": _display_filename(input_info.get("filename")) if isinstance(input_info, Mapping) else None,
        "size": input_info.get("size") if isinstance(input_info, Mapping) else None,
        "selected_models": dict(job.get("selected_models") or {}),
        "progress": {"completed_steps": completed, "total_steps": len(steps)},
        "failed_step": failed.get("step") if isinstance(failed, Mapping) else None,
        "error": _public_error(failed.get("error")) if isinstance(failed, Mapping) else None,
        "error_type": _public_error_type(failed.get("error_type")) if isinstance(failed, Mapping) else None,
        "retryable": (
            None
            if not isinstance(failed, Mapping) or failed.get("retryable") is None
            else bool(failed.get("retryable"))
        ),
        "bibtex": {"status": bibtex_status, "entry_key": entry_key},
        "preview_digest": job.get("preview_digest"),
        "bundle_digest": job.get("bundle_digest"),
        "cancel_requested": bool(job.get("cancel_requested")),
    }


def _job_dto(job: Mapping[str, Any]) -> dict[str, Any]:
    dto = _job_summary(job)
    steps = []
    for item in job.get("steps") or []:
        steps.append(
            {
                "name": str(item.get("name")),
                "status": str(item.get("status")),
                "attempt": int(item.get("attempt") or 0),
                "model_key": item.get("model_key"),
                "duration_ms": next(
                    (
                        attempt.get("duration_ms")
                        for attempt in reversed(job.get("attempts") or [])
                        if attempt.get("step") == item.get("name")
                    ),
                    None,
                ),
            }
        )
    dto["steps"] = steps
    dto["attempts"] = [
        {
            "step": attempt.get("step"),
            "attempt": attempt.get("attempt"),
            "status": attempt.get("status"),
            "error": _public_error(attempt.get("error")),
            "error_type": _public_error_type(attempt.get("error_type")),
            "retryable": (
                None
                if attempt.get("retryable") is None
                else bool(attempt.get("retryable"))
            ),
            "duration_ms": attempt.get("duration_ms"),
            "started_at": attempt.get("started_at"),
            "finished_at": attempt.get("finished_at"),
        }
        for attempt in job.get("attempts") or []
    ]
    dto["artifacts"] = [
        {"kind": _public_artifact_kind(str(item.get("kind"))), "size": int(item.get("size") or 0), "digest": item.get("digest")}
        for item in job.get("artifacts") or []
        if _public_artifact_kind(str(item.get("kind"))) is not None
    ]
    return dto


def _public_artifact_kind(internal_kind: str) -> str | None:
    for public, (internal, _media_type, _filename) in _ARTIFACTS.items():
        if public == internal_kind:
            return public
        if internal == internal_kind and public in {"pdf", "source_markdown", "summary_json", "translated_markdown"}:
            return public
    return None


def _batch_dto(batch: Mapping[str, Any]) -> dict[str, Any]:
    jobs = [_job_summary(job) for job in batch.get("jobs") or []]
    status_counts: dict[str, int] = {}
    for job in jobs:
        status = str(job["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "id": str(batch["id"]),
        "created_at": batch.get("created_at"),
        "revision": int(batch.get("revision", 0)),
        "job_count": len(jobs),
        "status_counts": status_counts,
        "jobs": jobs,
    }


def _safe_model_selection(config: PipelineConfig, body: Mapping[str, Any]) -> dict[str, str]:
    source = body.get("models")
    if source is None:
        source = {
            name: body.get(key)
            for name, key in (
                ("ocr", "ocr_model"),
                ("extract", "extract_model"),
                ("translate", "translate_model"),
            )
            if body.get(key) is not None
        }
    if not source:
        source = {name: body.get(name) for name in ("ocr", "extract", "translate") if body.get(name) is not None}
    if not isinstance(source, Mapping):
        raise ValueError("models must be an object")
    groups = {"ocr": config.ocr, "extract": config.extract, "translate": config.translate}
    result: dict[str, str] = {}
    for raw_name, raw_value in source.items():
        name = str(raw_name)
        if name not in groups or not isinstance(raw_value, str) or raw_value not in groups[name].allowlist:
            raise ValueError("model selection is outside allowlist")
        result[name] = raw_value
    return result


async def _json_body(request: Request, *, allow_empty: bool = False) -> dict[str, Any] | JSONResponse:
    try:
        raw = await request.body()
        if not raw and allow_empty:
            return {}
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _json_error("bad_request", "invalid JSON body", 400)
    if not isinstance(value, dict):
        return _json_error("bad_request", "JSON body must be an object", 400)
    return {str(key): item for key, item in value.items()}


async def _config(request: Request) -> Response:
    config, _state, _artifacts = _ctx(request)
    return JSONResponse(_public_config(config, _public_worker_status(request)))


async def _create_batch(request: Request) -> Response:
    config, state, artifacts = _ctx(request)
    try:
        async with request.form(
            max_files=config.pdfs_per_batch + 1,
            max_fields=16,
            max_part_size=max(config.max_pdf_bytes, config.bibtex_max_bytes),
        ) as form:
            pdf_values = form.getlist("pdfs[]") + form.getlist("pdfs")
            pdfs = [value for value in pdf_values if isinstance(value, UploadFile)]
            if len(pdfs) != len(pdf_values):
                return _json_error("bad_request", "pdfs must be uploaded files", 400)
            bib_value = form.get("bibtex")
            if bib_value is None:
                bib_value = form.get("bib")
            bibtex = bib_value if isinstance(bib_value, UploadFile) else None
            if bib_value is not None and bibtex is None:
                return _json_error("bad_request", "bibtex must be an uploaded file", 400)
            values: dict[str, Any] = {}
            for name in ("ocr", "extract", "translate"):
                for field in (f"{name}_model", f"{name}_model_key", name):
                    candidate = form.get(field)
                    if isinstance(candidate, str) and candidate.strip():
                        values[name] = candidate.strip()
                        break
            models = _safe_model_selection(config, values)
            if not pdfs:
                return _json_error("bad_request", "at least one PDF is required", 400)
            result = BatchIngestor(config, state, artifacts).ingest(
                [UploadPart(str(file.filename or ""), file.file) for file in pdfs],
                bibtex=(UploadPart(str(bibtex.filename or ""), bibtex.file) if bibtex else None),
                selected_models=models,
            )
    except (ValueError, MultiPartException) as exc:
        return _json_error("bad_request", str(exc), 400)
    except Exception:
        logger.exception("pipeline batch ingestion failed")
        return _json_error("internal_error", "batch ingestion failed", 500)
    try:
        batch = state.get_batch(result.batch_id)
    except Exception:
        logger.exception("pipeline batch was not readable after ingestion")
        return _json_error("internal_error", "batch creation failed", 500)
    return JSONResponse(
        {
            "batch": _batch_dto(batch),
            "batch_id": result.batch_id,
            "job_ids": list(result.jobs),
            "bibtex": {"status": result.bibtex_status},
        },
        status_code=200,
    )


async def _list_batches(request: Request) -> Response:
    page_value = _parse_page(request)
    if isinstance(page_value, JSONResponse):
        return page_value
    page, page_size = page_value
    _config_value, state, _artifacts = _ctx(request)
    try:
        batches, total = state.list_batches_page(offset=(page - 1) * page_size, limit=page_size)
    except Exception:
        logger.exception("pipeline batch listing failed")
        return _json_error("internal_error", "batch listing failed", 500)
    return JSONResponse(
        {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
            "items": [_batch_dto(batch) for batch in batches],
        }
    )


async def _get_batch(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    try:
        batch = state.get_batch(str(request.path_params["batch_id"]))
    except KeyError:
        return _json_error("not_found", "batch not found", 404)
    except Exception:
        logger.exception("pipeline batch detail lookup failed")
        return _json_error("internal_error", "batch lookup failed", 500)
    return JSONResponse({"batch": _batch_dto(batch)})


async def _get_job(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    try:
        job = state.get_job_details(str(request.path_params["job_id"]))
        job["bibtex_entries"] = state.list_bibtex_entries(str(job.get("batch_id"))) if job.get("batch_id") else []
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    except Exception:
        logger.exception("pipeline job detail lookup failed")
        return _json_error("internal_error", "job lookup failed", 500)
    return JSONResponse({"job": _job_dto(job), "worker": _public_worker_status(request)})


async def _retry_job(request: Request) -> Response:
    config, state, _artifacts = _ctx(request)
    body_value = await _json_body(request, allow_empty=True)
    if isinstance(body_value, JSONResponse):
        return body_value
    try:
        models = _safe_model_selection(config, body_value)
        job_id = str(request.path_params["job_id"])
        current = state.get_job(job_id)
        if str(current.get("status")) == "published_with_warning":
            expected_revision = body_value.get("expected_revision")
            if expected_revision is not None and (
                isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            ):
                return _json_error("bad_request", "expected_revision must be an integer", 400)
            if models:
                return _json_error("conflict", "published warning can only retry indexing", 409)
            result = state.retry_indexing(job_id, expected_revision=expected_revision)
        else:
            result = state.retry_job(job_id, selected_models=models or None)
        job = state.get_job_details(job_id)
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    except ValueError as exc:
        return _json_error("conflict", str(exc), 409)
    except Exception:
        logger.exception("pipeline retry failed")
        return _json_error("internal_error", "retry failed", 500)
    return JSONResponse({"job": _job_dto(job), "result": result})


async def _cancel_job(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    try:
        state.request_cancel(str(request.path_params["job_id"]))
        job = state.get_job_details(str(request.path_params["job_id"]))
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    except ValueError as exc:
        return _json_error("conflict", str(exc), 409)
    except Exception:
        logger.exception("pipeline cancellation failed")
        return _json_error("internal_error", "cancellation failed", 500)
    return JSONResponse({"job": _job_dto(job)})


async def _reject_job(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    try:
        state.admin_transition(str(request.path_params["job_id"]), "rejected")
        job = state.get_job_details(str(request.path_params["job_id"]))
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    except ValueError as exc:
        return _json_error("conflict", str(exc), 409)
    except Exception:
        logger.exception("pipeline rejection failed")
        return _json_error("internal_error", "rejection failed", 500)
    return JSONResponse({"job": _job_dto(job)})


async def _publish_job(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    body_value = await _json_body(request)
    if isinstance(body_value, JSONResponse):
        return body_value
    revision = body_value.get("expected_revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        return _json_error("bad_request", "expected_revision must be an integer", 400)
    try:
        result = state.queue_publication(str(request.path_params["job_id"]), revision)
        job = state.get_job_details(str(request.path_params["job_id"]))
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    except ValueError as exc:
        return _json_error("conflict", str(exc), 409)
    except Exception:
        logger.exception("pipeline publication queueing failed")
        return _json_error("internal_error", "publication queueing failed", 500)
    return JSONResponse({"job": _job_dto(job), "result": result})


async def _bibtex_match(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    body_value = await _json_body(request)
    if isinstance(body_value, JSONResponse):
        return body_value
    has_key = "entry_key" in body_value or "bibtex_key" in body_value
    explicit_none = body_value.get("no_bibtex") is True
    if not has_key and not explicit_none:
        return _json_error("bad_request", "entry_key or no_bibtex is required", 400)
    raw_key = body_value.get("entry_key", body_value.get("bibtex_key"))
    if explicit_none:
        raw_key = None
    if raw_key is not None and (not isinstance(raw_key, str) or not raw_key.strip()):
        return _json_error("bad_request", "entry_key must be a non-empty string or null", 400)
    try:
        result = state.bind_job_bibtex(
            str(request.path_params["job_id"]), None if raw_key is None else raw_key.strip()
        )
        callback = getattr(request.app.state, "preview_regenerator", None)
        if callable(callback):
            regenerated = callback(str(request.path_params["job_id"]))
            if inspect.isawaitable(regenerated):
                await regenerated
        job = state.get_job_details(str(request.path_params["job_id"]))
    except KeyError as exc:
        if str(exc).strip("'") == str(request.path_params["job_id"]):
            return _json_error("not_found", "job not found", 404)
        return _json_error("bad_request", "BibTeX entry not found", 400)
    except ValueError as exc:
        return _json_error("conflict", str(exc), 409)
    except Exception:
        logger.exception("pipeline BibTeX binding failed")
        return _json_error("internal_error", "BibTeX binding failed", 500)
    return JSONResponse({"job": _job_dto(job), "binding": result})


async def _publish_ready(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    body_value = await _json_body(request)
    if isinstance(body_value, JSONResponse):
        return body_value
    items = body_value.get("items")
    if items is None:
        items = body_value.get("jobs")
    if not isinstance(items, list):
        return _json_error("bad_request", "items must be an array", 400)
    batch_id = str(request.path_params["batch_id"])
    try:
        batch = state.get_batch(batch_id)
    except KeyError:
        return _json_error("not_found", "batch not found", 404)
    membership = {str(job["id"]) for job in batch.get("jobs") or []}
    outcomes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            outcomes.append({"status": "bad_request", "error": "item must be an object"})
            continue
        job_id = str(item.get("job_id") or "")
        revision = item.get("expected_revision")
        if job_id not in membership:
            outcomes.append({"job_id": job_id, "status": "not_found", "error": "job is not in batch"})
            continue
        if isinstance(revision, bool) or not isinstance(revision, int):
            outcomes.append({"job_id": job_id, "status": "bad_request", "error": "expected_revision must be an integer"})
            continue
        try:
            result = state.queue_publication(job_id, revision)
            outcomes.append({"job_id": job_id, "status": "queued", "result": result})
        except KeyError:
            outcomes.append({"job_id": job_id, "status": "not_found", "error": "job not found"})
        except ValueError as exc:
            outcomes.append({"job_id": job_id, "status": "conflict", "error": str(exc)})
        except Exception:
            logger.exception("pipeline batch publication item failed")
            outcomes.append({"job_id": job_id, "status": "internal_error", "error": "publication queueing failed"})
    return JSONResponse({"batch_id": batch_id, "outcomes": outcomes})


async def _cancel_batch(request: Request) -> Response:
    _config_value, state, _artifacts = _ctx(request)
    batch_id = str(request.path_params["batch_id"])
    try:
        batch = state.get_batch(batch_id)
    except KeyError:
        return _json_error("not_found", "batch not found", 404)
    outcomes: list[dict[str, Any]] = []
    for job in batch.get("jobs") or []:
        job_id = str(job["id"])
        try:
            state.request_cancel(job_id)
            outcomes.append({"job_id": job_id, "status": "cancel_requested"})
        except Exception:
            logger.exception("pipeline batch cancellation item failed")
            outcomes.append({"job_id": job_id, "status": "internal_error", "error": "cancellation failed"})
    return JSONResponse({"batch_id": batch_id, "outcomes": outcomes})


async def _artifact(request: Request) -> Response:
    _config_value, state, artifacts = _ctx(request)
    public_kind = str(request.path_params["kind"])
    selected = _ARTIFACTS.get(public_kind)
    if selected is None:
        return _json_error("not_found", "artifact kind not found", 404)
    internal_kind, media_type, filename = selected
    job_id = str(request.path_params["job_id"])
    try:
        details = state.get_job_details(job_id)
    except KeyError:
        return _json_error("not_found", "job not found", 404)
    metadata = next(
        (item for item in details.get("artifacts") or [] if item.get("kind") == internal_kind), None
    )
    if metadata is None:
        return _json_error("not_found", "artifact not found", 404)
    try:
        path = Path(str(metadata["path"]))
        artifact = Artifact(
            job_id,
            internal_kind,
            path,
            str(metadata["digest"]),
            int(metadata["size"]),
            artifacts.formal_root,
        )
        artifacts.validate_protected_artifact(artifact, job_id, internal_kind)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.digest or len(content) != artifact.size:
            raise ValueError("artifact metadata mismatch")
    except (OSError, ValueError):
        return _json_error("not_found", "artifact not found", 404)

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _with_auth(handler: Callable[[Request], Any], token: str) -> Callable[[Request], Any]:
    async def wrapped(request: Request) -> Response:
        if not _authorized(request, token):
            return _auth_error()
        return await handler(request)

    return wrapped


def create_pipeline_admin_app(
    *,
    config: PipelineConfig,
    state: PipelineState,
    artifacts: ArtifactStore,
    admin_token: str,
    worker_status_provider: Callable[[], Mapping[str, Any]] | None = None,
    preview_regenerator: Callable[[str], Any] | None = None,
) -> Starlette:
    """Create routes mounted beneath ``/api/v1/admin/pipeline``."""
    if not config.enabled:
        app = Starlette(routes=[])
        app.state.pipeline_config = config
        app.state.pipeline_state = state
        app.state.pipeline_artifacts = artifacts
        app.state.worker_status_provider = worker_status_provider
        app.state.preview_regenerator = preview_regenerator
        return app
    routes = [
        Route("/config", _with_auth(_config, admin_token), methods=["GET"]),
        Route("/batches", _with_auth(_create_batch, admin_token), methods=["POST"]),
        Route("/batches", _with_auth(_list_batches, admin_token), methods=["GET"]),
        Route("/batches/{batch_id:str}", _with_auth(_get_batch, admin_token), methods=["GET"]),
        Route("/batches/{batch_id:str}/publish-ready", _with_auth(_publish_ready, admin_token), methods=["POST"]),
        Route("/batches/{batch_id:str}/cancel", _with_auth(_cancel_batch, admin_token), methods=["POST"]),
        Route("/jobs/{job_id:str}", _with_auth(_get_job, admin_token), methods=["GET"]),
        Route("/jobs/{job_id:str}/retry", _with_auth(_retry_job, admin_token), methods=["POST"]),
        Route("/jobs/{job_id:str}/cancel", _with_auth(_cancel_job, admin_token), methods=["POST"]),
        Route("/jobs/{job_id:str}/reject", _with_auth(_reject_job, admin_token), methods=["POST"]),
        Route("/jobs/{job_id:str}/publish", _with_auth(_publish_job, admin_token), methods=["POST"]),
        Route("/jobs/{job_id:str}/bibtex-match", _with_auth(_bibtex_match, admin_token), methods=["PUT"]),
        Route("/jobs/{job_id:str}/artifacts/{kind:str}", _with_auth(_artifact, admin_token), methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.pipeline_config = config
    app.state.pipeline_state = state
    app.state.pipeline_artifacts = artifacts
    app.state.worker_status_provider = worker_status_provider
    app.state.preview_regenerator = preview_regenerator
    return app


create_admin_pipeline_app = create_pipeline_admin_app


__all__ = ["create_pipeline_admin_app", "create_admin_pipeline_app"]
