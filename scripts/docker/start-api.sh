#!/usr/bin/env bash
set -euo pipefail

snapshot_db="${PAPER_DB_SNAPSHOT_DB:-/db/papers.db}"

exec deepresearch-flow paper db api serve \
  --snapshot-db "$snapshot_db"  \
  --host 0.0.0.0 --port 8000 \
  --static-base-url "${PAPER_DB_STATIC_BASE:-}" \
  --cors-origin "*"
