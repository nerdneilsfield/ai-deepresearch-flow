from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "verification" / "check_manifest_coverage.py"
BOOTSTRAP_SCRIPT = REPO_ROOT / "tools" / "verification" / "generate_bootstrap_manifest.py"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _inventory() -> dict[str, object]:
    return {
        "version": 1,
        "repo_root": ".",
        "items": [
            {
                "path": "pkg/auth.py",
                "kind": "python_module",
                "classification": "source",
                "criticality": "P0",
                "flags": {"security_boundary": True},
                "symbols": [
                    {"stable_id": "py:pkg.auth:TokenCache.put#abc", "name": "put", "kind": "method"}
                ],
            },
            {
                "path": "frontend/public/pdfjs/viewer.js",
                "kind": "vendored_asset",
                "classification": "asset",
                "artifact_group_id": "vendor:frontend-public-pdfjs",
                "generated_reason": "vendored/generated static asset bundle",
                "criticality": "P2",
                "flags": {},
                "symbols": [],
            },
        ],
        "config_items": [
            {
                "config_id": "toml:pyproject.toml:project.version",
                "path": "pyproject.toml",
                "selector": "project.version",
                "kind": "toml_key",
                "criticality": "P1",
            }
        ],
        "evidence_assets": [
            {
                "evidence_id": "evidence:tests/test_auth.py",
                "path": "tests/test_auth.py",
                "target_inventory_ids": ["py:pkg.auth:TokenCache.put#abc"],
                "evidence_class": "UNIT_BLACK_BOX",
                "command": "pytest tests/test_auth.py -q",
                "black_box_contract": "observable behavior only",
                "deterministic_mode": True,
            }
        ],
        "artifact_groups": [
            {
                "artifact_group_id": "vendor:frontend-public-pdfjs",
                "path_prefix": "frontend/public/pdfjs",
                "upstream_name": "pdfjs",
                "file_count": 1,
                "checksum_manifest": "sha256:abc",
                "validation_command": "python -m pytest --version",
                "justification": "vendored/generated static runtime asset bundle",
            }
        ],
    }


def _complete_manifest(tmp_path: Path) -> dict[str, object]:
    model = tmp_path / "tools" / "formal" / "check_model.py"
    conformance = tmp_path / "tests" / "test_conformance.py"
    fault = tmp_path / "tests" / "test_fault.py"
    unit = tmp_path / "tests" / "test_auth.py"
    for file in (model, conformance, fault, unit):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# evidence\n", encoding="utf-8")
    return {
        "version": 1,
        "items": [
            {
                "path": "pkg/auth.py",
                "classification": "source",
                "criticality": "P0",
                "formal_status": "finite_model",
                "evidence_status": ["unit", "fault", "conformance"],
                "symbols": [
                    {
                        "stable_id": "py:pkg.auth:TokenCache.put#abc",
                        "observable_contract": "put/get/reopen public behavior",
                        "coverage": [
                            {
                                "kind": "MODEL_PROOF",
                                "path": "tools/formal/check_model.py",
                                "checker_command": "python tools/formal/check_model.py --depth 6",
                            },
                            {
                                "kind": "IMPLEMENTATION_REFINEMENT_CHECK",
                                "path": "tests/test_conformance.py",
                            },
                            {"kind": "FAULT_INJECTION", "path": "tests/test_fault.py"},
                            {"kind": "UNIT_BLACK_BOX", "path": "tests/test_auth.py"},
                        ],
                    }
                ],
            }
        ],
        "config_items": [
            {
                "config_id": "toml:pyproject.toml:project.version",
                "validation": {"command": "python tools/formal/check_model.py --help"},
            }
        ],
        "evidence_assets": [
            {
                "evidence_id": "evidence:tests/test_auth.py",
                "path": "tests/test_auth.py",
                "target_inventory_ids": ["py:pkg.auth:TokenCache.put#abc"],
                "evidence_class": "UNIT_BLACK_BOX",
                "command": "pytest tests/test_auth.py -q",
                "black_box_contract": "observable behavior only",
            }
        ],
        "artifact_groups": [
            {
                "artifact_group_id": "vendor:frontend-public-pdfjs",
                "path_prefix": "frontend/public/pdfjs",
                "upstream_name": "pdfjs",
                "file_count": 1,
                "checksum_manifest": "sha256:abc",
                "validation_command": "python -m pytest --version",
                "justification": "vendored/generated static runtime asset bundle",
            }
        ],
    }


