"""OCR configuration loading from ocr.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PADDLE_OCR_VL_MODEL = "PaddleOCR-VL-1.6"


@dataclass(frozen=True)
class GeneralConfig:
    output_dir: str = "ocr_output"


@dataclass(frozen=True)
class BackendConfig:
    type: str
    api_url: str
    token: str
    options: dict[str, object] = field(default_factory=dict)
    model: str = PADDLE_OCR_VL_MODEL
    poll_interval_seconds: float = 5.0
    job_timeout_seconds: float = 1800.0


@dataclass(frozen=True)
class OcrConfig:
    general: GeneralConfig
    backend: BackendConfig


def _resolve_env(value: str) -> str:
    """Resolve ``env:VAR_NAME`` to the environment variable value."""
    if not value.startswith("env:"):
        return value
    env_name = value.split(":", 1)[1]
    resolved = os.environ.get(env_name)
    if not resolved:
        raise ValueError(
            f"Environment variable '{env_name}' is not set "
            f"(referenced as 'env:{env_name}' in ocr.toml)"
        )
    return resolved


def _positive_seconds(value: object, field_name: str) -> float:
    """Return a positive numeric duration from TOML configuration."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"'{field_name}' must be a positive number in [backend]")
    return float(value)


def load_ocr_config(path: Path) -> OcrConfig:
    """Load and validate OCR configuration from a TOML file."""
    if not path.exists():
        raise FileNotFoundError(f"OCR config file not found: {path}")

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # General section (optional, has defaults).
    general_raw = raw.get("general", {})
    general = GeneralConfig(
        output_dir=general_raw.get("output_dir", "ocr_output"),
    )

    # Backend section (required).
    backend_raw = raw.get("backend")
    if not backend_raw:
        raise ValueError("'[backend]' section is required in ocr.toml")

    backend_type = backend_raw.get("type")
    if not backend_type:
        raise ValueError("'type' is required in [backend] section of ocr.toml")

    api_url = backend_raw.get("api_url", "")
    if not api_url:
        raise ValueError("'api_url' is required in [backend] section of ocr.toml")

    token_raw = backend_raw.get("token", "")
    if not token_raw:
        raise ValueError("'token' is required in [backend] section of ocr.toml")
    token = _resolve_env(token_raw)

    options = backend_raw.get("options", {})
    model = backend_raw.get("model", PADDLE_OCR_VL_MODEL)
    if not isinstance(model, str) or model != PADDLE_OCR_VL_MODEL:
        raise ValueError(
            f"'model' must be '{PADDLE_OCR_VL_MODEL}' for the paddle backend"
        )
    poll_interval_seconds = _positive_seconds(
        backend_raw.get("poll_interval_seconds", 5.0), "poll_interval_seconds"
    )
    job_timeout_seconds = _positive_seconds(
        backend_raw.get("job_timeout_seconds", 1800.0), "job_timeout_seconds"
    )

    backend = BackendConfig(
        type=backend_type,
        api_url=api_url,
        token=token,
        options=options,
        model=model,
        poll_interval_seconds=poll_interval_seconds,
        job_timeout_seconds=job_timeout_seconds,
    )

    return OcrConfig(general=general, backend=backend)
