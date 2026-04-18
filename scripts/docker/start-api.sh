#!/usr/bin/env bash
set -euo pipefail

snapshot_db="${PAPER_DB_SNAPSHOT_DB:-/db/papers.db}"
advanced_env_count=0

if [[ -n "${PAPER_DB_EMBED_DB:-}" ]]; then
  advanced_env_count=$((advanced_env_count + 1))
fi
if [[ -n "${PAPER_DB_CONFIG:-}" ]]; then
  advanced_env_count=$((advanced_env_count + 1))
fi
if [[ -n "${SEARCH_ACCESS_TOKEN:-}" ]]; then
  advanced_env_count=$((advanced_env_count + 1))
fi

cmd=(
  deepresearch-flow paper db api serve
  --snapshot-db "$snapshot_db"
  --host 0.0.0.0 --port 8000
  --static-base-url "${PAPER_DB_STATIC_BASE:-}"
  --cors-origin "*"
)

if [[ "$advanced_env_count" -eq 1 ]]; then
  echo \
    "[ERROR] Partial advanced Docker configuration detected. " \
    "Set at least two of PAPER_DB_EMBED_DB, PAPER_DB_CONFIG, SEARCH_ACCESS_TOKEN " \
    "for embedded mode, or unset all three for basic mode." >&2
  exit 1
fi

if [[ "$advanced_env_count" -ge 2 ]]; then
  if [[ -n "${PAPER_DB_EMBED_DB:-}" ]]; then
    cmd+=(--embed-db "${PAPER_DB_EMBED_DB}")
  fi
  if [[ -n "${PAPER_DB_CONFIG:-}" ]]; then
    cmd+=(--config "${PAPER_DB_CONFIG}")
  fi
fi

exec "${cmd[@]}"
