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

### 3) 处理一批 PDF

准备一个项目目录，放入 `pdf/` 和恰好一个 `.bib` 文件，然后生成并运行 `run.sh`：

```bash
mkdir myproj
cp -r /path/to/pdfs myproj/pdf
cp /path/to/library.bib myproj/

cp ocr.example.toml ocr.toml     # 填写 OCR 后端的 token
uv run deepresearch-flow utils init-run ./myproj
```

打开 `myproj/run.sh`，把每处 `--model PROVIDER/MODEL` 替换为 `config.toml` 中已声明的 `provider/model`。各步骤可以用不同的模型。然后运行：

```bash
./myproj/run.sh
```

脚本依次完成 OCR、公式与格式修复、结构化摘要抽取、翻译，最后构建快照数据库，输出在 `myproj/dist/`。失败的条目会自动重试，中断后重新运行会从失败的那一步继续。完整说明见 [从 PDF 到快照](docs/zh/pipeline.md)。

### 4) 浏览结果

`run.sh` 结束后，用本地 Web UI 查看：

```bash
uv run deepresearch-flow paper db serve \
  --input myproj/deep_read.json \
  --bibtex myproj/library.bib \
  --md-root myproj/md_simple \
  --md-translated-root myproj/md_base64_translated \
  --pdf-root myproj/pdf
```

生产环境部署快照与静态资源，见 [部署指南](docs/zh/deployment.md)。

---

## 文档

| 文档 | 内容 |
|---|---|
| [从 PDF 到快照](docs/zh/pipeline.md) | 用 `run.sh` 处理一个项目：步骤、重试、退出码、通知 |
| [高级工作流](docs/zh/workflow.md) | 对已有数据做增量构建、合并、补充模板和翻译 |
| [功能参考](docs/zh/reference.md) | Translator、Extract、Recognize、语义搜索、退出码、`init-run` 的选项 |
| [Snapshot 管理](docs/zh/snapshot-management.md) | Snapshot 的迁移、补充和更新 |
| [部署指南](docs/zh/deployment.md) | CDN、Nginx/Caddy、Docker、Compose、Admin 流水线 |
| [API 与 MCP](docs/zh/api-and-mcp.md) | Admin API、推送、MCP 端点、鉴权与工具 |

---

<p align="center">
  Built with love for the Open Science community.
</p>
