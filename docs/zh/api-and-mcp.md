[← 返回 README](../README_ZH.md)

# API 与 MCP

## Admin API

启用 Admin API 后，可以通过 Bearer token 认证远程添加或删除论文。

```bash
PAPER_DB_ADMIN_TOKEN=your-secret-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

也可以通过命令行传 token：`--admin-token your-secret-token`

### 接口

所有接口均需要 `Authorization: Bearer <token>`。

#### `POST /api/v1/admin/papers`

批量添加论文（单次最多 200 条）。

返回：`{ added, skipped, errors, paper_ids }`

```bash
curl -X POST https://api.example.com/api/v1/admin/papers \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"papers": [{"paper_title": "...", "paper_authors": [...], ...}]}'
```

#### `DELETE /api/v1/admin/papers/{paper_id}`

删除一篇论文及其所有关联数据。

返回：`{ deleted: true, paper_id }`

```bash
curl -X DELETE https://api.example.com/api/v1/admin/papers/{paper_id} \
  -H "Authorization: Bearer your-secret-token"
```

---

## 从本地数据库推送到远程

使用 `api push` 将本地构建的 snapshot 数据库合并到远程部署中。

### 配置

```toml
# remote.toml
[remote]
api_base_url = "https://api.example.com"
admin_token = "env:PAPER_DB_ADMIN_TOKEN"
batch_size = 10

[remote.semantic]
max_rows = 25
max_payload_bytes = 4000000
timeout = 120
retries = 3
retry_backoff_seconds = 2

[remote.storage]
type = "webdav"
url = "https://cdn.example.com/paper-static"
username = "deploy"
password = "env:PAPER_DB_WEBDAV_PASSWORD"
```

### 命令

```bash
# 预览（不实际推送）
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --dry-run

# 推送到远程
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml

# 推送元数据 + semantic LanceDB 分块
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# 仅推送 API 元数据
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-api

# 仅推送静态存储资源
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml --only-storage --storage-concurrency 8

# 重试失败的静态文件
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --retry-failed push-static-errors.json

# 按论文索引切片推送
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml --only-api \
  --start-idx 100 --end-idx 200
```

### 注意事项

- `--static-export-dir` 可选——提供后会包含 summary JSON，用于全文搜索和预览
- `--embed-db` 可选——在元数据之后推送 semantic 分块
- 重复论文会自动跳过
- 存储后端：`webdav`
- 重试文件：`push-static-errors.json` / `push-semantic-errors.json`
- `--only-api` 与 `--only-storage` 互斥
- `--dry-run` 会跳过 semantic 推送

### 仅推送 Semantic

```bash
# 推送所有 semantic 分组
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# 重试失败的 semantic 批次
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --retry-failed push-semantic-errors.json

# 按分块窗口推送（0 起始，end 不包含，-1 表示到末尾）
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --start-chunk-idx 1000 --end-chunk-idx 2000
```

说明：分块窗口会自动扩展到完整的 `(doc_id, template_tag)` 分组。`--retry-failed` 仅用于 semantic 重试。不能与 `--start-chunk-idx/--end-chunk-idx` 同时使用。

---

## MCP（FastMCP Streamable HTTP + SSE）

### 概览

项目通过 FastMCP 暴露 MCP 工具，供 AI agent 调用。推荐的 public surface 是有边界的工具工作流：先发现 metadata、outline 或 summary keys，再读取具体行窗口或具体 summary 值。

**端点：**

- Static bearer Streamable HTTP：`http://<host>:8001/mcp`
- Static bearer SSE：`http://<host>:8001/mcp-sse`
- GitHub OAuth Streamable HTTP：`https://<public-host>/oauth/mcp`
- OAuth SSE：当前缺失/不支持。除非未来 gate 明确加入，否则不要配置 `/oauth/mcp-sse`。
- OAuth discovery/protocol 路由：`/.well-known/`、`/.well-known/oauth-protected-resource/oauth/mcp`、`/authorize`、`/token`、`/register`、`/auth/callback`、`/consent`
- 可选协议头：`mcp-protocol-version`（`2025-03-26` 或 `2025-06-18`）

