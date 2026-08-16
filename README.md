<p align="center">
  <img src=".github/assets/logo.png" width="140" alt="ai-deepresearch-flow logo" />
</p>

<h3 align="center">ai-deepresearch-flow</h3>

<p align="center">
  <em>From documents to deep research insight — automatically.</em>
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

## Core Pain Points

- **OCR Chaos**: Raw markdown from OCR tools is often broken — tables drift, formulas break, references are non-clickable.
- **Translation Nightmares**: Translating technical papers often destroys code blocks, LaTeX formulas, and table structures.
- **Information Overload**: Extracting structured insights (authors, venues, summaries) from hundreds of PDFs manually is impossible.
- **Context Switching**: Managing PDFs, summaries, and translations in different windows kills focus.

## Solution

DeepResearch Flow provides a unified pipeline to **Repair**, **Translate**, **Extract**, and **Serve** your research library.

## Key Features

- **Smart Extraction** — Turn unstructured Markdown into schema-enforced JSON (summaries, metadata, Q&A) using LLMs.
- **Precision Translation** — Translate OCR Markdown to Chinese/Japanese while freezing formulas, code, tables, and references.
- **Local Knowledge DB** — Web UI with Split View (Source/Translation/Summary), full-text search, and multi-dimensional filtering.
- **Snapshot + API Serve** — Production-ready SQLite snapshot with static assets and read-only JSON API.
- **OCR Post-Processing** — Fix broken references, merge split paragraphs, repair LaTeX and Mermaid diagrams.
- **Semantic Search** — LanceDB-backed vector search with hybrid recall and cloud reranking.
- **MCP Integration** — FastMCP server for AI agent access with bounded read tools, static-bearer Streamable HTTP/SSE, and GitHub OAuth at `/oauth/mcp`.

---

## Quick Start

### 1) Installation

```bash
uv pip install deepresearch-flow
# or: pip install deepresearch-flow
```

### 2) Configuration

```bash
cp config.example.toml config.toml
```

Minimal config with weighted multi-provider routing:

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

Keys use `env:VAR_NAME` syntax to keep secrets out of config files. Multiple providers (Ollama, Gemini, DashScope, Azure OpenAI) are supported. For full configuration options (embedding, rerank, translator defaults, search), see `config.example.toml`.

### 3) The "Zero to Hero" Workflow

#### Step 1: Extract Structured Insights

```bash
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read
```

<p align="center">
  <img src=".github/assets/extract.png" width="70%" alt="extract" />
</p>

#### Step 1.1: Verify & Retry Missing Fields

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

#### Step 2: Safe Translation

```bash
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --fix-level moderate
```

#### Step 2.5: OCR on PDFs/Images (Optional)

If your source documents are PDFs or scanned images:

```bash
cp ocr.example.toml ocr.toml
# Set: export PADDLE_OCR_TOKEN=xxx
# The example uses PaddleOCR-VL-1.6's asynchronous Job API.
# Adjust poll_interval_seconds and job_timeout_seconds in ocr.toml if needed.

uv run deepresearch-flow recognize ocr ./pdfs --config ocr.toml --output-dir ./ocr_output
```

The backend uploads local PDF/image files, polls the Job API, then writes the result in
the mineru layout (`full.md` + `images/` per document). The configured timeout stops the
local wait only; it does not cancel the remote PaddleOCR job.

#### Step 3: Repair OCR Outputs (Recommended)

Recommended order: `fix` → `fix-math` → `fix-mermaid` → `fix`.

```bash
# Fix OCR markdown structure
uv run deepresearch-flow recognize fix \
  --input ./docs --in-place
```

<p align="center">
  <img src=".github/assets/fix.png" width="70%" alt="fix" />
</p>

```bash
# Fix LaTeX formulas
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --in-place
```

<p align="center">
  <img src=".github/assets/fix-math.png" width="70%" alt="fix math" />
</p>

```bash
# Fix Mermaid diagrams
uv run deepresearch-flow recognize fix-mermaid \
  --input ./paper_outputs --json \
  --model openai/gpt-4o-mini --in-place
```

<p align="center">
  <img src=".github/assets/fix-mermaid.png" width="70%" alt="fix mermaid" />
</p>

```bash
# Retry only failed formulas/diagrams
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --retry-failed

# Final format normalization
uv run deepresearch-flow recognize fix \
  --input ./docs --in-place
```

<p align="center">
  <img src=".github/assets/fix-retry-failed.png" width="70%" alt="fix retry failed" />
</p>

#### Step 4: Serve Your Local Knowledge Base

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --md-root ./docs \
  --md-translated-root ./docs \
  --host 127.0.0.1
```

#### Step 4.1: Add Semantic Search (Optional)

Build a LanceDB vector index from extracted JSON:

```bash
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --input ./paper_infos.json \
  --max-concurrency 4 \
  --document-window 8 \
  --output-embed-db ./paper_vectors
```

Serve with semantic search enabled:

```bash
uv run deepresearch-flow paper db serve \
  --input ./paper_infos.json \
  --md-root ./docs \
  --embed-db ./paper_vectors \
  --search-access-token "your-token"
```

#### Step 5: MCP Integration (Optional)

The project exposes bounded MCP tools for AI agent access via FastMCP. See the [MCP documentation](docs/en/api-and-mcp.md#mcp) for endpoint, auth, and tool reference.

---

## Further Reading

- **[Advanced Workflows](docs/en/workflow.md)** — Incremental builds, merging JSON/BibTeX, supplementing templates
- **[Deployment](docs/en/deployment.md)** — CDN serving, Nginx/Caddy config, Docker, Compose
- **[API & MCP](docs/en/api-and-mcp.md)** — Admin API, push/push-semantic, MCP endpoints, auth, and tools
- **[Reference](docs/en/reference.md)** — Translator, Extract, DB & Recognize in detail
- **[Snapshot Management](docs/en/snapshot-management.md)** — Snapshot migration, supplement, update

---

<p align="center">
  Built with love for the Open Science community.
</p>
