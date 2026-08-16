<p align="center">
  <img src=".github/assets/logo.png" width="140" alt="ai-deepresearch-flow logo" />
</p>

<h3 align="center">ai-deepresearch-flow</h3>

<p align="center">
  <em>从文档到深度研究洞见，自动化完成。</em>
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_ZH.md">中文</a>
</p>

<p align="center">
  <a href="https://github.com/nerdneilsfield/ai-deepresearch-flow/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/nerdneilsfield/ai-deepresearch-flow/push-to-pypi.yml?style=flat-square" />
  </a>
  <a href="https://pypi.org/project/deepresearch-flow/">
    <img src="https://img.shields.io/pypi/v/deepresearch-flow?style=flat-square" />
  </a>
  <a href="https://pypi.org/project/deepresearch-flow/">
    <img src="https://img.shields.io/pypi/pyversions/deepresearch-flow?style=flat-square" />
  </a>
  <a href="https://hub.docker.com/r/nerdneils/deepresearch-flow">
    <img src="https://img.shields.io/docker/v/nerdneils/deepresearch-flow?style=flat-square" />
  </a>
  <a href="https://github.com/nerdneilsfield/ai-deepresearch-flow/pkgs/container/deepresearch-flow">
    <img src="https://img.shields.io/badge/ghcr.io-nerdneilsfield%2Fdeepresearch-flow-0f172a?style=flat-square" />
  </a>
  <a href="https://github.com/nerdneilsfield/ai-deepresearch-flow/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/nerdneilsfield/ai-deepresearch-flow?style=flat-square" />
  </a>
  <a href="https://github.com/nerdneilsfield/ai-deepresearch-flow/stargazers">
    <img src="https://img.shields.io/github/stars/nerdneilsfield/ai-deepresearch-flow?style=flat-square" />
  </a>
  <a href="https://pypi.org/project/deepresearch-flow">
    <img alt="PyPI - Version" src="https://img.shields.io/pypi/v/deepresearch-flow">
  </a>
  <a href="https://github.com/nerdneilsfield/ai-deepresearch-flow/issues">
    <img src="https://img.shields.io/github/issues/nerdneilsfield/ai-deepresearch-flow?style=flat-square" />
  </a>
</p>

---

## 要解决的问题

- **OCR 混乱**：OCR 产出的 Markdown 经常错乱，表格漂移、公式断裂、引用不可点击。
- **翻译灾难**：翻译技术论文时，代码块、LaTeX、表格结构很容易被破坏。
- **信息过载**：从海量 PDF 中提取作者、会议、摘要等结构化信息十分耗时。
- **频繁切换**：PDF、摘要、翻译分散在多个窗口，阅读体验割裂。

## 解决方案

DeepResearch Flow 提供一条完整流水线，覆盖 **修复**、**翻译**、**抽取** 与 **浏览服务**。

## 关键特性

- **智能抽取** — 用 LLM 将非结构化 Markdown 转为结构化 JSON（摘要、元数据、问答）。
- **精准翻译** — 翻译 OCR Markdown 到中文/日文，同时冻结公式、代码、表格与引用。
- **本地知识库** — Web UI 支持 Split View（原文/翻译/摘要）、全文搜索、多维过滤。
- **Snapshot + API 服务** — 生产级 SQLite 快照 + 静态资源 + 只读 JSON API。
- **OCR 后处理** — 自动修复引用、合并断段、修复 LaTeX 和 Mermaid 图。
- **语义搜索** — LanceDB 向量检索，支持混合召回和云端重排序。
- **MCP 集成** — FastMCP 服务，支持 AI Agent 通过有边界读取工具访问，static-bearer Streamable HTTP/SSE，以及 `/oauth/mcp` 上的 GitHub OAuth。

---

## 快速开始

### 1) 安装

```bash
uv pip install deepresearch-flow
# 或: pip install deepresearch-flow
```

### 2) 配置

```bash
cp config.example.toml config.toml
```

最小配置（加权多 provider 路由）：