**静态读取优先级：** `PAPER_DB_STATIC_EXPORT_DIR` → `PAPER_DB_STATIC_BASE` / `PAPER_DB_STATIC_BASE_URL`

Full-content MCP 读取和全文 paper URI resources 是旧的/已移除/归档的 public-surface 模式。不要再为 agent workflow 推荐 `get_paper_summary`、`get_paper_source`、旧的 source/translation line 工具或全文 `paper://...` resources；请使用下面的有边界工具。小型 `paper://{paper_id}/metadata` resource 可为兼容保留，但 agent 应优先使用 `get_paper_metadata`。

不同部署模式下的端点行为：

| Endpoint | Static bearer 模式 | GitHub OAuth 模式 |
| --- | --- | --- |
| `/mcp` | Streamable HTTP，使用 `Authorization: Bearer <MCP_ACCESS_TOKEN>` | 仍然只接受 static bearer |
| `/mcp-sse` | SSE，使用 `Authorization: Bearer <MCP_ACCESS_TOKEN>` | 仍然只接受 static bearer |
| `/oauth/mcp` | 不使用 | GitHub OAuth 的 Streamable HTTP |
| `/oauth/mcp-sse` | 不使用 | 当前不支持/不存在 |
| OAuth protocol 路由 | 不使用 | 用于 discovery、registration、authorization、token exchange、callback 和 consent |

普通 CLI agent 和自动化脚本使用 static bearer。ChatGPT/Claude 这类需要交互式 OAuth 的托管客户端使用 GitHub OAuth。不要把 static bearer token 发到 `/oauth/mcp`；该端点会有意拒绝它。

### 鉴权模式

#### Static Bearer

使用 `--mcp-access-token` 或 `MCP_ACCESS_TOKEN`；请求头为 `Authorization: Bearer <token>`。对 `/mcp` 和 `/mcp-sse` 均生效。

CLI 示例：

```bash
MCP_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db papers.db \
  --mcp-access-token "$MCP_ACCESS_TOKEN"
```

客户端 URL：Streamable HTTP 使用 `https://<public-host>/mcp`，SSE 使用 `https://<public-host>/mcp-sse`。

#### GitHub OAuth

设置 `MCP_AUTH_MODE=github-oauth`。OAuth 客户端使用 `/oauth/mcp` 的 Streamable HTTP；此端点会拒绝 static bearer token。`/mcp` 和 `/mcp-sse` 仍是 static bearer 端点。

必需环境变量：

- `MCP_AUTH_MODE=github-oauth`
- `MCP_PUBLIC_BASE_URL`（只填 origin，例如 `https://papers.example.com`；不要包含 `/oauth/mcp`）
- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `MCP_GITHUB_ALLOWED_USER_IDS`（GitHub 用户数字 ID，必填）
- `MCP_ACCESS_TOKEN`（仍用于 static bearer `/mcp` 和 `/mcp-sse`）

OAuth 客户端配置摘要：

1. 将 MCP client URL 配为 `https://<public-host>/oauth/mcp`。
2. 让客户端从 `https://<public-host>/.well-known/oauth-protected-resource/oauth/mcp` 发现 protected-resource metadata。
3. 通过服务器路由完成 GitHub OAuth：`/authorize`、`/token`、`/register`、`/auth/callback`、`/consent`。
4. 不要使用 `/oauth/mcp-sse`；当前 OAuth SSE gate 结果是缺失/不支持。

> **注意：** MCP token、advanced-search token、admin token 和 GitHub OAuth 凭据是相互独立的凭据。

##### 创建 GitHub OAuth App

这里要创建的是 **GitHub OAuth App**，不是 GitHub App。GitHub OAuth App 只作为上游身份提供方；对 ChatGPT、Claude 和其他 MCP client 来说，drflow 仍然是 MCP authorization/resource server。

1. 用应该拥有该 OAuth App 的个人账号或组织账号登录 GitHub。
2. 打开 **Settings → Developer settings → OAuth Apps**。
   - 个人账号：点击头像 → **Settings** → **Developer settings** → **OAuth Apps**。
   - 组织拥有的 App：进入组织 → **Settings** → **Developer settings** → **OAuth Apps**。
