"""Schema loading and validation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import importlib.resources as resources

from jsonschema import Draft7Validator


class SchemaError(ValueError):
    pass


def load_schema(schema_path: str | None) -> dict[str, Any]:
    if schema_path:
        path = Path(schema_path)
        if not path.exists():
            raise SchemaError(f"Schema file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    with resources.files("deepresearch_flow.paper.schemas").joinpath(
        "default_paper_schema.json"
    ).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_schema(schema: dict[str, Any]) -> Draft7Validator:
    if schema.get("type") != "object":
        raise SchemaError("Schema must describe a JSON object at the top level")

    required = schema.get("required", [])
    if "paper_title" not in required:
        raise SchemaError("Schema must require 'paper_title'")
    if "paper_authors" not in required:
        raise SchemaError("Schema must require 'paper_authors'")

    properties = schema.get("properties", {})
    authors_schema = properties.get("paper_authors", {})
    if authors_schema.get("type") != "array":
        raise SchemaError("'paper_authors' must be an array in the schema")

    items = authors_schema.get("items", {})
    if items.get("type") != "string":
        raise SchemaError("'paper_authors' items must be strings")

    schema_for_validation = dict(schema)
    if schema_for_validation.get("additionalProperties", True) is False:
        schema_for_validation = dict(schema_for_validation)
        schema_for_validation["additionalProperties"] = True

    return Draft7Validator(schema_for_validation)


def schema_to_prompt(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2)
