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

Run this after the bootstrap workflow has generated repaired summary JSON,
`md_simple/`, and `md_base64_translated/`. No existing snapshot database is
required; the build host must be able to read the original PDF and Markdown
roots. The CDN only needs the exported directory.

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./summary_json/deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir /data/paper-static
```

Notes:

- Add one `--input ./summary_json/<template>.json` for each additional repaired
  summary template.
- `--pdf-root` should point to the directory containing original PDF files.
- `--md-root` / `--md-translated-root` should point to `md_simple/` and
  `md_base64_translated/`, respectively.
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

Advanced search supports `SEARCH_AUTH_MODE=static`, `github-oauth`, or `both` (default: `static`).
To offer GitHub sign-in in the browser while retaining bearer-token clients:

```bash
SEARCH_AUTH_MODE=both \
SEARCH_ACCESS_TOKEN=your-token \
MCP_PUBLIC_BASE_URL=https://papers.example.com \
GITHUB_OAUTH_CLIENT_ID=... \
GITHUB_OAUTH_CLIENT_SECRET=... \
MCP_GITHUB_ALLOWED_USER_IDS=12345678 \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --config ./config.toml \
  --host 0.0.0.0 --port 8001
```

The browser session lasts seven days and is stored in an HttpOnly cookie. Configure the reused
GitHub OAuth App callback as `https://papers.example.com/auth/callback`; the browser flow uses its
allowed subpath `/auth/callback/web`. GitHub users outside the numeric ID allowlist are rejected.

Deploy the frontend and API on the same origin when possible. For a cross-origin deployment,
the default CORS wildcard (`*`) does not permit credentialed browser requests and the OAuth session
cookie will not authenticate semantic-search fetches. Explicitly allow the frontend origin, for
example `--cors-origin https://frontend.example.com`, and keep frontend requests configured with
`credentials: include` (the bundled frontend already does this). `MCP_PUBLIC_BASE_URL` must be the
public HTTPS origin through which the browser reaches the API and OAuth callback.

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

## 6. Optional Admin PDF pipeline

The upload/review workflow is opt-in. Existing containers remain API + Nginx
only until both `config.toml` and the process bridge enable it:

```toml
[pipeline]
enabled = true
work_dir = "/data/pipeline-work/work"
preview_root = "/data/pipeline-work/previews"
queue_db = "/data/pipeline-work/queue.sqlite3"
snapshot_db = "/db/papers.db"
static_root = "/static"
formal_gc_batch_size = 100
formal_gc_grace_seconds = 86400
```

Use the commented `[pipeline]` example in `config.example.toml` for limits,
allowlisted OCR/Extract/Translate model keys, fixed extract templates,
translation language, lease/heartbeat, retry, retention, and supporting-model
fingerprints. A selected model must be in its corresponding allowlist. Only
nonsecret identifiers, paths, endpoints, and configuration fingerprints are
stored in the queue; provider keys and WebDAV passwords stay in environment
variables or the container secret mechanism.

Set all of the following when enabling the deployment image:

