#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

status=0
run_gate() {
  local name="$1"
  shift
  echo "==> $name"
  "$@"
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "gate failed: $name (exit $rc)" >&2
    status=1
  fi
}

run_gate "versions" uv run python tools/verification/check_versions.py
run_gate "doc secrets" uv run python tools/verification/check_doc_secrets.py
run_gate "supply chain" uv run python tools/verification/check_supply_chain.py

exit "$status"
