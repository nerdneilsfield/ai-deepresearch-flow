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

Start with `./pdfs/` and, optionally, `./papers.bib`. You do not need an
existing JSON library, SQLite database, or processed Markdown directory.

The workflow produces these roots:

```text
pdfs/ + papers.bib
  → ocr_output/
  → md_simple/            # local image files
  → md_base64/            # images embedded as data URLs
  ├─ summary_json/<template>.json
  └─ md_base64_translated/
```

#### Step 1: OCR PDFs or Images

Copy and configure the OCR settings:

```bash
cp ocr.example.toml ocr.toml
# Set: export PADDLE_OCR_TOKEN=xxx
# The example uses PaddleOCR-VL-1.6's asynchronous Job API.
# Adjust poll_interval_seconds and job_timeout_seconds in ocr.toml if needed.

uv run deepresearch-flow recognize ocr ./pdfs \
  --config ocr.toml \
  --output-dir ./ocr_output
# Processes up to 4 files concurrently by default; override with --workers 2.
```

The backend writes MinerU-compatible layouts: one `full.md` and `images/`
directory per document. The configured timeout stops local polling only; it does
not cancel the remote PaddleOCR job.

#### Step 2: Repair Nested OCR Outputs

Each OCR document is nested below `ocr_output/`, so both repair commands must
use `-r`:

```bash
# Repair Markdown structure in every OCR document
uv run deepresearch-flow recognize fix \
  --input ./ocr_output -r --in-place

# Repair LaTeX formulas in every OCR document
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

#### Step 3: Organize Source Markdown

Create both source representations in one pass. `organize` also needs `-r`
to discover nested OCR layouts. Do not pass `--fix`: Step 2 has already
repaired the OCR source.

```bash
uv run deepresearch-flow recognize organize \
  --input ./ocr_output -r \
  --output-simple ./md_simple \
  --output-base64 ./md_base64
```

`md_simple/` keeps image files under `md_simple/images/`; `md_base64/`
embeds images, so it is the translation input.

#### Step 4: Generate Structured Summaries

Generate one JSON bundle per selected prompt template. This example uses
`deep_read`; repeat it for every template you need, naming each output
`./summary_json/<template>.json`.

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

#### Step 4.1: Verify and Retry Summary Fields

Keep verification reports outside `summary_json/` so JSON repair scans only
summary bundles. `paper db verify` validates the JSON bundle; it does not
require a database. Repeat this unit for every selected template.

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

#### Step 5: Translate Base64 Markdown

```bash
uv run deepresearch-flow translator translate \
  --input ./md_base64 \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --fix-level moderate \
  --output-dir ./md_base64_translated
```

#### Step 6: Repair Generated Artifacts

Repair every summary JSON after extraction and retry. JSON inputs require
`--json`; keep `-r` because the directory can contain multiple template
bundles.

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

Repair the translated Markdown separately. Mermaid repair is only part of the
summary JSON branch.

```bash
uv run deepresearch-flow recognize fix \
  --input ./md_base64_translated -r --in-place

uv run deepresearch-flow recognize fix-math \
  --input ./md_base64_translated -r \
  --model openai/gpt-4o-mini \
  --in-place
```

#### Step 7: Build a Snapshot Database or Serve Locally

Both commands consume the repaired summary JSON. Add one `--input` option for
each additional file in `summary_json/`; neither command consumes the other
command's output.

Build a persistent SQLite snapshot and static assets:

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

Or start the local web UI directly from the same inputs:

```bash
uv run deepresearch-flow paper db serve \
  --input ./summary_json/deep_read.json \
  --bibtex ./papers.bib \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --pdf-root ./pdfs \
  --host 127.0.0.1
```

If you have no BibTeX file, omit `--bibtex ./papers.bib`.

#### Step 8: Add Semantic Search (Optional)

Build a LanceDB vector index from the same repaired summaries and Markdown
roots:

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

Serve with semantic search enabled:

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

#### Step 9: MCP Integration (Optional)

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
