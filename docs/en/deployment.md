[← Back to README](../../README.md)

# Deployment

The recommended production setup is **front/back separation**:

- **Static CDN** hosts PDFs/Markdown/images/summaries.
- **API server** serves a read-only snapshot DB.
- **Frontend** is a separate static app (Vite build or any static host).

<p align="center">
  <img src="../../.github/assets/frontend.png" width="80%" alt="frontend" />
</p>

## Prerequisites

- Python 3.10+ with `uv`
- Node.js 18+ (for frontend build)
- Caddy or Nginx (for reverse proxy / static serving)
- Docker (optional, for containerized deployment)

---

## 1. Build Snapshot + Static Export

The build host must be able to read the original PDF/Markdown roots. The CDN only needs the exported directory.

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir /data/paper-static
```

Notes:

- `--pdf-root` should point to the directory containing original PDF files.
- `--md-root` / `--md-translated-root` should point to directories with Markdown and translated Markdown files.
- `--static-export-dir` is the output directory for static assets. Copy or mount this to your CDN.

---

## 2. Serve Static Assets

Static assets must be served with CORS headers and long-lived cache headers so the frontend can load them cross-origin.

### 2.1 Caddy

```caddyfile
:8002 {
  root * /data/paper-static
  encode zstd gzip

  @static path /pdf/* /md/* /md_translate/* /images/*
  header @static {
    Access-Control-Allow-Origin *
    Access-Control-Allow-Methods GET,HEAD,OPTIONS
    Access-Control-Allow-Headers *
    Cache-Control "public, max-age=31536000, immutable"
  }

  @options method OPTIONS
  respond @options 204

  file_server
}
```

### 2.2 Nginx (API + Frontend on One Domain, Static on Another)

```nginx
# Frontend + API (same domain)
server {
  listen 80;
  server_name frontend.example.com;

  root /var/www/paper-frontend;
  index index.html;

  location / {
    try_files $uri /index.html;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  # Static-bearer MCP Streamable HTTP
  location ^~ /mcp {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  # Static-bearer SSE transport for MCP clients that require Server-Sent Events
  location ^~ /mcp-sse {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    chunked_transfer_encoding off;
    add_header X-Accel-Buffering no;
  }

  # OAuth MCP Streamable HTTP and OAuth protocol/discovery routes.
  # /oauth/mcp-sse is currently absent/unsupported; do not proxy it unless a future gate adds it.
  location = /oauth/mcp {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ^~ /.well-known/ {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ~ ^/(authorize|token|register|auth/callback|consent)$ {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}

# Static assets (separate domain)
server {
  listen 80;
  server_name static.example.com;

  root /data/paper-static;

  location / {
    add_header Access-Control-Allow-Origin *;
    add_header Access-Control-Allow-Methods "GET,HEAD,OPTIONS";
    add_header Access-Control-Allow-Headers "*";
    add_header Cache-Control "public, max-age=31536000, immutable";
    try_files $uri =404;
  }
}
```

---

## 3. Start the API Server

Set the `PAPER_DB_STATIC_BASE_URL` environment variable so the API can generate correct URLs pointing to your static CDN.

```bash
export PAPER_DB_STATIC_BASE_URL="https://static.example.com"
```

### 3.1 Basic Mode (No Advanced Search)

```bash
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

### 3.2 Advanced Search Mode

Enable semantic search by providing an embedding database and search config.

```bash
SEARCH_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --embed-db /data/paper_vectors \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

Or via `config.toml`:

```toml
[search]
advanced_enabled = true
vector_dir = "/data/paper_vectors"
access_token = "env:SEARCH_ACCESS_TOKEN"
```

Then:

```bash
SEARCH_ACCESS_TOKEN=your-token uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

### 3.3 API Endpoints

**BibTeX metadata**

```
GET /api/v1/papers/{paper_id}/bibtex
```

| Response field | Type   | Description                    |
|---------------|--------|--------------------------------|
| `paper_id`    | string | Paper ID                       |
| `doi`         | string | DOI (if available)             |
| `bibtex_raw`  | string | Raw BibTeX entry               |
| `bibtex_key`  | string | BibTeX citation key            |
| `entry_type`  | string | Entry type (article, inproceedings, etc.) |

Error responses: `paper_not_found`, `bibtex_not_found`

> For Admin API and MCP endpoint details, see [API and MCP](api-and-mcp.md).

---

## 4. Frontend

The frontend is a Vite-based static app.

```bash
cd frontend
npm install
npm run dev   # with env vars
npm run build # with env vars
```

**Environment variables:**

| Variable                    | Description                          |
|----------------------------|--------------------------------------|
| `VITE_PAPER_DB_API_BASE`   | Base URL of the API server           |
| `VITE_PAPER_DB_STATIC_BASE`| Base URL of the static asset CDN     |

After `npm run build`, deploy the `frontend/dist/` directory to any static host (Nginx, Caddy, Vercel, Netlify, etc.).

---

## 5. Docker

Pre-built images are available on both Docker Hub and GitHub Container Registry:

- `nerdneils/deepresearch-flow:latest` — CLI and API image
- `nerdneilsfield/deepresearch-flow:deploy-latest` — Deployment image (API + Nginx frontend)

### 5.1 Run the CLI

```bash
docker run --rm -v $(pwd):/app -it ghcr.io/nerdneilsfield/deepresearch-flow:latest --help
```

### 5.2 Deploy (API + Frontend via Nginx)

**Basic mode:**

```bash
docker run --rm -p 127.0.0.1:8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  -e MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

**Advanced search (embedded vectors):**

```bash
docker run --rm -p 127.0.0.1:8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  -v $(pwd)/paper_vectors:/db/paper_vectors \
  -v $(pwd)/config.toml:/app/config.toml:ro \
  -e PAPER_DB_EMBED_DB=/db/paper_vectors \
  -e PAPER_DB_CONFIG=/app/config.toml \
  -e SEARCH_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  -e MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

**External static assets (no local static mount):**

```bash
docker run --rm -p 127.0.0.1:8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -e PAPER_DB_STATIC_BASE=https://static.example.com \
  -e MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

### 5.3 Docker Compose

Four profiles are available to match different deployment scenarios.

```bash
export MCP_ACCESS_TOKEN="$(openssl rand -hex 32)"
export SEARCH_ACCESS_TOKEN="$(openssl rand -hex 32)"
docker compose -f scripts/docker/docker-compose.example.yml --profile local-static up
```

| Profile                     | Static Assets | Advanced Search |
|----------------------------|---------------|-----------------|
| `local-static`             | Local mount   | No              |
| `external-static`          | External URL  | No              |
| `local-static-advanced`    | Local mount   | Yes             |
| `external-static-advanced` | External URL  | Yes             |

**Runtime notes:**

- Nginx listens on `8899`, proxies `/api`, static-bearer MCP (`/mcp`, `/mcp-sse`), OAuth MCP (`/oauth/mcp`), and OAuth discovery/protocol routes (`/.well-known/`, `/authorize`, `/token`, `/register`, `/auth/callback`, `/consent`) to internal API at `127.0.0.1:8000`. OAuth SSE `/oauth/mcp-sse` is currently absent/unsupported.
- Mount snapshot DB to `/db/papers.db`, static assets to `/static`.
- `start-api.sh` auto-detects advanced mode by checking whether `PAPER_DB_EMBED_DB`, `PAPER_DB_CONFIG`, and `SEARCH_ACCESS_TOKEN` are set.
- Set `MCP_ACCESS_TOKEN` before deploying. `MCP_PUBLIC_UNSAFE=1` is intended for isolated local testing only.
- `PAPER_DB_NGINX_TEMPLATE=prefix` is only needed for sub-path deployments. The default (`root`) works for dedicated domains.
