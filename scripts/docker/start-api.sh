#!/usr/bin/env bash
set -euo pipefail

advanced_env_count=0
mcp_access_token="${MCP_ACCESS_TOKEN:-}"
pipeline_bridge="${PAPER_PIPELINE_ENABLED:-}"

case "$pipeline_bridge" in
  ""|0|false|no|off|1|true|yes|on) ;;
  *)
    echo "[ERROR] PAPER_PIPELINE_ENABLED must be 0 or 1." >&2
    exit 1
    ;;
esac
if [[ "$pipeline_bridge" == "1" || "$pipeline_bridge" == "true" || "$pipeline_bridge" == "yes" || "$pipeline_bridge" == "on" ]]; then
  if [[ -z "${PAPER_DB_CONFIG:-}" ]]; then
    echo "[ERROR] PAPER_DB_CONFIG is required when PAPER_PIPELINE_ENABLED=1." >&2
    exit 1
  fi
fi

if [[ "${MCP_PUBLIC_UNSAFE:-}" != "1" ]]; then
  if [[ -z "$mcp_access_token" || "$mcp_access_token" == "your-mcp-token" ]]; then
    echo \
      "[ERROR] Refusing to start with an unprotected or placeholder MCP token. " \
      "Set MCP_ACCESS_TOKEN to a private value, or set MCP_PUBLIC_UNSAFE=1 only for isolated local testing." >&2
    exit 1
  fi
fi

if [[ -n "${PAPER_DB_EMBED_DB:-}" ]]; then
  advanced_env_count=$((advanced_env_count + 1))
fi
if [[ -n "${PAPER_DB_CONFIG:-}" ]]; then
  advanced_env_count=$((advanced_env_count + 1))
fi
if [[ -n "${SEARCH_ACCESS_TOKEN:-}" ]]; then
  if [[ "${SEARCH_ACCESS_TOKEN}" == "your-token" ]]; then
    echo \
      "[ERROR] Refusing to start with placeholder SEARCH_ACCESS_TOKEN. " \
      "Set SEARCH_ACCESS_TOKEN to a private value or unset it for basic mode." >&2
    exit 1
  fi
  advanced_env_count=$((advanced_env_count + 1))
fi

cmd=(
  deepresearch-flow paper db api serve
  --host 0.0.0.0 --port 8000
  --static-base-url "${PAPER_DB_STATIC_BASE:-}"
)
if [[ -n "${PAPER_DB_SNAPSHOT_DB:-}" ]]; then
  cmd+=(--snapshot-db "${PAPER_DB_SNAPSHOT_DB}")
elif [[ "$pipeline_bridge" != "1" && "$pipeline_bridge" != "true" && "$pipeline_bridge" != "yes" && "$pipeline_bridge" != "on" ]]; then
  # Keep legacy non-pipeline containers on their historical bind mount.
  cmd+=(--snapshot-db "/db/papers.db")
fi

if [[ -v PAPER_DB_CORS_ORIGINS ]]; then
  cors_origins="${PAPER_DB_CORS_ORIGINS}"
elif [[ -v PAPER_DB_CORS_ORIGIN ]]; then
  cors_origins="${PAPER_DB_CORS_ORIGIN}"
else
  cors_origins="*"
fi
IFS=',' read -r -a cors_origin_list <<< "$cors_origins"
has_cors_origin=0
for origin in "${cors_origin_list[@]}"; do
  origin="${origin#"${origin%%[![:space:]]*}"}"
  origin="${origin%"${origin##*[![:space:]]}"}"
  if [[ -n "$origin" ]]; then
    has_cors_origin=1
    cmd+=(--cors-origin "$origin")
  fi
done
if [[ "$has_cors_origin" -eq 0 ]]; then
  echo "[ERROR] PAPER_DB_CORS_ORIGINS/PAPER_DB_CORS_ORIGIN must contain at least one origin." >&2
  exit 1
fi

if [[ "$advanced_env_count" -eq 1 && ! ( "$pipeline_bridge" == "1" || "$pipeline_bridge" == "true" || "$pipeline_bridge" == "yes" || "$pipeline_bridge" == "on" ) ]]; then
  echo \
    "[ERROR] Partial advanced Docker configuration detected. " \
    "Set at least two of PAPER_DB_EMBED_DB, PAPER_DB_CONFIG, SEARCH_ACCESS_TOKEN " \
    "for embedded mode, or unset all three for basic mode." >&2
  exit 1
fi

if [[ "$advanced_env_count" -ge 2 || ( "$pipeline_bridge" == "1" || "$pipeline_bridge" == "true" || "$pipeline_bridge" == "yes" || "$pipeline_bridge" == "on" ) ]]; then
  if [[ -n "${PAPER_DB_EMBED_DB:-}" ]]; then
    cmd+=(--embed-db "${PAPER_DB_EMBED_DB}")
  fi
  if [[ -n "${PAPER_DB_CONFIG:-}" ]]; then
    cmd+=(--config "${PAPER_DB_CONFIG}")
  fi
fi

exec "${cmd[@]}"
