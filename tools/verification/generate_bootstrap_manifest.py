#!/usr/bin/env python3
"""Generate a bootstrap verification manifest from an inventory.

The bootstrap manifest is intentionally conservative: it proves that every
tracked surface is inventoried and assigns at least one executable evidence
class for P0 surfaces. Formal model targets are explicit and are not replaced
by temporary gap records.
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

FORMAL_TARGETS = {
    "python/deepresearch_flow/paper/snapshot/auth.py": {
        "status": "finite_state_model",
        "coverage": [
            {
                "kind": "MODEL_PROOF",
                "path": "tools/formal/tlc/check_all_tlc_models.py",
                "checker_command": "make verify-formal-tlc",
                "observable_contract": "OAuth client/cache/resource state machine exhausts the declared finite TLC state space.",
            },
            {
                "kind": "MODEL_PROOF",
                "path": "tools/formal/smt/check_all_smt_models.py",
                "checker_command": "make verify-formal-smt",
                "observable_contract": "OAuth client/cache/resource state machine satisfies the declared finite Z3 invariants.",
            },
            {
                "kind": "IMPLEMENTATION_REFINEMENT_CHECK",
                "path": "python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py",
                "checker_command": "uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py -q",
                "observable_contract": "OAuth client cache registration, reopen, corruption, and write-failure behavior is fail-closed at the public cache API.",
            },
            {
                "kind": "IMPLEMENTATION_REFINEMENT_CHECK",
                "path": "python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py",
                "checker_command": "uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py -q",
                "observable_contract": "MCP OAuth HTTP endpoints recover syntactically valid missing dynamic clients for reauth, reject malformed clients, and never issue tokens before the full auth chain.",
            },
        ],
    },
    "formal/oauth_client_cache/OAuthClientCache.tla": {
        "status": "tla_model",
        "coverage": [
            {
                "kind": "MODEL_PROOF",
                "path": "tools/formal/tlc/check_all_tlc_models.py",
                "checker_command": "make verify-formal-tlc",
                "observable_contract": "Checked TLA+ OAuth cache model exhausts its reachable finite state space.",
            },
            {
                "kind": "IMPLEMENTATION_REFINEMENT_CHECK",
                "path": "tests/verification/test_tlc_formal_gate.py",
                "checker_command": "DRFLOW_RUN_LOCAL_FORMAL=1 uv run pytest tests/verification/test_tlc_formal_gate.py -q",
                "observable_contract": "The local TLC gate fails if a checked model leaves states unexplored or violates invariants.",
            },
        ],
    },
    "formal/oauth_client_cache/OAuthClientCache.cfg": {
        "status": "tla_model",
        "coverage": [
            {
                "kind": "MODEL_PROOF",
                "path": "tools/formal/tlc/check_all_tlc_models.py",
                "checker_command": "make verify-formal-tlc",
                "observable_contract": "Checked TLA+ OAuth cache configuration is consumed by the local TLC gate.",
            },
            {
                "kind": "IMPLEMENTATION_REFINEMENT_CHECK",
                "path": "tests/verification/test_tlc_formal_gate.py",
                "checker_command": "DRFLOW_RUN_LOCAL_FORMAL=1 uv run pytest tests/verification/test_tlc_formal_gate.py -q",
                "observable_contract": "The local TLC gate reports exhausted queues and invariant status for configured models.",
            },
        ],
    },
}

PYTHON_SOURCE_EVIDENCE = [
    {
        "kind": "STATIC_CHECK",
        "path": "pyproject.toml",
        "checker_command": "uv run ruff check python tests tools",
        "observable_contract": "Python source is accepted by the repository static checker.",
    },
    {
        "kind": "TYPE_CHECK",
        "path": "pyproject.toml",
        "checker_command": "uv run ty check python tests tools",
        "observable_contract": "Python source is accepted by the repository type checker.",
    },
]

FRONTEND_SOURCE_EVIDENCE = [
    {
        "kind": "FRONTEND_TEST",
        "path": "frontend/package.json",
        "checker_command": "cd frontend && npm test -- --run",
        "observable_contract": "Frontend source is accepted by black-box component/unit tests.",
    },
    {
        "kind": "BUILD_CHECK",
        "path": "frontend/package.json",
        "checker_command": "cd frontend && npm run build",
        "observable_contract": "Frontend source compiles into production assets.",
    },
]

DOC_EVIDENCE = [
    {
        "kind": "DOC_SECRET_SCAN",
        "path": "tools/verification/check_doc_secrets.py",
        "checker_command": "uv run python tools/verification/check_doc_secrets.py",
        "observable_contract": "Documentation and example deploy surfaces do not expose configured secret/private-host patterns.",
    }
]

SUPPLY_CHAIN_EVIDENCE = [
    {
        "kind": "SUPPLY_CHAIN_CHECK",
        "path": "tools/verification/check_supply_chain.py",
        "checker_command": "uv run python tools/verification/check_supply_chain.py",
        "observable_contract": "Local lockfile, package manifest, and deploy supply-chain checks have no failing findings.",
    }
]

DEPLOY_EVIDENCE_BY_KIND = {
    "container_build": [
        {
            "kind": "DEPLOY_BLACK_BOX_TEST",
            "path": "python/deepresearch_flow/paper/tests/test_docker_deploy_image.py",
            "checker_command": "uv run pytest python/deepresearch_flow/paper/tests/test_docker_deploy_image.py -q",
            "observable_contract": "Docker build surfaces expose the expected runtime startup contract.",
        }
    ],
    "compose_config": [
        {
            "kind": "DEPLOY_BLACK_BOX_TEST",
            "path": "python/deepresearch_flow/paper/tests/test_docker_compose_example.py",
            "checker_command": "uv run pytest python/deepresearch_flow/paper/tests/test_docker_compose_example.py -q",
            "observable_contract": "Example compose configuration parses and exposes required services/volumes through observable config output.",
        }
    ],
    "reverse_proxy_config": [
        {
            "kind": "DEPLOY_BLACK_BOX_TEST",
            "path": "python/deepresearch_flow/paper/tests/test_docker_nginx_config.py",
            "checker_command": "uv run pytest python/deepresearch_flow/paper/tests/test_docker_nginx_config.py -q",
            "observable_contract": "Nginx templates expose required MCP/OAuth/API routing and fallback behavior.",
        }
    ],
    "startup_script": [
        {
            "kind": "DEPLOY_BLACK_BOX_TEST",
            "path": "python/deepresearch_flow/paper/tests/test_docker_start_api_script.py",
            "checker_command": "uv run pytest python/deepresearch_flow/paper/tests/test_docker_start_api_script.py -q",
            "observable_contract": "Startup scripts expose fail-closed and configured startup behavior through their public shell interface.",
        }
    ],
}

PATH_EVIDENCE = {
    "frontend/src/components/RenderedMarkdown.vue": [
        {
            "kind": "RENDERER_CONTENT_PRESERVATION_TEST",
            "path": "frontend/src/__tests__/RenderedMarkdown.test.ts",
            "checker_command": "cd frontend && npm test -- --run src/__tests__/RenderedMarkdown.test.ts",
            "observable_contract": "Markdown formulas and Mermaid diagrams remain rendered or visible with detailed diagnostics through the public component DOM.",
        },
        {
            "kind": "RENDERER_FAULT_DIAGNOSTIC_TEST",
            "path": "frontend/src/__tests__/RenderedMarkdownDiagnostics.test.ts",
            "checker_command": "cd frontend && npm test -- --run src/__tests__/RenderedMarkdownDiagnostics.test.ts",
            "observable_contract": "Renderer dependency faults expose visible diagnostics with source excerpts rather than silently dropping content.",
        },
    ],
    "frontend/src/lib/markdown-normalize.ts": [
        {
            "kind": "RENDERER_CONTENT_PRESERVATION_TEST",
            "path": "frontend/src/__tests__/RenderedMarkdown.test.ts",
            "checker_command": "cd frontend && npm test -- --run src/__tests__/RenderedMarkdown.test.ts",
            "observable_contract": "API-style formula tags are normalized into content that remains rendered or visible in the public markdown preview DOM.",
        }
    ],
    "python/deepresearch_flow/paper/snapshot/advanced/auth.py": [
        {
            "kind": "UNIT_BLACK_BOX",
            "path": "python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py",
            "checker_command": "uv run pytest python/deepresearch_flow/paper/snapshot/advanced/tests/test_auth.py -q",
            "observable_contract": "Advanced-search bearer auth accepts and rejects tokens through public verify behavior.",
        }
    ],
    "frontend/src/lib/token-db.ts": [
        {
            "kind": "UNIT_BLACK_BOX",
            "path": "frontend/src/__tests__/tokenDb.test.ts",
            "checker_command": "cd frontend && npm test -- --run src/__tests__/tokenDb.test.ts",
            "observable_contract": "Token storage public API persists, loads, and clears tokens through observable IndexedDB behavior.",
        }
    ],
    "frontend/src/composables/useAdvancedSearchToken.ts": [
        {
            "kind": "UNIT_BLACK_BOX",
            "path": "frontend/src/__tests__/useAdvancedSearchToken.test.ts",
            "checker_command": "cd frontend && npm test -- --run src/__tests__/useAdvancedSearchToken.test.ts",
            "observable_contract": "Advanced-search token composable exposes observable load/set/clear/error state behavior.",
        }
    ],
    "tools/formal/check_oauth_client_cache_model.py": [
        {
            "kind": "MODEL_GATE_TEST",
            "path": "tests/verification/test_smt_formal_gate.py",
            "checker_command": "DRFLOW_RUN_LOCAL_FORMAL=1 uv run pytest tests/verification/test_smt_formal_gate.py -q",
            "observable_contract": "SMT gate reports PASS only when the finite-universe model checker succeeds.",
        }
    ],
    "tools/verification/check_doc_secrets.py": [
        {
            "kind": "DOC_SECRET_SCAN",
            "path": "tools/verification/check_doc_secrets.py",
            "checker_command": "uv run python tools/verification/check_doc_secrets.py",
            "observable_contract": "Secret scanner exits nonzero on configured secret/private-host findings and redacts output.",
        }
    ],
}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _release_blocking(criticality: str) -> bool:
    return criticality in {"P0", "P1"}


def _copy_coverage(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(entry) for entry in entries]


def _coverage_for_item(
    raw_item: dict[str, Any],
) -> tuple[str, list[str], list[dict[str, Any]], bool]:
    path = str(raw_item.get("path") or "")
    kind = str(raw_item.get("kind") or "")
    classification = str(raw_item.get("classification") or "")
    formal_target = FORMAL_TARGETS.get(path)
    coverage: list[dict[str, Any]] = [
        {
            "kind": "INVENTORY_MAPPING",
            "path": "docs/verification/repo-verification-inventory.json",
            "observable_contract": "Tracked repository surface is present in the generated verification inventory.",
        }
    ]
    status = "none"
    evidence_status = ["inventory"]
    requires_formal_model = False

    if formal_target is not None:
        status = str(formal_target["status"])
        evidence_status.extend(["model", "conformance"])
        coverage.extend(_copy_coverage(formal_target["coverage"]))
        requires_formal_model = True

    if classification == "source" and kind == "python_module":
        coverage.extend(_copy_coverage(PYTHON_SOURCE_EVIDENCE))
        evidence_status.extend(["static", "typecheck"])
    elif classification == "source" and kind in {"typescript_module", "vue_component"}:
        coverage.extend(_copy_coverage(FRONTEND_SOURCE_EVIDENCE))
        evidence_status.extend(["frontend_test", "build"])
    elif classification == "doc":
        coverage.extend(_copy_coverage(DOC_EVIDENCE))
        evidence_status.append("doc_secret_scan")
    elif classification in {"workflow", "build", "script", "config"}:
        coverage.extend(_copy_coverage(SUPPLY_CHAIN_EVIDENCE))
        evidence_status.append("supply_chain")
    elif classification == "asset" and path.startswith("scripts/docker/"):
        coverage.extend(_copy_coverage(SUPPLY_CHAIN_EVIDENCE))
        evidence_status.append("supply_chain")

    deploy_entries = DEPLOY_EVIDENCE_BY_KIND.get(kind)
    if deploy_entries:
        coverage.extend(_copy_coverage(deploy_entries))
        evidence_status.append("deploy_test")

    path_entries = PATH_EVIDENCE.get(path)
    if path_entries:
        coverage.extend(_copy_coverage(path_entries))
        evidence_status.append("targeted_test")

    return status, sorted(set(evidence_status)), coverage, requires_formal_model


def build_manifest(inventory: dict[str, Any], *, generated_from: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw_item in _list(inventory.get("items")):
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("artifact_group_id") or raw_item.get("classification") == "test":
            continue
        criticality = str(raw_item.get("criticality") or "P2")
        formal_status, evidence_status, coverage, requires_formal_model = _coverage_for_item(
            raw_item
        )
        entry: dict[str, Any] = {
            "path": raw_item.get("path"),
            "classification": raw_item.get("classification"),
            "kind": raw_item.get("kind"),
            "criticality": criticality,
            "release_blocking": _release_blocking(criticality),
            "formal_status": formal_status,
            "evidence_status": evidence_status,
            "flags": raw_item.get("flags") or {},
            "coverage": coverage,
            "symbols": [],
        }
        if requires_formal_model:
            entry["requires_formal_model"] = True
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
                            "observable_contract": "Symbol is present in the generated verification inventory and inherits file-level evidence.",
                        }
                    ],
                }
            )
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
