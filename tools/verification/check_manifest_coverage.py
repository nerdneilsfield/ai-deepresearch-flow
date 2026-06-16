#!/usr/bin/env python3
"""Check verification manifest coverage against generated inventory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

FORMAL_REQUIRED = {"MODEL_PROOF", "IMPLEMENTATION_REFINEMENT_CHECK"}
NON_EXECUTABLE_COVERAGE = {"INVENTORY_MAPPING"}


def _load_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json" or yaml is None:
        return json.loads(text)
    data = yaml.safe_load(text)
    return data or {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coverage_entries(manifest_item: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in _list(manifest_item.get("coverage")):
        if isinstance(entry, dict):
            entries.append(entry)
    for symbol in _list(manifest_item.get("symbols")):
        if not isinstance(symbol, dict):
            continue
        for entry in _list(symbol.get("coverage")):
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _manifest_symbol_ids(manifest_item: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for symbol in _list(manifest_item.get("symbols")):
        if isinstance(symbol, dict) and symbol.get("stable_id"):
            ids.add(str(symbol["stable_id"]))
    return ids


def _manifest_symbol_by_id(manifest_item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for symbol in _list(manifest_item.get("symbols")):
        if isinstance(symbol, dict) and symbol.get("stable_id"):
            out[str(symbol["stable_id"])] = symbol
    return out


def _add_duplicate_errors(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    duplicated: set[str] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    for value in sorted(duplicated):
        errors.append(f"duplicate {label}: {value}")


def _is_collectable_test_path(path: str) -> bool:
    name = Path(path).name
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith(
        (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")
    )


def check_manifest(repo: Path, inventory: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    manifest_items = [item for item in _list(manifest.get("items")) if isinstance(item, dict)]
    manifest_by_path = {str(item.get("path")): item for item in manifest_items if item.get("path")}
    _add_duplicate_errors(
        [str(item.get("path")) for item in manifest_items if item.get("path")],
        "manifest path",
        errors,
    )

    manifest_config_ids = {
        str(item.get("config_id"))
        for item in _list(manifest.get("config_items"))
        if isinstance(item, dict) and item.get("config_id")
    }
    _add_duplicate_errors(
        [
            str(item.get("config_id"))
            for item in _list(manifest.get("config_items"))
            if isinstance(item, dict) and item.get("config_id")
        ],
        "manifest config item",
        errors,
    )

    manifest_evidence_ids = {
        str(item.get("evidence_id"))
        for item in _list(manifest.get("evidence_assets"))
        if isinstance(item, dict) and item.get("evidence_id")
    }
    manifest_evidence_paths = {
        str(item.get("path"))
        for item in _list(manifest.get("evidence_assets"))
        if isinstance(item, dict) and item.get("path")
    }
    _add_duplicate_errors(list(manifest_evidence_ids), "manifest evidence asset", errors)

    manifest_artifact_groups = {
        str(item.get("artifact_group_id")): item
        for item in _list(manifest.get("artifact_groups"))
        if isinstance(item, dict) and item.get("artifact_group_id")
    }
    _add_duplicate_errors(
        [
            str(item.get("artifact_group_id"))
            for item in _list(manifest.get("artifact_groups"))
            if isinstance(item, dict) and item.get("artifact_group_id")
        ],
        "manifest artifact group",
        errors,
    )

    inventory_paths = {
        str(item.get("path"))
        for item in _list(inventory.get("items"))
        if isinstance(item, dict) and item.get("path")
    }
    for path in sorted(set(manifest_by_path) - inventory_paths):
        errors.append(f"stale manifest path: {path}")

    for item in _list(inventory.get("items")):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", ""))
        classification = str(item.get("classification", ""))
        group_id = item.get("artifact_group_id")
        if group_id:
            if str(group_id) not in manifest_artifact_groups:
                errors.append(f"uncovered artifact group: {group_id}")
            if not item.get("generated_reason"):
                errors.append(f"generated item missing reason: {path}")
            continue
        if classification == "test":
            if path not in manifest_evidence_paths:
                errors.append(f"uncovered evidence asset: {path}")
            continue
        manifest_item = manifest_by_path.get(path)
        if manifest_item is None:
            errors.append(f"uncovered path: {path}")
            for symbol in _list(item.get("symbols")):
                if isinstance(symbol, dict) and symbol.get("stable_id"):
                    errors.append(f"uncovered symbol: {symbol['stable_id']}")
            continue
        symbol_ids = _manifest_symbol_ids(manifest_item)
        symbol_by_id = _manifest_symbol_by_id(manifest_item)
        _add_duplicate_errors(
            [
                str(symbol.get("stable_id"))
                for symbol in _list(manifest_item.get("symbols"))
                if isinstance(symbol, dict) and symbol.get("stable_id")
            ],
            "manifest symbol",
            errors,
        )
        for symbol in _list(item.get("symbols")):
            if not isinstance(symbol, dict) or not symbol.get("stable_id"):
                continue
            stable_id = str(symbol["stable_id"])
            if stable_id not in symbol_ids:
                errors.append(f"uncovered symbol: {stable_id}")
                continue
            manifest_symbol = symbol_by_id[stable_id]
            for cov in _list(manifest_symbol.get("coverage")):
                if isinstance(cov, dict) and cov.get("kind") in {
                    "PROPERTY_TEST",
                    "FUZZ_TEST",
                    "FAULT_INJECTION",
                }:
                    if not (
                        cov.get("observable_contract") or manifest_symbol.get("observable_contract")
                    ):
                        errors.append(f"coverage missing observable_contract: {stable_id}")
        criticality = str(manifest_item.get("criticality") or item.get("criticality") or "")
        if manifest_item.get("temporary_gap"):
            errors.append(f"temporary gap remains: {path}")
        kinds = {
            str(entry.get("kind"))
            for entry in _coverage_entries(manifest_item)
            if entry.get("kind")
        }
        if criticality == "P0" and not (kinds - NON_EXECUTABLE_COVERAGE):
            errors.append(f"P0 missing executable coverage: {path}")
        if manifest_item.get("requires_formal_model"):
            missing = FORMAL_REQUIRED - kinds
            for kind in sorted(missing):
                errors.append(f"formal target missing {kind}: {path}")
        if _dict(item.get("flags")).get("suspected_boundary_unclassified"):
            errors.append(f"suspected boundary unclassified: {path}")

    for config_item in _list(inventory.get("config_items")):
        if not isinstance(config_item, dict) or not config_item.get("config_id"):
            continue
        config_id = str(config_item["config_id"])
        if config_id not in manifest_config_ids:
            errors.append(f"uncovered config item: {config_id}")

    for evidence in _list(inventory.get("evidence_assets")):
        if not isinstance(evidence, dict) or not evidence.get("evidence_id"):
            continue
        evidence_id = str(evidence["evidence_id"])
        path = str(evidence.get("path", ""))
        if evidence_id not in manifest_evidence_ids and path not in manifest_evidence_paths:
            errors.append(f"uncovered evidence asset: {path or evidence_id}")
        if path and not _is_collectable_test_path(path):
            errors.append(f"evidence path is not a collectable test file: {path}")

    referenced_paths: list[str] = []
    for item in manifest_items:
        for cov in _coverage_entries(item):
            if cov.get("path"):
                referenced_paths.append(str(cov["path"]))
    for evidence in _list(manifest.get("evidence_assets")):
        if isinstance(evidence, dict) and evidence.get("path"):
            referenced_paths.append(str(evidence["path"]))
            if not _is_collectable_test_path(str(evidence["path"])):
                errors.append(
                    f"manifest evidence path is not a collectable test file: {evidence['path']}"
                )
    for path in sorted(set(referenced_paths)):
        if path and not (repo / path).exists():
            errors.append(f"missing evidence path: {path}")

    for group_id, group in manifest_artifact_groups.items():
        missing = [
            field
            for field in (
                "path_prefix",
                "upstream_name",
                "file_count",
                "checksum_manifest",
                "validation_command",
                "justification",
            )
            if not group.get(field)
        ]
        for field in missing:
            errors.append(f"artifact group missing {field}: {group_id}")

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    inventory = _load_data(args.inventory)
    manifest = _load_data(args.manifest)
    errors = check_manifest(args.repo.resolve(), inventory, manifest)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("coverage ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
