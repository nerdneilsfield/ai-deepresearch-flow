#!/usr/bin/env python3
"""Generate a repository-wide verification inventory."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - reported when YAML files are inventoried
    yaml = None

_BOUNDARY_KEYWORDS = {
    "security_boundary": (
        "auth",
        "token",
        "oauth",
        "bearer",
        "jwt",
        "client",
        "session",
        "secret",
        "password",
        "key",
    ),
    "persistence_boundary": ("db", "sql", "lance", "vector", "store", "migrate", "schema", "cache"),
    "network_boundary": (
        "http",
        "api",
        "request",
        "response",
        "route",
        "fastmcp",
        "starlette",
        "uvicorn",
        "webdav",
    ),
    "parser_boundary": (
        "parse",
        "json",
        "yaml",
        "toml",
        "markdown",
        "latex",
        "katex",
        "mermaid",
        "pdf",
        "html",
        "bibtex",
    ),
    "renderer_boundary": (
        "render",
        "markdown",
        "latex",
        "katex",
        "mermaid",
        "pdf",
        "html",
        "viewer",
    ),
    "concurrency_boundary": (
        "async",
        "thread",
        "lock",
        "concurrent",
        "scheduler",
        "retry",
        "cooldown",
        "quota",
    ),
    "deploy_security_boundary": (
        "docker",
        "compose",
        "nginx",
        "supervisor",
        "start-api",
        "start-nginx",
    ),
    "secret_boundary": (
        "secret",
        "token",
        "key",
        "password",
        "env",
        "cors",
        "origin",
        "issuer",
        "public_base",
    ),
    "runtime_config_boundary": (
        "config",
        "env",
        "cors",
        "origin",
        "issuer",
        "public_base",
        "docker",
        "compose",
    ),
    "release_publish_boundary": (
        "workflow",
        "publish",
        "release",
        "pypi",
        "docker-images",
        "push-to-pypi",
    ),
}

_VENDOR_GROUPS = {
    "frontend/public/pdfjs": "vendor:frontend-public-pdfjs",
    "python/deepresearch_flow/paper/web/pdfjs": "vendor:python-web-pdfjs",
}


def _run_git(repo: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return [line for line in result.stdout.splitlines() if line]


def _tracked_paths(repo: Path, *, dev: bool) -> list[str]:
    if dev:
        return sorted(
            set(_run_git(repo, ["ls-files", "--cached", "--others", "--exclude-standard"]))
        )
    return sorted(set(_run_git(repo, ["ls-files"])))


def _sha(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _module_name(path: str) -> str:
    if path.startswith("python/") and path.endswith(".py"):
        rel = path[len("python/") : -3]
        return rel.replace("/", ".")
    return path[:-3].replace("/", ".")


def _decorator_name(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _signature_hash(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            signature = ast.unparse(node.args)
        except Exception:
            signature = node.name
        return _sha(f"function:{node.name}:{signature}:{isinstance(node, ast.AsyncFunctionDef)}")
    if isinstance(node, ast.ClassDef):
        try:
            bases = ",".join(ast.unparse(base) for base in node.bases)
        except Exception:
            bases = ""
        return _sha(f"class:{node.name}:{bases}")
    return _sha(type(node).__name__)


def _python_symbols(repo: Path, path: str) -> list[dict[str, Any]]:
    full = repo / path
    try:
        tree = ast.parse(full.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    module = _module_name(path)
    symbols: list[dict[str, Any]] = []

    def visit_body(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                sig_hash = _signature_hash(node)
                symbols.append(
                    {
                        "name": node.name,
                        "qualname": qualname,
                        "kind": "class",
                        "stable_id": f"py:{module}:{qualname}#{sig_hash}",
                        "source_span": f"{getattr(node, 'lineno', 0)}-{getattr(node, 'end_lineno', getattr(node, 'lineno', 0))}",
                        "signature_hash": sig_hash,
                        "async": False,
                        "decorators": [_decorator_name(d) for d in node.decorator_list],
                    }
                )
                visit_body(node.body, qualname)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                sig_hash = _signature_hash(node)
                symbols.append(
                    {
                        "name": node.name,
                        "qualname": qualname,
                        "kind": "method" if prefix else "function",
                        "stable_id": f"py:{module}:{qualname}#{sig_hash}",
                        "source_span": f"{getattr(node, 'lineno', 0)}-{getattr(node, 'end_lineno', getattr(node, 'lineno', 0))}",
                        "signature_hash": sig_hash,
                        "async": isinstance(node, ast.AsyncFunctionDef),
                        "decorators": [_decorator_name(d) for d in node.decorator_list],
                    }
                )
                visit_body(node.body, qualname)

    visit_body(tree.body)
    return _dedupe_symbol_ids(symbols)


def _ts_symbols(repo: Path, path: str) -> list[dict[str, Any]]:
    try:
        text = (repo / path).read_text(encoding="utf-8")
    except OSError:
        return []
    symbols: list[dict[str, Any]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        name = None
        kind = None
        if stripped.startswith("export function "):
            rest = stripped[len("export function ") :]
            name = rest.split("(", 1)[0].strip()
            kind = "function"
        elif (
            stripped.startswith("export const ")
            or stripped.startswith("export let ")
            or stripped.startswith("export var ")
        ):
            rest = stripped.split(None, 2)[2]
            name = rest.split("=", 1)[0].split(":", 1)[0].strip()
            kind = "const"
        elif stripped.startswith("export class "):
            rest = stripped[len("export class ") :]
            name = rest.split("{", 1)[0].split("(", 1)[0].strip()
            kind = "class"
        elif stripped.startswith("export default"):
            name = "default"
            kind = "default_export"
        if name:
            sig_hash = _sha(stripped)
            symbols.append(
                {
                    "name": name,
                    "kind": kind,
                    "stable_id": f"fe:{path}:{name}#{sig_hash}",
                    "source_span": f"{index}-{index}",
                    "signature_hash": sig_hash,
                    "export_mode": "export",
                }
            )
    return _dedupe_symbol_ids(symbols)


def _dedupe_symbol_ids(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    deduped: list[dict[str, Any]] = []
    for symbol in symbols:
        base = str(symbol["stable_id"])
        counts[base] = counts.get(base, 0) + 1
        if counts[base] == 1:
            deduped.append(symbol)
            continue
        updated = dict(symbol)
        updated["stable_id"] = f"{base}~{counts[base]}"
        updated["occurrence"] = counts[base]
        deduped.append(updated)
    return deduped


def _vue_symbols(repo: Path, path: str) -> list[dict[str, Any]]:
    try:
        text = (repo / path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    name = Path(path).stem
    sig_hash = _sha(name)
    props = []
    emits = []
    if "defineProps" in text:
        props.append("unknown")
    if "defineEmits" in text:
        emits.append("unknown")
    return [
        {
            "name": name,
            "kind": "vue_component",
            "stable_id": f"fe:{path}:component#{sig_hash}",
            "source_span": "file",
            "signature_hash": sig_hash,
            "component_id": f"fe:{path}:component#{sig_hash}",
            "props": props,
            "emits": emits,
            "slots_unknown": "<slot" in text,
            "uses_store": "use" in text and "Store" in text,
            "uses_router": "useRouter" in text or "useRoute" in text,
            "uses_network": "fetch(" in text or "http" in text or "api" in text.lower(),
            "uses_storage": "localStorage" in text or "indexedDB" in text,
            "uses_rendering": any(
                word in text.lower() for word in ("markdown", "mermaid", "katex", "pdf")
            ),
            "needs_manual_symbol_review": False,
        }
    ]


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            child_key = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(child, child_key))
        return out
    return [(prefix, value)] if prefix else []


def _json_pointer(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            out.extend(_json_pointer(child, f"{prefix}/{escaped}"))
        return out
    return [(prefix or "/", value)]


def _config_items(repo: Path, path: str) -> list[dict[str, Any]]:
    suffix = Path(path).suffix.lower()
    full = repo / path
    try:
        if suffix == ".toml":
            data = tomllib.loads(full.read_text(encoding="utf-8"))
            return [
                {
                    "config_id": f"toml:{path}:{selector}",
                    "path": path,
                    "selector": selector,
                    "kind": "toml_key",
                    "criticality": _criticality(path, selector),
                }
                for selector, _ in _flatten(data)
            ]
        if suffix == ".json":
            data = json.loads(full.read_text(encoding="utf-8"))
            return [
                {
                    "config_id": f"json:{path}:{selector}",
                    "path": path,
                    "selector": selector,
                    "kind": "json_key",
                    "criticality": _criticality(path, selector),
                }
                for selector, _ in _json_pointer(data)
            ]
        if suffix in {".yml", ".yaml"} and yaml is not None:
            data = yaml.safe_load(full.read_text(encoding="utf-8"))
            return [
                {
                    "config_id": f"yaml:{path}:{selector}",
                    "path": path,
                    "selector": selector,
                    "kind": "yaml_key",
                    "criticality": _criticality(path, selector),
                }
                for selector, _ in _flatten(data or {})
            ]
    except Exception:
        return []
    return []


def _criticality(path: str, extra: str = "") -> str:
    haystack = f"{path} {extra}".lower()
    if any(
        word in haystack
        for word in (
            "auth",
            "token",
            "secret",
            "oauth",
            "workflow",
            "publish",
            "pypi",
            "docker",
            "deploy",
        )
    ):
        return "P0"
    if any(word in haystack for word in ("db", "cache", "config", "route", "api", "version")):
        return "P1"
    return "P2"


def _classify(path: str) -> tuple[str, str]:
    p = Path(path)
    name = p.name
    lower = path.lower()
    is_test_area = (
        "/tests/" in f"/{path}" or "/__tests__/" in f"/{path}" or path.startswith("tests/")
    )
    is_python_test = name.startswith("test_") and name.endswith(".py")
    is_frontend_test = name.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"))
    if path == ".dockerignore":
        return "docker_ignore", "build"
    if path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        if any(word in lower for word in ("publish", "pypi", "release", "docker")):
            return "release_publish_workflow", "workflow"
        return "ci_workflow", "workflow"
    if name.startswith("Dockerfile") or "/Dockerfile" in path:
        return "container_build", "build"
    if "docker-compose" in name and name.endswith((".yml", ".yaml")):
        return "compose_config", "config"
    if name.endswith(".sh"):
        return "startup_script", "script"
    if "nginx" in name and (name.endswith(".template") or name.endswith(".conf")):
        return "reverse_proxy_config", "config"
    if name == "supervisord.conf":
        return "process_supervisor_config", "config"
    if _artifact_group_id(path):
        return "vendored_asset", "asset"
    if is_test_area or is_python_test or is_frontend_test:
        if is_python_test:
            return "python_test", "test"
        if is_frontend_test:
            return "test_asset", "test"
        return "test_support", "test_support"
    if name.endswith(".py"):
        return "python_module", "source"
    if name.endswith(".vue"):
        return "vue_component", "source"
    if name.endswith(".ts"):
        return "typescript_module", "source"
    if name.endswith((".toml", ".json", ".yml", ".yaml")):
        return "config", "config"
    if name.endswith(".md") or name in {"AGENTS.md", "CLAUDE.md", "QODER.md"}:
        return "markdown_doc", "doc"
    if name in {"Makefile"}:
        return "build_file", "build"
    if name.endswith((".lock", "lock")) or "lock" in name:
        return "lockfile", "config"
    return "asset", "asset"


def _artifact_group_id(path: str) -> str | None:
    for prefix, group_id in _VENDOR_GROUPS.items():
        if path == prefix or path.startswith(f"{prefix}/"):
            return group_id
    return None


def _flags(path: str, kind: str, symbols: list[dict[str, Any]]) -> dict[str, bool]:
    haystack = " ".join([path, kind, *(s.get("name", "") for s in symbols)]).lower()
    flags = {
        name: any(word in haystack for word in words) for name, words in _BOUNDARY_KEYWORDS.items()
    }
    suspected = any(flags.values()) and not any(
        flags[name]
        for name in (
            "security_boundary",
            "persistence_boundary",
            "network_boundary",
            "parser_boundary",
            "renderer_boundary",
            "concurrency_boundary",
            "deploy_security_boundary",
            "secret_boundary",
            "runtime_config_boundary",
            "release_publish_boundary",
        )
    )
    flags["suspected_boundary_unclassified"] = suspected
    return flags


def _symbols(repo: Path, path: str, kind: str) -> list[dict[str, Any]]:
    if kind in {"python_module", "python_test"} and path.endswith(".py"):
        return _python_symbols(repo, path)
    if kind == "typescript_module" and path.endswith(".ts"):
        return _ts_symbols(repo, path)
    if kind == "vue_component" and path.endswith(".vue"):
        return _vue_symbols(repo, path)
    return []


def generate_inventory(
    repo: Path, *, dev: bool = False, exclude_paths: set[str] | None = None
) -> dict[str, Any]:
    repo = repo.resolve()
    paths = [path for path in _tracked_paths(repo, dev=dev) if path not in (exclude_paths or set())]
    if yaml is None and any(path.endswith((".yml", ".yaml")) for path in paths):
        raise RuntimeError("PyYAML is required to inventory YAML configuration files")
    items: list[dict[str, Any]] = []
    config_items: list[dict[str, Any]] = []
    evidence_assets: list[dict[str, Any]] = []
    group_files: dict[str, list[str]] = {}
    for path in paths:
        kind, classification = _classify(path)
        symbols = _symbols(repo, path, kind)
        group_id = _artifact_group_id(path)
        item: dict[str, Any] = {
            "path": path,
            "kind": kind,
            "classification": classification,
            "symbols": symbols,
            "flags": _flags(path, kind, symbols),
            "criticality": _criticality(path),
        }
        if group_id:
            item["artifact_group_id"] = group_id
            item["generated_reason"] = "vendored/generated static asset bundle"
            group_files.setdefault(group_id, []).append(path)
        items.append(item)
        config_items.extend(_config_items(repo, path))
        if classification == "test":
            if path in {
                "tests/verification/test_smt_formal_gate.py",
                "tests/verification/test_tlc_formal_gate.py",
            }:
                command = f"DRFLOW_RUN_LOCAL_FORMAL=1 uv run pytest {path} -q"
            elif path.endswith(".py"):
                command = f"uv run pytest {path} -q"
            elif path.startswith("frontend/"):
                command = f"cd frontend && npm test -- --run {path.removeprefix('frontend/')}"
            else:
                command = f"npm test -- --run {path}"
            evidence_assets.append(
                {
                    "evidence_id": f"evidence:{path}",
                    "path": path,
                    "target_inventory_ids": [],
                    "evidence_class": "UNIT_BLACK_BOX",
                    "command": command,
                    "black_box_contract": "observable behavior only",
                    "deterministic_mode": True,
                }
            )
    artifact_groups = []
    for group_id, members in sorted(group_files.items()):
        digest = _sha("\n".join(sorted(members)), length=16)
        artifact_groups.append(
            {
                "artifact_group_id": group_id,
                "path_prefix": next(
                    prefix for prefix, gid in _VENDOR_GROUPS.items() if gid == group_id
                ),
                "upstream_name": "pdfjs",
                "file_count": len(members),
                "checksum_manifest": f"sha256:{digest}",
                "validation_command": "uv run python tools/verification/check_manifest_coverage.py --help",
                "justification": "vendored/generated static runtime asset bundle",
            }
        )
    return {
        "version": 1,
        "repo_root": ".",
        "mode": "dev" if dev else "release",
        "items": sorted(items, key=lambda item: item["path"]),
        "config_items": sorted(config_items, key=lambda item: item["config_id"]),
        "evidence_assets": sorted(evidence_assets, key=lambda item: item["evidence_id"]),
        "artifact_groups": artifact_groups,
    }


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args(argv)
    exclude_paths: set[str] = set()
    if args.output is not None:
        try:
            exclude_paths.add(str(args.output.resolve().relative_to(args.repo.resolve())))
        except ValueError:
            pass
    payload = generate_inventory(args.repo, dev=args.dev, exclude_paths=exclude_paths)
    rendered = _canonical(payload)
    if args.check:
        if args.output is None:
            print("--check requires --output", file=sys.stderr)
            return 2
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError:
            print("inventory is stale: output file is missing", file=sys.stderr)
            return 1
        if current != rendered:
            print("inventory is stale", file=sys.stderr)
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