def _write_case(tmp_path: Path, manifest: dict[str, object]) -> tuple[Path, Path]:
    inventory = tmp_path / "inventory.json"
    manifest_path = tmp_path / "manifest.yml"
    _write_json(inventory, _inventory())
    _write_json(manifest_path, manifest)
    return inventory, manifest_path


def test_manifest_checker_accepts_complete_observable_coverage(tmp_path: Path) -> None:
    inventory, manifest = _write_case(tmp_path, _complete_manifest(tmp_path))

    result = _run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--manifest",
            str(manifest),
        ],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "coverage ok" in result.stdout


def test_manifest_checker_rejects_missing_path_symbol_config_and_artifact(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["items"] = []
    manifest["config_items"] = []
    manifest["artifact_groups"] = []
    inventory, manifest_path = _write_case(tmp_path, manifest)

    result = _run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--manifest",
            str(manifest_path),
        ],
        tmp_path,
    )

    assert result.returncode != 0
    assert "uncovered path: pkg/auth.py" in result.stderr
    assert "uncovered symbol: py:pkg.auth:TokenCache.put#abc" in result.stderr
    assert "uncovered config item: toml:pyproject.toml:project.version" in result.stderr
    assert "uncovered artifact group: vendor:frontend-public-pdfjs" in result.stderr


def test_manifest_checker_rejects_p0_without_model_conformance_or_gap(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    symbol = manifest["items"][0]["symbols"][0]
    symbol["coverage"] = [{"kind": "UNIT_BLACK_BOX", "path": "tests/test_auth.py"}]
    inventory, manifest_path = _write_case(tmp_path, manifest)

    result = _run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--manifest",
            str(manifest_path),
        ],
        tmp_path,
    )

    assert result.returncode != 0
    assert "P0 missing MODEL_PROOF" in result.stderr
    assert "P0 missing IMPLEMENTATION_REFINEMENT_CHECK" in result.stderr


def test_manifest_checker_rejects_missing_evidence_path_and_duplicate_ids(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    symbol = manifest["items"][0]["symbols"][0]
    symbol["coverage"].append(
        {
            "kind": "FUZZ_TEST",
            "path": "tests/missing_fuzz.py",
            "observable_contract": "public behavior",
        }
    )
    manifest["items"].append(manifest["items"][0])
    inventory, manifest_path = _write_case(tmp_path, manifest)

    result = _run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--manifest",
            str(manifest_path),
        ],
        tmp_path,
    )

    assert result.returncode != 0
    assert "missing evidence path: tests/missing_fuzz.py" in result.stderr
    assert "duplicate manifest path: pkg/auth.py" in result.stderr


def test_bootstrap_manifest_generator_emits_checker_acceptable_manifest(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.json"
    manifest_path = tmp_path / "manifest.yml"
    _write_json(inventory_path, _inventory())
    for path in ["tests/test_auth.py", "docs/verification/repo-verification-inventory.json"]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# evidence\n", encoding="utf-8")

    generated = _run(
        [
            sys.executable,
            str(BOOTSTRAP_SCRIPT),
            "--inventory",
            str(inventory_path),
            "--output",
            str(manifest_path),
        ],
        tmp_path,
    )
    assert generated.returncode == 0, generated.stderr

    checked = _run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(tmp_path),
            "--inventory",
            str(inventory_path),
            "--manifest",
            str(manifest_path),
        ],
        tmp_path,
    )

    assert checked.returncode == 0, checked.stderr
    assert "coverage ok" in checked.stdout
