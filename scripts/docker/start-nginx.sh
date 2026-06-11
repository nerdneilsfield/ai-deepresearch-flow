#!/usr/bin/env bash
set -euo pipefail

api_base="${PAPER_DB_API_BASE:-http://127.0.0.1:8000}"

export PAPER_DB_API_BASE="$api_base"

nginx_template="${PAPER_DB_NGINX_TEMPLATE:-root}"
case "$nginx_template" in
  root|prefix) ;;
  *)
    echo "[ERROR] PAPER_DB_NGINX_TEMPLATE must be 'root' or 'prefix'." >&2
    exit 1
    ;;
esac

template="/etc/nginx/templates/nginx.conf.${nginx_template}.template"
envsubst '${PAPER_DB_API_BASE}' < "$template" > /etc/nginx/nginx.conf

exec nginx -g 'daemon off;'