3. 点击 **New OAuth App**。
4. 填写：

   | GitHub 字段 | 填写值 |
   | --- | --- |
   | Application name | 任意清晰名称，例如 `drflow-registration-mcp` |
   | Homepage URL | `https://<public-host>` |
   | Application description | 可选 |
   | Authorization callback URL | `https://<public-host>/auth/callback` |

   例如，如果 `MCP_PUBLIC_BASE_URL=https://drflow.example.com`，则填写：

   ```text
   Homepage URL: https://drflow.example.com
   Authorization callback URL: https://drflow.example.com/auth/callback
   ```

   不要把 GitHub callback URL 填成 `/oauth/mcp`。`/oauth/mcp` 是 MCP client 连接的端点；`/auth/callback` 才是 GitHub 登录完成后浏览器回跳到 drflow 的地址。

5. 点击 **Register application**。
6. 复制 **Client ID**，填入 `GITHUB_OAUTH_CLIENT_ID`。
7. 点击 **Generate a new client secret**，复制生成的 secret，填入 `GITHUB_OAUTH_CLIENT_SECRET`。这个值通常只显示一次，按密钥处理，不要提交到仓库。
8. 找到允许访问 MCP 的 GitHub 数字用户 ID，填入 `MCP_GITHUB_ALLOWED_USER_IDS`。

   这个值是 GitHub API 返回的稳定数字 `id` 字段。它**不是** GitHub username、显示名、邮箱，也不是 OAuth client ID。如果这个变量为空，或者填成了 username，drflow 会在启动时报错：

   ```text
   MCP_GITHUB_ALLOWED_USER_IDS must contain numeric GitHub user IDs
   ```

   查询当前 GitHub CLI 登录用户：

   ```bash
   gh api user --jq .id
   ```

   查询指定用户名：

   ```bash
   gh api users/<github-username> --jq .id
   # 或：
   curl -s https://api.github.com/users/<github-username> | jq -r .id
   ```

   示例：

   ```bash
   gh api users/octocat --jq .id
   ```

   多个用户用英文逗号分隔：

   ```env
   MCP_GITHUB_ALLOWED_USER_IDS=12345678,87654321
   ```

   不要加引号，也不要加空格：

   ```env
   # 正确：
   MCP_GITHUB_ALLOWED_USER_IDS=12345678,87654321

   # 错误：
   MCP_GITHUB_ALLOWED_USER_IDS=alice,bob
   MCP_GITHUB_ALLOWED_USER_IDS="12345678, 87654321"
   ```

9. 配置 drflow：

   ```env
   MCP_AUTH_MODE=github-oauth
   MCP_PUBLIC_BASE_URL=https://<public-host>
   GITHUB_OAUTH_CLIENT_ID=...
   GITHUB_OAUTH_CLIENT_SECRET=...
   MCP_GITHUB_ALLOWED_USER_IDS=12345678

   # /mcp 和 /mcp-sse 的 static bearer 端点仍然需要：
   MCP_ACCESS_TOKEN=...
   ```

10. 确认反向代理把这些 OAuth 路由转发到 drflow：

    - `/oauth/mcp`
    - `/.well-known/`
    - `/authorize`
    - `/token`
    - `/register`
    - `/auth/callback`
    - `/consent`

11. 配置 MCP 客户端：

    - ChatGPT/Claude OAuth 端点：`https://<public-host>/oauth/mcp`
    - 普通 CLI/static bearer 端点：`https://<public-host>/mcp`，并带上 `Authorization: Bearer <MCP_ACCESS_TOKEN>`
    - 需要 SSE 时使用 static bearer 端点：`https://<public-host>/mcp-sse`

如果 GitHub 报 callback mismatch，优先检查 GitHub OAuth App 里的 callback URL。它必须和外部可访问的 drflow callback URL 完全一致，包括 scheme、host 和 path。如果 staging/production 使用不同 public host，建议创建两个 GitHub OAuth App，让每个环境有独立的 callback URL 和 client secret。

运维注意事项：

