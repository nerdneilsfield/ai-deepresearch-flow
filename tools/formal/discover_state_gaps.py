#!/usr/bin/env python3
"""Enumerate finite protocol/fault state obligations and report uncovered gaps.

This is not a proof of implementation correctness. It is an adversarial catalog
checker: given an independent finite state/fault taxonomy, every generated state
combination must be mapped to an explicit expected behavior obligation.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from collections import Counter
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def _load_catalog(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json" or yaml is None:
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("catalog must be an object")
    return data


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _allowed_values(selector: Any) -> set[str]:
    if isinstance(selector, list):
        return {str(item) for item in selector}
    return {str(selector)}


def _state_matches(state: dict[str, str], obligation: dict[str, Any]) -> bool:
    when = _dict(obligation.get("when"))
    for key, selector in when.items():
        if key not in state:
            return False
        if state[key] not in _allowed_values(selector):
            return False
    return True


def _enumerate_states(dimensions: dict[str, Any]) -> list[dict[str, str]]:
    names: list[str] = []
    values: list[list[str]] = []
    for name, raw_values in dimensions.items():
        vals = [str(item) for item in _list(raw_values)]
        if not vals:
            raise ValueError(f"dimension {name} must contain at least one value")
        names.append(str(name))
        values.append(vals)
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*values)]


def _status(obligation: dict[str, Any]) -> str:
    return str(obligation.get("status") or "implemented")


def _priority(obligation: dict[str, Any]) -> int:
    value = obligation.get("priority", 0)
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _evidence_paths(obligation: dict[str, Any]) -> list[str]:
    raw = obligation.get("evidence")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    if isinstance(raw, str) and raw:
        return [raw]
    return []


def _check_obligation_schema(
    subsystem_id: str, obligation: dict[str, Any], *, base_path: Path
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    missing_evidence: list[str] = []
    if not obligation.get("id"):
        errors.append(f"{subsystem_id}: obligation missing id")
    if not obligation.get("expected"):
        errors.append(
            f"{subsystem_id}: obligation {obligation.get('id', '<missing>')} missing expected"
        )
    if _status(obligation) not in {"implemented", "known_gap", "uncovered"}:
        errors.append(
            f"{subsystem_id}: obligation {obligation.get('id', '<missing>')} has invalid status"
        )
    if not isinstance(obligation.get("when"), dict):
        errors.append(
            f"{subsystem_id}: obligation {obligation.get('id', '<missing>')} missing when"
        )
    if _status(obligation) == "implemented":
        evidence = _evidence_paths(obligation)
        if not evidence:
            missing_evidence.append(str(obligation.get("id", "<missing>")))
        for raw_path in evidence:
            evidence_path = Path(raw_path)
            if not evidence_path.is_absolute():
                evidence_path = base_path / evidence_path
            if not evidence_path.exists():
                missing_evidence.append(str(obligation.get("id", "<missing>")))
                break
    return errors, missing_evidence


def discover(catalog: dict[str, Any], *, base_path: Path | None = None) -> dict[str, Any]:
    uncovered: list[dict[str, Any]] = []
    known_gaps: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    missing_evidence: list[dict[str, Any]] = []
    schema_errors: list[str] = []
    total_states = 0
    resolved_base = Path.cwd() if base_path is None else base_path

    subsystems = _list(catalog.get("subsystems"))
    if not subsystems:
        schema_errors.append("catalog must contain at least one subsystem")

    for subsystem in subsystems:
        if not isinstance(subsystem, dict):
            schema_errors.append("subsystem entry must be an object")
            continue
        subsystem_id = str(subsystem.get("id") or "<missing>")
        dimensions = _dict(subsystem.get("dimensions"))
        obligations = [
            item for item in _list(subsystem.get("obligations")) if isinstance(item, dict)
        ]
        if not dimensions:
            schema_errors.append(f"{subsystem_id}: missing dimensions")
            continue
        if not obligations:
            schema_errors.append(f"{subsystem_id}: missing obligations")
        for obligation in obligations:
            obligation_errors, obligation_missing_evidence = _check_obligation_schema(
                subsystem_id, obligation, base_path=resolved_base
            )
            schema_errors.extend(obligation_errors)
            for obligation_id in obligation_missing_evidence:
                missing_evidence.append({"subsystem": subsystem_id, "obligation": obligation_id})
        states = _enumerate_states(dimensions)
        total_states += len(states)
        for state in states:
            matches = [ob for ob in obligations if _state_matches(state, ob)]
            if matches:
                highest = max(_priority(item) for item in matches)
                winning_matches = [item for item in matches if _priority(item) == highest]
                row = {
                    "subsystem": subsystem_id,
                    "state": state,
                    "obligations": [str(item.get("id")) for item in winning_matches],
                }
                if len(winning_matches) > 1:
                    ambiguous.append(row)
                    continue
                winner = winning_matches[0]
                obligation_status = _status(winner)
                gap_like_expected = str(winner.get("expected", "")).startswith(
                    ("gap", "known_gap", "unhandled")
                )
                if obligation_status in {"known_gap", "uncovered"} or gap_like_expected:
                    known_gaps.append(row)
                else:
                    covered.append(row)
            else:
                uncovered.append({"subsystem": subsystem_id, "state": state})

    if total_states == 0 and not schema_errors:
        schema_errors.append("catalog generated zero states")

    known_gap_obligations = Counter(
        obligation for gap in known_gaps for obligation in gap.get("obligations", [])
    )
    uncovered_by_subsystem = Counter(str(gap.get("subsystem")) for gap in uncovered)
    known_by_subsystem = Counter(str(gap.get("subsystem")) for gap in known_gaps)
    missing_evidence_obligations = Counter(str(item.get("obligation")) for item in missing_evidence)
    status = (
        "schema_error"
        if schema_errors
        else ("gap" if uncovered or known_gaps or ambiguous or missing_evidence else "covered")
    )
    return {
        "tool": "state_gap_discovery",
        "status": status,
        "total_states": total_states,
        "covered_count": len(covered),
        "uncovered_count": len(uncovered),
        "known_gap_count": len(known_gaps),
        "ambiguous_count": len(ambiguous),
        "missing_evidence_count": len(missing_evidence),
        "known_gap_obligations": dict(sorted(known_gap_obligations.items())),
        "missing_evidence_obligations": dict(sorted(missing_evidence_obligations.items())),
        "uncovered_by_subsystem": dict(sorted(uncovered_by_subsystem.items())),
        "known_gap_by_subsystem": dict(sorted(known_by_subsystem.items())),
        "schema_errors": schema_errors,
        "uncovered": uncovered,
        "known_gaps": known_gaps,
        "ambiguous": ambiguous,
        "missing_evidence": missing_evidence,
    }


def _print_text(payload: dict[str, Any]) -> None:
    for error in payload["schema_errors"]:
        print(f"STATE_GAP SCHEMA_ERROR {error}")
    for obligation, count in payload.get("known_gap_obligations", {}).items():
        print(f"STATE_GAP KNOWN_GAP_SUMMARY {obligation} count={count}")
    for obligation, count in payload.get("missing_evidence_obligations", {}).items():
        print(f"STATE_GAP MISSING_EVIDENCE_SUMMARY {obligation} count={count}")
    for gap in payload["uncovered"][:200]:
        print(f"STATE_GAP UNCOVERED {gap['subsystem']} {json.dumps(gap['state'], sort_keys=True)}")
    for gap in payload.get("known_gaps", [])[:200]:
        print(
            f"STATE_GAP KNOWN_GAP {gap['subsystem']} {json.dumps(gap['state'], sort_keys=True)} obligations={','.join(gap['obligations'])}"
        )
    for gap in payload.get("ambiguous", [])[:200]:
        print(
            f"STATE_GAP AMBIGUOUS {gap['subsystem']} {json.dumps(gap['state'], sort_keys=True)} obligations={','.join(gap['obligations'])}"
        )
    total_gap_rows = (
        payload["uncovered_count"]
        + payload.get("known_gap_count", 0)
        + payload.get("ambiguous_count", 0)
        + payload.get("missing_evidence_count", 0)
    )
    if total_gap_rows > 200:
        print(f"STATE_GAP GAP_TRUNCATED remaining={total_gap_rows - 200}")
    print(
        "STATE_GAP {status} total={total} covered={covered} uncovered={uncovered} known_gaps={known} ambiguous={ambiguous} missing_evidence={missing_evidence}".format(
            status=str(payload["status"]).upper(),
            total=payload["total_states"],
            covered=payload["covered_count"],
            uncovered=payload["uncovered_count"],
            known=payload.get("known_gap_count", 0),
            ambiguous=payload.get("ambiguous_count", 0),
            missing_evidence=payload.get("missing_evidence_count", 0),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("docs/verification/state-space-obligations.yml"),
        help="state/fault obligation catalog",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-gap", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = discover(_load_catalog(args.catalog), base_path=Path.cwd())
    except Exception as exc:
        payload = {
            "tool": "state_gap_discovery",
            "status": "schema_error",
            "total_states": 0,
            "covered_count": 0,
            "uncovered_count": 0,
            "known_gap_count": 0,
            "ambiguous_count": 0,
            "missing_evidence_count": 0,
            "known_gap_obligations": {},
            "missing_evidence_obligations": {},
            "uncovered_by_subsystem": {},
            "known_gap_by_subsystem": {},
            "schema_errors": [str(exc)],
            "uncovered": [],
            "known_gaps": [],
            "ambiguous": [],
            "missing_evidence": [],
        }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(payload)

    if payload["status"] == "schema_error":
        return 2
    if args.fail_on_gap and (
        payload["uncovered_count"]
        or payload.get("known_gap_count", 0)
        or payload.get("ambiguous_count", 0)
        or payload.get("missing_evidence_count", 0)
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