```bash
mkdir -p pipeline-work/work pipeline-work/previews pipeline-static
touch paper_snapshot.db
docker run --rm -p 127.0.0.1:8899:8899 \
  -e PAPER_DB_CONFIG=/app/config.toml \
  -e PAPER_OCR_CONFIG=/app/ocr.toml \
  -e PAPER_DB_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  -e PAPER_PIPELINE_ENABLED=1 \
  -e PAPER_DB_SNAPSHOT_DB=/db/papers.db \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  -v "$(pwd)/ocr.toml:/app/ocr.toml:ro" \
  -v "$(pwd)/pipeline-work:/data/pipeline-work" \
  -v "$(pwd)/pipeline-static:/static" \
  -v "$(pwd)/paper_snapshot.db:/db/papers.db" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

The same `PAPER_DB_CONFIG`, queue DB, work root, Snapshot DB, and formal static
root are used by API and Worker. `PAPER_PIPELINE_ENABLED=1` is only a
Supervisor materialization bridge: it cannot turn on a TOML-disabled pipeline,
and disagreement fails closed. `PAPER_DB_ADMIN_TOKEN` is required when TOML
enables the feature. Startup verifies that work, preview, queue, Snapshot, and
`/static` paths are absolute, writable, and (in Docker) below a non-root
mounted boundary. Create `work/` and `previews/` before startup; missing
durability mounts fail closed. Keep `pipeline-work` private; it contains
uploads, intermediates, and protected previews and is never an Nginx alias.
Protected previews are served only through authenticated Admin artifact routes,
never from `/static`; formal local output alone is written under `/static`.
On first enabled startup, registered legacy previews found under the historical
static root are validated and moved into `preview_root`; corrupt, outside, or
symlinked registrations fail closed before routes/Worker start. The migration
is idempotent across restart and scans only the exact UUID/preview artifact
shape for unregistered crash orphans; it never deletes unrelated static files.

Nginx accepts multipart uploads up to `500m` by default. Override with a
validated positive Nginx size such as `PAPER_DB_NGINX_BODY_LIMIT=1g`; the
protected Admin pipeline location has long upload/preview timeouts. The
frontend keeps the Admin token only in `sessionStorage` and validates it
against `/api/v1/admin/pipeline/config`.

Supervisor starts one durable Worker only when the bridge and TOML agree. Run
exactly one pipeline Worker process per queue deployment; the publication and
formal-GC serialization lock is in-process and assumes this topology. The
Worker polls processing and queued publication jobs, uses SQLite WAL leases,
writes idle and active heartbeats, recovers expired
`running`/`publishing`/`indexing` leases after restart, and stops between
processing step boundaries on `TERM`.
Supervisor classifies a crash before five seconds as a failed initial start
and bounds those starts to three retries. After startup, the Worker uses
`autorestart=false`: a runtime failure leaves it stopped, its heartbeat ages
out, and the Admin API/UI report `offline`; Supervisor does not enter an
unlimited crash loop. An operator must deliberately restart the Worker, after
which lease expiry/heartbeat recovery resumes queued work. A persistently
invalid configuration becomes a Supervisor startup failure requiring operator
action, not a successful data-state transition.
An in-flight remote processing call may finish; its completed checkpoint is
committed, the lease is atomically requeued, and no next Job is claimed.
An in-flight irreversible publication may finish, then publication polling
stops before claiming another Job.
The Admin config endpoint reports `online`, `degraded`, or `offline` from the
heartbeat age. Review each protected PDF/source/summary/translation, resolve
BibTeX ambiguity (or explicitly choose no BibTeX), then retry, reject, publish,
or batch-publish ready jobs. A publish action is revision-aware and returns a
conflict for stale UI data.

Terminal work and protected-preview artifacts for `published`,
`published_with_warning`, `rejected`, and `cancelled` jobs are retained for
seven days by default. Cleanup is batched and never
touches formal published resources, failed/review-ready work, or WebDAV
objects. A separate bounded reference-based formal GC may remove only
digest-verified, unreferenced objects older than `formal_gc_grace_seconds`;
unknown receipt/manifest metadata or unsupported WebDAV listing/deletion is
reported as a warning and causes no deletion. Only a manifest joined to a
Snapshot publication receipt authorizes liveness; a bounded cursor persists
across Worker restarts so referenced prefixes cannot starve later candidates.
To discard an unwanted terminal upload immediately, use the
authenticated reject/cancel action and run the bounded local cleanup task (or
remove only its UUID directories under both private `work/` and `previews/`
after confirming status in the Admin API); never delete the formal static root
by hand.

Publication writes immutable content-addressed files to the configured local
formal root or WebDAV, then commits a Snapshot publication receipt before
incremental LanceDB indexing. For WebDAV, the current bundle's
content-addressed resources are staged in a temporary private directory and
only that paper is loaded/upserted; the stage is removed after indexing.
WebDAV upload success is sufficient; no HEAD probe is required. A crash after
the receipt is safe to retry. If embedding fails, the paper remains published
as `published_with_warning`; retry indexing only after fixing vector
configuration. The queue keeps only a small publication manifest; formal
content-addressed resources remain in the local formal root (including a
local cache alongside WebDAV publication), so index-only retry still works
after seven-day private work/preview cleanup. The Worker runs bounded,
reference-based GC against both local and capable WebDAV stores; it never
traverses private roots or removes objects referenced by current Snapshot,
publication receipts, or manifests. Legacy `published_with_warning` rows that
lack an exact manifest/cache fail with an actionable 409 instead of entering a
doomed retry.

To disable or roll back, stop the container, set `PAPER_PIPELINE_ENABLED` unset
or `0`, and use a config with `[pipeline].enabled = false`. Existing public
search/API routes and CLI Snapshot behavior remain unchanged. Keep the queue
and work volumes when recovering; restart Worker with the same paths so lease
expiry and heartbeat recovery can proceed.
