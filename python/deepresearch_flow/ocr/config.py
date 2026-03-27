"""OCR configuration loading from ocr.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GeneralConfig:
    output_dir: str = "ocr_output"


@dataclass(frozen=True)
class BackendConfig:
    type: str
    api_url: str
    token: str
    options: dict[str, object] = field(default_factory=dict)


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

    backend = BackendConfig(
        type=backend_type,
        api_url=api_url,
        token=token,
        options=options,
    )

    return OcrConfig(general=general, backend=backend)
