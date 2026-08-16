[← 返回 README](../README_ZH.md)

# 部署指南

生产环境推荐**前后端分离**的架构：

- **静态 CDN** 托管 PDF、Markdown、图片和摘要文件。
- **API 服务** 提供只读快照数据库的查询接口。
- **前端** 是独立的静态应用（Vite 构建产物，也可以放在任意静态托管服务上）。

<p align="center">
  <img src="../../.github/assets/frontend.png" width="80%" alt="frontend" />
</p>

## 环境要求

- Python 3.10+，搭配 `uv`
- Node.js 18+（构建前端用）
- Caddy 或 Nginx（反向代理 / 静态文件服务）
- Docker（可选，容器化部署时使用）

---

## 1. 构建快照与静态资源导出

在从零流程已生成并修复 summary JSON、`md_simple/` 与
`md_base64_translated/` 后执行此步。不需要已有 snapshot 数据库；构建机器需要能读取
原始 PDF 和 Markdown 根目录。CDN 端只需要拿到导出后的静态目录。

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

说明：

- 每增加一份已修复的摘要模板，就增加一个
  `--input ./summary_json/<template>.json`。
- `--pdf-root` 指向存放原始 PDF 文件的目录。
- `--md-root` / `--md-translated-root` 分别指向 `md_simple/` 与
  `md_base64_translated/`。
- `--static-export-dir` 是静态资源的输出目录。把这个目录复制或挂载到 CDN 上即可。

---

## 2. 托管静态资源

静态资源需要配置 CORS 头以及长期缓存头，这样前端才能跨域加载。

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

### 2.2 Nginx（API + 前端同域，静态资源独立域名）

```nginx
# 前端 + API（同一域名）
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

  # Static-bearer SSE 传输，给需要 Server-Sent Events 的 MCP 客户端使用
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

  # OAuth MCP Streamable HTTP 以及 OAuth 协议/发现路由。
  # /oauth/mcp-sse 当前缺失/不支持；除非未来 gate 加入，否则不要代理它。
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

# 静态资源（独立域名）
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

## 3. 启动 API 服务

设置 `PAPER_DB_STATIC_BASE_URL` 环境变量，API 才能生成指向静态 CDN 的正确 URL。

```bash
export PAPER_DB_STATIC_BASE_URL="https://static.example.com"
```

### 3.1 基础模式（不启用高级搜索）

```bash
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

### 3.2 高级搜索模式

传入向量数据库和搜索配置，即可开启语义搜索。

```bash
SEARCH_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --embed-db /data/paper_vectors \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

也可以用 `config.toml` 来管理配置：

```toml
[search]
advanced_enabled = true
vector_dir = "/data/paper_vectors"
access_token = "env:SEARCH_ACCESS_TOKEN"
```

然后启动：

```bash
SEARCH_ACCESS_TOKEN=your-token uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

高级搜索支持 `SEARCH_AUTH_MODE=static`、`github-oauth`、`both`，默认 `static`。若要让浏览器
使用 GitHub 登录，同时保留 bearer token 客户端：

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

浏览器会话保存于 HttpOnly cookie，期限七日。复用的 GitHub OAuth App callback 配为
`https://papers.example.com/auth/callback`；浏览器流程使用其允许的子路径
`/auth/callback/web`。不在数字 ID 白名单内的 GitHub 用户会被拒绝。

应尽量让前端与 API 同源部署。若必须跨源部署，默认 CORS 通配符（`*`）不允许携带凭据的
浏览器请求，OAuth session cookie 因而不能为语义搜索 fetch 鉴权。须显式允许前端 origin，
例如 `--cors-origin https://frontend.example.com`，并让前端请求保持 `credentials: include`
（仓库自带前端已如此配置）。`MCP_PUBLIC_BASE_URL` 必须是浏览器实际访问 API 与 OAuth
callback 的公开 HTTPS origin。

### 3.3 API 接口

**BibTeX 元数据**

```
GET /api/v1/papers/{paper_id}/bibtex
```

