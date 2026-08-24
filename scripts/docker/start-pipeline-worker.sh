#!/usr/bin/env bash
set -euo pipefail

if [[ "${PAPER_PIPELINE_ENABLED:-}" != "1" && "${PAPER_PIPELINE_ENABLED:-}" != "true" && "${PAPER_PIPELINE_ENABLED:-}" != "yes" && "${PAPER_PIPELINE_ENABLED:-}" != "on" ]]; then
  echo "[ERROR] Pipeline Worker requires PAPER_PIPELINE_ENABLED=1." >&2
  exit 1
fi
if [[ -z "${PAPER_DB_CONFIG:-}" ]]; then
  echo "[ERROR] Pipeline Worker requires PAPER_DB_CONFIG." >&2
  exit 1
fi

cmd=(
  "${PYTHON_BIN:-python3}" -m deepresearch_flow.pipeline.runtime
  --config "$PAPER_DB_CONFIG"
  --ocr-config "${PAPER_OCR_CONFIG:-ocr.toml}"
)
if [[ -n "${PAPER_DB_SNAPSHOT_DB:-}" ]]; then
  cmd+=(--snapshot-db "${PAPER_DB_SNAPSHOT_DB}")
fi
if [[ -n "${PAPER_DB_EMBED_DB:-}" ]]; then
  cmd+=(--vector-dir "$PAPER_DB_EMBED_DB")
fi
if [[ -n "${PAPER_PIPELINE_WORKER_ID:-}" ]]; then
  cmd+=(--worker-id "$PAPER_PIPELINE_WORKER_ID")
fi
exec "${cmd[@]}"
