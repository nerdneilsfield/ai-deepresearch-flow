#!/usr/bin/env bash
set -euo pipefail

api_base="${PAPER_DB_API_BASE:-http://127.0.0.1:8000}"
body_limit="${PAPER_DB_NGINX_BODY_LIMIT:-500m}"
export PAPER_DB_NGINX_BODY_LIMIT="$body_limit"

if [[ ! "$body_limit" =~ ^[1-9][0-9]*(k|m|g)?$ ]]; then
  echo "[ERROR] PAPER_DB_NGINX_BODY_LIMIT must be a positive Nginx size such as 500m." >&2
  exit 1
fi

export PAPER_DB_API_BASE="$api_base"

nginx_template="${PAPER_DB_NGINX_TEMPLATE:-root}"
case "$nginx_template" in
  root|prefix) ;;
  *)
    echo "[ERROR] PAPER_DB_NGINX_TEMPLATE must be 'root' or 'prefix'." >&2
    exit 1
    ;;
esac

template_dir="${PAPER_DB_NGINX_TEMPLATE_DIR:-/etc/nginx/templates}"
template="${template_dir}/nginx.conf.${nginx_template}.template"
output_path="${PAPER_DB_NGINX_CONFIG_PATH:-/etc/nginx/nginx.conf}"
envsubst '${PAPER_DB_API_BASE} ${PAPER_DB_NGINX_BODY_LIMIT}' < "$template" > "$output_path"

exec nginx -g 'daemon off;'