```toml
main_model = [
  { model = "openai/gpt-4o-mini", weight = 4 },
  { model = "claude/claude-sonnet-4-5-20250929", weight = 1 }
]

[[providers]]
name = "openai"
type = "openai_compatible"
base = [
  { url = "https://api.openai.com/v1", weight = 1, key = [
    { value = "env:OPENAI_API_KEY", weight = 4 }
  ] }
]
models = [
  { model_name = "gpt-4o-mini", is_support_json_schema = true }
]

[[providers]]
name = "claude"
type = "claude"
base = [
  { url = "https://api.anthropic.com", weight = 1, key = [
    { value = "env:ANTHROPIC_API_KEY", weight = 1 }
  ] }
]
models = [
  { model_name = "claude-sonnet-4-5-20250929" }
]
```

密钥使用 `env:VAR_NAME` 语法，避免明文写入配置文件。支持 Ollama、Gemini、DashScope、Azure OpenAI 等多种 provider。完整配置选项（embedding、rerank、翻译器默认值、搜索）见 `config.example.toml`。

### 3) 从零到一的流程

从 `./pdfs/` 和可选的 `./papers.bib` 开始。不需要已有的 JSON 文献库、SQLite 数据库或处理过的 Markdown 目录。

本流程会生成以下目录和文件：

```text
pdfs/ + papers.bib
  → ocr_output/
  → md_simple/            # 图片保留为本地文件
  → md_base64/            # 图片内嵌为 data URL
  ├─ summary_json/<template>.json
  └─ md_base64_translated/
```

#### 步骤 1：对 PDF/图片执行 OCR

先复制并填写 OCR 配置：

```bash
cp ocr.example.toml ocr.toml
# 设置: export PADDLE_OCR_TOKEN=xxx
# 示例使用 PaddleOCR-VL-1.6 的异步 Job API。
# 如有需要，可在 ocr.toml 中调整 poll_interval_seconds 和 job_timeout_seconds。

uv run deepresearch-flow recognize ocr ./pdfs \
  --config ocr.toml \
  --output-dir ./ocr_output
# 默认最多并发处理 4 个文件；可用 --workers 2 覆盖。
```

后端会写出兼容 MinerU 的布局：每篇文档有一个 `full.md` 和
`images/` 目录。超时只会停止本地轮询，不会取消远端 PaddleOCR 任务。

#### 步骤 2：修复嵌套的 OCR 产物

每篇 OCR 文档都嵌套在 `ocr_output/` 下，两个修复命令都必须带 `-r`：

```bash
# 修复所有 OCR 文档的 Markdown 结构
uv run deepresearch-flow recognize fix \
  --input ./ocr_output -r --in-place

# 修复所有 OCR 文档的 LaTeX 公式
uv run deepresearch-flow recognize fix-math \
  --input ./ocr_output -r \
  --model openai/gpt-4o-mini \
  --in-place
```

<p align="center">
  <img src=".github/assets/fix.png" width="70%" alt="fix" />
</p>

<p align="center">
  <img src=".github/assets/fix-math.png" width="70%" alt="fix math" />
</p>

#### 步骤 3：整理源 Markdown

用一条命令同时生成两种源文件表示。为发现嵌套 OCR 布局，`organize`
也必须带 `-r`。不要传 `--fix`，因为步骤 2 已经修复 OCR 源文件。

```bash
uv run deepresearch-flow recognize organize \
  --input ./ocr_output -r \
  --output-simple ./md_simple \
  --output-base64 ./md_base64
```

`md_simple/` 会将图片保留在 `md_simple/images/`；`md_base64/`
会内嵌图片，故它是翻译输入。

#### 步骤 4：生成结构化摘要

每个选用的 prompt template 生成一个 JSON 包。以下示例使用
`deep_read`；其他模板也重复此操作，并将输出命名为
`./summary_json/<template>.json`。

```bash
uv run deepresearch-flow paper extract \
  --input ./md_simple \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --output ./summary_json/deep_read.json
```

<p align="center">
  <img src=".github/assets/extract.png" width="70%" alt="extract" />
</p>