- `MCP_PUBLIC_BASE_URL` 必须是外部可访问的 HTTPS origin，不要追加 `/mcp` 或 `/oauth/mcp`。
- `MCP_GITHUB_ALLOWED_USER_IDS` 使用稳定的 GitHub 数字用户 ID，不使用用户名/显示名。这样 drflow 仍然是单库/单租户，只是限制哪些 GitHub 身份可以访问 MCP。
- GitHub OAuth App callback 需要匹配本部署暴露的 OAuth callback 路由，目前是同一 public base URL 下的 `/auth/callback`。
- 测试 OAuth 时，如果客户端保留了旧的 `Authorization: Bearer ...` 请求头，先移除该请求头再开始 OAuth flow。

### 推荐 Agent Workflow

1. 使用 `search_papers`、`search_papers_by_keyword` 或 `search_papers_semantic` 搜索。
2. 调用 `get_paper_metadata` 发现可用性字段。
3. 对 source 或 translation markdown，先调用 `get_paper_content_outline`，再调用 `get_paper_content_window`。
4. 对 summary，先调用 `get_paper_summary_keys`，再对精确 key 调用 `get_paper_summary_value`。

### MCP 工具

<details>
<summary><code>search_papers(query, limit=10) → list[dict]</code></summary>

全文搜索论文（按相关性排序）。当你只有主题关键词时使用此工具。

返回：`paper_id`、`title`、`year`、`venue`、`snippet_markdown`。
</details>

<details>
<summary><code>search_papers_by_keyword(keyword, limit=10) → list[dict]</code></summary>

按关键词/标签搜索论文（精确匹配）。当你知道具体关键词或标签时使用此工具。

返回：`paper_id`、`title`、`year`、`venue`、`snippet_markdown`。
</details>

<details>
<summary><code>search_papers_semantic(query, top_n=10, mmr_lambda=None, rerank="auto", filters=None) → dict</code></summary>

运行完整的 advanced semantic search 流程并返回结果。需要 MCP 服务器配置了 advanced search。

- `mmr_lambda`：MMR 多样性参数（`None` 时使用服务器默认值）
- `rerank`：重排序模式（默认为 `"auto"`）
- `filters`：可选的结构化过滤参数
</details>

<details>
<summary><code>get_paper_metadata(paper_id) → dict</code></summary>

获取论文元数据和可用性标记。在读取 source、translation、summary 或 BibTeX 前先调用它。

返回：`paper_id`、`title`、`year`、`venue`、`doi`、`arxiv_id`、`openreview_id`、`paper_pw_url`、`preferred_summary_template`、`has_source`、`available_translations`、`available_summary_templates`、`has_bibtex`。

可用性字段：

- `has_source`：source markdown 可通过 content window 工具读取。
- `available_translations`：`content_type="translation"` 时 content 工具接受的规范化语言标签。
- `available_summary_templates`：summary 工具接受的模板标签。
- `has_bibtex`：可通过 `get_paper_bibtex` 读取 BibTeX。
</details>

<details>
<summary><code>get_paper_bibtex(paper_id) → dict</code></summary>

获取论文的 BibTeX 数据。包含论文元数据中的规范 DOI 和 BibTeX 条目文本。

返回：`paper_id`、`doi`、`bibtex_raw`、`bibtex_key`、`entry_type`。
</details>

<details>
<summary><code>get_paper_content_outline(paper_id, content_type, lang=None, max_sections=200) → dict</code></summary>

获取 source 或 translated markdown 的有边界 heading outline。

- `content_type`：`"source"` 或 `"translation"`
- `lang`：translation 必填，source 省略；使用 `available_translations` 中的标签
- `max_sections`：默认 `200`，服务器会设置硬上限

返回：`paper_id`、`content_type`、`lang`、`total_lines`、带 heading 层级和行范围的 `sections`。
</details>

<details>
<summary><code>get_paper_content_window(..., mode="head", line_count=80, ...) → dict</code></summary>

获取 source 或 translated markdown 的有边界行窗口。

