#!/usr/bin/env bash
set -euo pipefail

template="${SUPERVISOR_CONFIG_TEMPLATE:-/etc/supervisor/conf.d/supervisord.conf}"
output="${SUPERVISOR_CONFIG_OUTPUT:-/tmp/deepresearch-flow-supervisord.conf}"
supervisor_bin="${SUPERVISOR_BIN:-/usr/bin/supervisord}"
python_bin="${PYTHON_BIN:-python3}"

if [[ ! -f "$template" ]]; then
  echo "[ERROR] Supervisor configuration template is missing." >&2
  exit 1
fi

bridge="${PAPER_PIPELINE_ENABLED:-}"
case "$bridge" in
  ""|0|false|no|off|1|true|yes|on) ;;
  *)
    echo "[ERROR] PAPER_PIPELINE_ENABLED must be 0 or 1." >&2
    exit 1
    ;;
esac

pipeline_bridge_enabled=false
if [[ "$bridge" == "1" || "$bridge" == "true" || "$bridge" == "yes" || "$bridge" == "on" ]]; then
  pipeline_bridge_enabled=true
fi

if [[ -n "${PAPER_DB_CONFIG:-}" ]]; then
  if ! "$python_bin" -c \
    'from deepresearch_flow.pipeline.runtime import validate_pipeline_environment; import os; validate_pipeline_environment(os.environ["PAPER_DB_CONFIG"], os.environ)' \
    >/dev/null 2>&1; then
    echo "[ERROR] Pipeline TOML/environment configuration is invalid or inconsistent." >&2
    exit 1
  fi
elif [[ "$pipeline_bridge_enabled" == true ]]; then
  echo "[ERROR] PAPER_DB_CONFIG is required when PAPER_PIPELINE_ENABLED is set." >&2
  exit 1
fi

mkdir -p "$(dirname "$output")"
cp "$template" "$output"

if [[ "$pipeline_bridge_enabled" == true ]]; then
  cat >> "$output" <<'EOF'

[program:pipeline-worker]
command=/usr/local/bin/start-pipeline-worker.sh
autostart=true
autorestart=true
startretries=3
stopsignal=TERM
stopasgroup=true
killasgroup=true
stopwaitsecs=120
stdout_logfile=/dev/stdout
stderr_logfile=/dev/stderr
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
EOF
fi

exec "$supervisor_bin" -c "$output"