| 返回字段       | 类型   | 说明                            |
|---------------|--------|--------------------------------|
| `paper_id`    | string | 论文 ID                         |
| `doi`         | string | DOI（如有）                      |
| `bibtex_raw`  | string | 原始 BibTeX 条目                 |
| `bibtex_key`  | string | BibTeX 引用键                    |
| `entry_type`  | string | 条目类型（article、inproceedings 等） |

错误响应：`paper_not_found`、`bibtex_not_found`

> Admin API 和 MCP 端点的详细信息见 [API 与 MCP](api-and-mcp.md)。

---

## 4. 前端

前端是 Vite 构建的静态应用。

```bash
cd frontend
npm install
npm run dev   # 开发模式，需要设置环境变量
npm run build # 生产构建，需要设置环境变量
```

**环境变量：**

| 变量                         | 说明                      |
|-----------------------------|--------------------------|
| `VITE_PAPER_DB_API_BASE`    | API 服务的基础 URL         |
| `VITE_PAPER_DB_STATIC_BASE` | 静态资源 CDN 的基础 URL    |

执行 `npm run build` 后，把 `frontend/dist/` 目录部署到任意静态托管服务即可（Nginx、Caddy、Vercel、Netlify 等）。

---

## 5. Docker

预构建镜像发布在 Docker Hub 和 GitHub Container Registry：

- `nerdneils/deepresearch-flow:latest` — CLI 和 API 镜像
- `nerdneilsfield/deepresearch-flow:deploy-latest` — 部署镜像（API + Nginx 前端）

### 5.1 运行 CLI

```bash
docker run --rm -v $(pwd):/app -it ghcr.io/nerdneilsfield/deepresearch-flow:latest --help
```

### 5.2 部署（API + 前端，通过 Nginx 提供服务）

**基础模式：**

```bash
docker run --rm -p 127.0.0.1:8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  -e MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

**高级搜索（内嵌向量）：**

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

**外部静态资源（不挂载本地静态目录）：**

```bash
docker run --rm -p 127.0.0.1:8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -e PAPER_DB_STATIC_BASE=https://static.example.com \
  -e MCP_ACCESS_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

### 5.3 Docker Compose

提供了四种 profile，对应不同的部署场景。

```bash
export MCP_ACCESS_TOKEN="$(openssl rand -hex 32)"
export SEARCH_ACCESS_TOKEN="$(openssl rand -hex 32)"
docker compose -f scripts/docker/docker-compose.example.yml --profile local-static up
```

| Profile                      | 静态资源    | 高级搜索 |
|-----------------------------|-----------|---------|
| `local-static`              | 本地挂载    | 否      |
| `external-static`           | 外部 URL   | 否      |
| `local-static-advanced`     | 本地挂载    | 是      |
| `external-static-advanced`  | 外部 URL   | 是      |

**运行时注意事项：**

- Nginx 监听 `8899` 端口，将 `/api`、static-bearer MCP（`/mcp`、`/mcp-sse`）、OAuth MCP（`/oauth/mcp`）以及 OAuth discovery/protocol 路由（`/.well-known/`、`/authorize`、`/token`、`/register`、`/auth/callback`、`/consent`）反向代理到内部 API（`127.0.0.1:8000`）。OAuth SSE `/oauth/mcp-sse` 当前缺失/不支持。
- 快照数据库挂载到 `/db/papers.db`，静态资源挂载到 `/static`。
- `start-api.sh` 会自动检测是否设置了 `PAPER_DB_EMBED_DB`、`PAPER_DB_CONFIG` 和 `SEARCH_ACCESS_TOKEN`，以此判断是否启用高级搜索模式。
- 部署前务必设置 `MCP_ACCESS_TOKEN`。`MCP_PUBLIC_UNSAFE=1` 仅用于隔离的本地测试环境。
- `PAPER_DB_NGINX_TEMPLATE=prefix` 只在子路径部署时需要。默认值（`root`）适用于独立域名部署。