- 常用参数：`paper_id`、`content_type`、可选 `lang`
- 模式：`head`、`tail`、`head_tail`、`around`、`range`
- `head`：前 `line_count` 行
- `tail`：后 `line_count` 行
- `head_tail`：前 `head_lines` 行加后 `tail_lines` 行
- `around`：以 `center_line` 为中心，包含 `before_lines` 和 `after_lines`
- `range`：从 `start_line` 到 `end_line` 的闭区间

返回：`paper_id`、`content_type`、`lang`、行边界、`total_lines`、`content` 以及截断/窗口 metadata。

行号从 1 开始，且首尾都包含。显式 range 必须合法；需要 section 边界时先调用 `outline`。响应受服务器行数/字符数上限约束，请根据返回的截断 metadata 判断窗口是否被裁剪。
</details>

<details>
<summary><code>get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False, max_paths=200) → dict</code></summary>

按文档顺序获取递归的 summary 键路径。

- `template`：省略时使用首选模板
- `max_depth`：最大嵌套深度（默认 `2`）
- `include_preview`：设为 `True` 时包含有边界的预览值
- `max_paths`：最多返回的路径数

返回：`paper_id`、`template`、`paths`、`total_paths`、`returned_paths`、`truncated`。

Key 使用类似 JSON 的 dot/index 路径，例如 `contribution.main` 或 `experiments[0].result`。应先发现 key，再读取具体值，避免下载完整 summary JSON。
</details>

<details>
<summary><code>get_paper_summary_value(paper_id, key, template=None, max_chars=4000, include_subtree=False) → dict</code></summary>

读取一个指定 summary 值。对 object 或 array 节点，默认返回 child-key metadata，而不是大 subtree；只有需要时才设置 `include_subtree=True`。

返回：`paper_id`、`template`、`key`、`value_type`、`content_format`、`content`、适用时的 child-key metadata，以及 `truncated`。

长 summary 字符串受 `max_chars` 前缀上限约束。读取中段/尾部需要未来的 `get_paper_summary_window` 工具。
</details>

<details>
<summary><code>get_database_stats() → dict</code></summary>

获取数据库统计信息。返回总数、按年/月的分布，以及 top facets（作者、会议/期刊、关键词、机构、标签）。
</details>

<details>
<summary><code>list_top_facets(category, limit=20) → list[dict]</code></summary>

列出指定 facet 的 top 值。

- `category`：`author`、`venue`、`keyword`、`institution`、`tag` 之一

返回：`[{ value, paper_count }]`。
</details>

<details>
<summary><code>filter_papers(author=None, venue=None, year=None, keyword=None, tag=None, limit=10) → list[dict]</code></summary>

按结构化字段筛选论文。适用于按作者、会议/期刊、年份、关键词或标签进行精确筛选。

返回：`paper_id`、`title`、`year`、`venue`。
</details>

### 示例

读取 source 前 80 行：

```json
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"source","mode":"head","line_count":80}}
```

先找 section，再读取某一行附近内容：

```json
{"tool":"get_paper_content_outline","arguments":{"paper_id":"paper-123","content_type":"source"}}
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"source","mode":"around","center_line":245,"before_lines":30,"after_lines":50}}
```

读取中文 translation 尾部：

```json
{"tool":"get_paper_content_window","arguments":{"paper_id":"paper-123","content_type":"translation","lang":"zh","mode":"tail","line_count":80}}
```

发现 summary keys，再读取一个值：

```json
{"tool":"get_paper_summary_keys","arguments":{"paper_id":"paper-123","template":"deep_read","max_depth":3,"include_preview":true}}
{"tool":"get_paper_summary_value","arguments":{"paper_id":"paper-123","template":"deep_read","key":"experiments.main_result","max_chars":4000}}
```

### 已归档 MCP resources

`paper://{paper_id}/summary`、`paper://{paper_id}/summary/{template}`、`paper://{paper_id}/source` 和 `paper://{paper_id}/translation/{lang}` 是历史 full-content resource 模式。它们不是当前 agent 推荐的 MCP public surface。请优先使用有边界工具：`get_paper_content_outline`、`get_paper_content_window`、`get_paper_summary_keys` 和 `get_paper_summary_value`。