#### 步骤 4.1：校验并重试摘要字段

校验报告放在 `summary_json/` 外，避免 JSON 修复扫描到报告文件。每个选用的
template 都重复这一组命令。这里的 `paper db verify` 只校验 JSON 包，
不需要数据库。

```bash
uv run deepresearch-flow paper db verify \
  --input-json ./summary_json/deep_read.json \
  --prompt-template deep_read \
  --output-json ./summary_verify/deep_read.json

uv run deepresearch-flow paper extract \
  --input ./md_simple \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --output ./summary_json/deep_read.json \
  --retry-list-json ./summary_verify/deep_read.json
```

<p align="center">
  <img src=".github/assets/verify.png" width="70%" alt="verify" />
</p>

#### 步骤 5：翻译 Base64 Markdown

```bash
uv run deepresearch-flow translator translate \
  --input ./md_base64 \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --fix-level moderate \
  --output-dir ./md_base64_translated
```

#### 步骤 6：修复生成产物

在抽取和重试完成后，修复每一份摘要 JSON。JSON 输入须带 `--json`；
目录内可能有多个 template JSON，故保留 `-r`。

```bash
uv run deepresearch-flow recognize fix \
  --input ./summary_json --json -r --in-place

uv run deepresearch-flow recognize fix-math \
  --input ./summary_json --json -r \
  --model openai/gpt-4o-mini \
  --in-place

uv run deepresearch-flow recognize fix-mermaid \
  --input ./summary_json --json -r \
  --model openai/gpt-4o-mini \
  --in-place
```

<p align="center">
  <img src=".github/assets/fix-mermaid.png" width="70%" alt="fix mermaid" />
</p>

译文 Markdown 单独修复；Mermaid 修复只属于摘要 JSON 分支。

```bash
uv run deepresearch-flow recognize fix \
  --input ./md_base64_translated -r --in-place

uv run deepresearch-flow recognize fix-math \
  --input ./md_base64_translated -r \
  --model openai/gpt-4o-mini \
  --in-place
```

#### 步骤 7：构建 Snapshot 数据库或直接本地启动

两条命令都读取已修复的摘要 JSON。每增加一份 `summary_json/` 中的文件，
就增加一个 `--input`；二者不会读取对方的输出。

构建持久的 SQLite snapshot 和静态资源：

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./summary_json/deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static
```

或者以相同输入直接启动本地 Web UI：

```bash
uv run deepresearch-flow paper db serve \
  --input ./summary_json/deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdfs \
  --host 127.0.0.1
```

没有 BibTeX 文件时，省略 `--bibtex ./papers.bib`。

#### 步骤 8：启用语义搜索（可选）

从相同的已修复摘要和 Markdown 根目录构建 LanceDB 向量索引：

```bash
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --input ./summary_json/deep_read.json \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --max-concurrency 4 \
  --document-window 8 \
  --output-embed-db ./paper_vectors
```

启动时开启语义搜索：

```bash
uv run deepresearch-flow paper db serve \
  --input ./summary_json/deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdfs \
  --embed-db ./paper_vectors \
  --search-access-token "your-token"
```

#### 步骤 9：MCP 集成（可选）

项目通过 FastMCP 暴露有边界的 MCP 工具，供 AI Agent 访问。端点、鉴权和工具参考详见 [MCP 文档](docs/zh/api-and-mcp.md#mcp)。

---

## 延伸阅读

- **[高级工作流](docs/zh/workflow.md)** — 增量构建、合并 JSON/BibTeX、补充模板
- **[部署指南](docs/zh/deployment.md)** — CDN 部署、Nginx/Caddy 配置、Docker、Compose
- **[API & MCP](docs/zh/api-and-mcp.md)** — Admin API、推送、MCP 端点、鉴权与工具
- **[功能参考](docs/zh/reference.md)** — Translator、Extract、DB、Recognize 详解
- **[Snapshot 管理](docs/zh/snapshot-management.md)** — Snapshot 迁移、补充、更新

---

<p align="center">
  Built with love for the Open Science community.
</p>
