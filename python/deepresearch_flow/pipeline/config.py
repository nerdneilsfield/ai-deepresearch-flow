"""Configuration for the optional administrative pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _positive(value: Any, name: str, default: int) -> int:
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"pipeline {name} must be positive")
    return int(value)


def _path(value: Any, default: str) -> str:
    return os.path.expanduser(str(default if value is None else value))


def _model_group(raw: dict[str, Any], name: str) -> tuple[tuple[str, ...], str | None]:
    group = raw.get(name, {})
    if isinstance(group, dict):
        allowed = group.get("allowlist", group.get("allowed", []))
        default = group.get("default", group.get("default_model"))
    else:
        allowed, default = group, None
    if isinstance(allowed, str):
        allowed = [allowed]
    if allowed is None:
        allowed = []
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise ValueError(f"pipeline {name} allowlist must be a list of strings")
    if default is not None and not isinstance(default, str):
        raise ValueError(f"pipeline {name} default must be a string")
    if default is not None and allowed and default not in allowed:
        raise ValueError(f"pipeline {name} default model is not in allowlist")
    return tuple(allowed), default


@dataclass(frozen=True)
class ModelAllowlist:
    allowlist: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class PipelineConfig:
    enabled: bool = False
    pdfs_per_batch: int = 20
    max_pdf_bytes: int = 100 * 1024 * 1024
    max_batch_bytes: int = 500 * 1024 * 1024
    bibtex_max_bytes: int = 1024 * 1024
    max_concurrent_jobs: int = 2
    retention_days: int = 7
    work_dir: str = "pipeline-work"
    queue_db: str = "pipeline-work/queue.sqlite3"
    snapshot_root: str = "pipeline-snapshots"
    static_root: str = "pipeline-static"
    webdav_url: str | None = None
    ocr: ModelAllowlist = field(default_factory=ModelAllowlist)
    extract: ModelAllowlist = field(default_factory=ModelAllowlist)
    translate: ModelAllowlist = field(default_factory=ModelAllowlist)
    extract_templates: tuple[str, ...] = ()
    translation_language: str = "en"
    lease_seconds: int = 300
    heartbeat_seconds: int = 30

    @property
    def lease_duration_seconds(self) -> int:
        return self.lease_seconds

    @property
    def heartbeat_interval_seconds(self) -> int:
        return self.heartbeat_seconds

    def public_snapshot(self) -> dict[str, Any]:
        """Return stable non-secret configuration suitable for an admin API."""
        result = asdict(self)
        # Explicit allowlist keeps this safe if future fields contain credentials.
        if self.webdav_url:
            parsed = urlsplit(self.webdav_url)
            # Keep endpoint setting while removing optional userinfo.
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            result["webdav_url"] = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
        return result

    def fingerprint(self) -> str:
        payload = json.dumps(self.public_snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    """Parse ``[pipeline]`` from existing service TOML configuration."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as stream:
        document = tomllib.load(stream)
    raw = document.get("pipeline", {})
    if not isinstance(raw, dict):
        raise ValueError("[pipeline] must be a table")
    storage = raw.get("storage", {})
    if not isinstance(storage, dict):
        raise ValueError("pipeline storage must be a table")
    raw = {**raw, **storage}
    models = raw.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("pipeline models must be a table")
    # Accept concise top-level aliases used by service deployments.
    for name in ("ocr", "extract", "translate"):
        if name not in models and isinstance(raw.get(name), dict):
            models[name] = raw[name]
        if name not in models and f"{name}_allowlist" in raw:
            models[name] = {
                "allowlist": raw.get(f"{name}_allowlist"),
                "default": raw.get(f"{name}_default_model", raw.get(f"{name}_default")),
            }
    ocr_allowed, ocr_default = _model_group(models, "ocr")
    extract_allowed, extract_default = _model_group(models, "extract")
    translate_allowed, translate_default = _model_group(models, "translate")
    # A selected model can also be specified independently of nested defaults.
    selected = raw.get("selected_models", {})
    if isinstance(selected, dict):
        for name, default in (("ocr", ocr_default), ("extract", extract_default), ("translate", translate_default)):
            value = selected.get(name)
            if value is None and isinstance(models.get(name), dict):
                value = models[name].get("selected")
            if value is not None:
                allowed = models.get(name, {})
                allowed = allowed.get("allowlist", allowed.get("allowed", [])) if isinstance(allowed, dict) else allowed
                if not isinstance(value, str) or (allowed and value not in allowed):
                    raise ValueError(f"pipeline {name} selected model is not in allowlist")
                if default is None:
                    if name == "ocr": ocr_default = value
                    elif name == "extract": extract_default = value
                    else: translate_default = value
    templates = raw.get("extract_templates", raw.get("fixed_extract_templates", []))
    if isinstance(templates, str):
        templates = [templates]
    if not isinstance(templates, list) or any(not isinstance(item, str) for item in templates):
        raise ValueError("pipeline extract_templates must be a list of strings")
    return PipelineConfig(
        enabled=bool(raw.get("enabled", False)),
        pdfs_per_batch=_positive(raw.get("pdfs_per_batch"), "pdfs_per_batch", 20),
        max_pdf_bytes=_positive(raw.get("max_pdf_bytes"), "max_pdf_bytes", 100 * 1024 * 1024),
        max_batch_bytes=_positive(raw.get("max_batch_bytes"), "max_batch_bytes", 500 * 1024 * 1024),
        bibtex_max_bytes=_positive(raw.get("bibtex_max_bytes"), "bibtex_max_bytes", 1024 * 1024),
        max_concurrent_jobs=_positive(raw.get("max_concurrent_jobs"), "max_concurrent_jobs", 2),
        retention_days=_positive(raw.get("retention_days"), "retention_days", 7),
        work_dir=_path(raw.get("work_dir"), "pipeline-work"),
        queue_db=_path(raw.get("queue_db"), "pipeline-work/queue.sqlite3"),
        snapshot_root=_path(raw.get("snapshot_root", raw.get("snapshot_dir")), "pipeline-snapshots"),
        static_root=_path(raw.get("static_root"), "pipeline-static"),
        webdav_url=raw.get("webdav_url"),
        ocr=ModelAllowlist(ocr_allowed, ocr_default),
        extract=ModelAllowlist(extract_allowed, extract_default),
        translate=ModelAllowlist(translate_allowed, translate_default),
        extract_templates=tuple(templates),
        translation_language=str(raw.get("translation_language", "en")),
        lease_seconds=_positive(raw.get("lease_seconds", raw.get("lease_duration_seconds")), "lease_seconds", 300),
        heartbeat_seconds=_positive(raw.get("heartbeat_seconds", raw.get("heartbeat_interval_seconds")), "heartbeat_seconds", 30),
    )
