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
- **MCP 集成** — FastMCP 服务，支持 AI Agent 访问，Streamable HTTP + SSE，GitHub OAuth 鉴权。

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

#### 步骤 1：抽取结构化信息

```bash
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read
```

<p align="center">
  <img src=".github/assets/extract.png" width="70%" alt="extract" />
</p>

#### 步骤 1.1：校验与重试缺失字段

```bash
uv run deepresearch-flow paper db verify \
  --input-json ./paper_infos.json \
  --prompt-template deep_read \
  --output-json ./paper_verify.json

uv run deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --retry-list-json ./paper_verify.json
```

<p align="center">
  <img src=".github/assets/verify.png" width="70%" alt="verify" />
</p>

#### 步骤 2：安全翻译

```bash
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --fix-level moderate
```

#### 步骤 2.5：对 PDF/图片执行 OCR（可选）

如果源文档是 PDF 或扫描图片：

```bash
cp ocr.example.toml ocr.toml
# 设置: export PADDLE_OCR_TOKEN=xxx

uv run deepresearch-flow recognize ocr ./pdfs --config ocr.toml --output-dir ./ocr_output
```

输出兼容 mineru 布局（每个文档一个 `full.md` + `images/` 目录）。

#### 步骤 3：修复 OCR 产物（推荐）

推荐顺序：`fix` → `fix-math` → `fix-mermaid` → `fix`。

```bash
# 修复 OCR Markdown 结构
uv run deepresearch-flow recognize fix \
  --input ./docs --in-place
```

<p align="center">
  <img src=".github/assets/fix.png" width="70%" alt="fix" />
</p>

```bash
# 修复 LaTeX 公式
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --in-place
```

<p align="center">
  <img src=".github/assets/fix-math.png" width="70%" alt="fix math" />
</p>

```bash
# 修复 Mermaid 图
uv run deepresearch-flow recognize fix-mermaid \
  --input ./paper_outputs --json \
  --model openai/gpt-4o-mini --in-place
```

<p align="center">
  <img src=".github/assets/fix-mermaid.png" width="70%" alt="fix mermaid" />
</p>

```bash
# 仅重试失败的公式/图
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --retry-failed

# 最后再修一遍统一格式
uv run deepresearch-flow recognize fix \
  --input ./docs --in-place
```

<p align="center">
  <img src=".github/assets/fix-retry-failed.png" width="70%" alt="fix retry failed" />
</p>

#### 步骤 4：启动本地知识库

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --md-root ./docs \
  --md-translated-root ./docs \
  --host 127.0.0.1
```

#### 步骤 4.1：启用语义搜索（可选）

从抽取结果构建 LanceDB 向量索引：

```bash
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --input ./paper_infos.json \
  --max-concurrency 4 \
  --document-window 8 \
  --output-embed-db ./paper_vectors
```

启动时开启语义搜索：

```bash
uv run deepresearch-flow paper db serve \
  --input ./paper_infos.json \
  --md-root ./docs \
  --embed-db ./paper_vectors \
  --search-access-token "your-token"
```

#### 步骤 5：MCP 集成（可选）

项目通过 FastMCP 暴露 MCP 工具和资源，供 AI Agent 访问。详见 [MCP 文档](docs/zh/api-and-mcp.md#mcp)。

---

## 延伸阅读

- **[高级工作流](docs/zh/workflow.md)** — 增量构建、合并 JSON/BibTeX、补充模板
- **[部署指南](docs/zh/deployment.md)** — CDN 部署、Nginx/Caddy 配置、Docker、Compose
- **[API & MCP](docs/zh/api-and-mcp.md)** — Admin API、推送、MCP 工具与资源
- **[功能参考](docs/zh/reference.md)** — Translator、Extract、DB、Recognize 详解
- **[Snapshot 管理](docs/zh/snapshot-management.md)** — Snapshot 迁移、补充、更新

---

<p align="center">
  Built with love for the Open Science community.
</p>
