#!/usr/bin/env python3
"""Generate a bootstrap verification manifest from an inventory.

The bootstrap manifest is intentionally conservative: it proves that every
tracked surface is inventoried and assigns explicit temporary gaps for P0
surfaces that still need deeper model/conformance coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required to generate the bootstrap manifest") from exc

BOOTSTRAP_GAP = {
    "reason": "repo-wide bootstrap manifest; formal/conformance target not assigned yet",
    "risk": "P0 item is inventoried but not fully formally covered",
    "follow_up": "assign MODEL_PROOF and IMPLEMENTATION_REFINEMENT_CHECK in formal/fuzz rollout",
    "expires": "2026-07-15",
}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _release_blocking(criticality: str) -> bool:
    return criticality in {"P0", "P1"}


def build_manifest(inventory: dict[str, Any], *, generated_from: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw_item in _list(inventory.get("items")):
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("artifact_group_id") or raw_item.get("classification") == "test":
            continue
        criticality = str(raw_item.get("criticality") or "P2")
        entry: dict[str, Any] = {
            "path": raw_item.get("path"),
            "classification": raw_item.get("classification"),
            "kind": raw_item.get("kind"),
            "criticality": criticality,
            "release_blocking": _release_blocking(criticality),
            "formal_status": "none",
            "evidence_status": ["inventory"],
            "flags": raw_item.get("flags") or {},
            "symbols": [],
        }
        for symbol in _list(raw_item.get("symbols")):
            if not isinstance(symbol, dict) or not symbol.get("stable_id"):
                continue
            entry["symbols"].append(
                {
                    "stable_id": symbol["stable_id"],
                    "observable_contract": (
                        "inventory-mapped; behavior evidence to be assigned by targeted "
                        "manifest refinement"
                    ),
                    "coverage": [
                        {
                            "kind": "INVENTORY_MAPPING",
                            "path": "docs/verification/repo-verification-inventory.json",
                        }
                    ],
                }
            )
        if criticality == "P0":
            entry["temporary_gap"] = dict(BOOTSTRAP_GAP)
        items.append(entry)

    config_items = [
        {
            "config_id": item.get("config_id"),
            "validation": {
                "command": "uv run python tools/verification/check_manifest_coverage.py --help"
            },
        }
        for item in _list(inventory.get("config_items"))
        if isinstance(item, dict) and item.get("config_id")
    ]

    evidence_assets = [
        dict(item)
        for item in _list(inventory.get("evidence_assets"))
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    artifact_groups = [
        dict(item)
        for item in _list(inventory.get("artifact_groups"))
        if isinstance(item, dict) and item.get("artifact_group_id")
    ]

    return {
        "version": 1,
        "generated_from": generated_from,
        "items": items,
        "config_items": config_items,
        "evidence_assets": evidence_assets,
        "artifact_groups": artifact_groups,
    }


def _render(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    generated_from = args.inventory.as_posix()
    payload = build_manifest(inventory, generated_from=generated_from)
    rendered = _render(payload)

    if args.check:
        if args.output is None:
            print("--check requires --output", file=sys.stderr)
            return 2
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError:
            print("bootstrap manifest is stale: output file is missing", file=sys.stderr)
            return 1
        if current != rendered:
            print("bootstrap manifest is stale", file=sys.stderr)
            return 1
        return 0

    if args.stdout or args.output is None:
        print(rendered, end="")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
