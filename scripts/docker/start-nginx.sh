#!/usr/bin/env bash
set -euo pipefail

api_base="${PAPER_DB_API_BASE:-http://127.0.0.1:8000}"

export PAPER_DB_API_BASE="$api_base"

template=/etc/nginx/templates/nginx.conf.root.template
envsubst '${PAPER_DB_API_BASE}' < "$template" > /etc/nginx/nginx.conf

exec nginx -g 'daemon off;'
