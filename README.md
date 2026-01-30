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

## The Core Pain Points

- **OCR Chaos**: Raw markdown from OCR tools is often broken -- tables drift, formulas break, and references are non-clickable.
- **Translation Nightmares**: Translating technical papers often destroys code blocks, LaTeX formulas, and table structures.
- **Information Overload**: Extracting structured insights (authors, venues, summaries) from hundreds of PDFs manually is impossible.
- **Context Switching**: Managing PDFs, summaries, and translations in different windows kills focus.

## The Solution

DeepResearch Flow provides a unified pipeline to **Repair**, **Translate**, **Extract**, and **Serve** your research library.

## Key Features

- **Smart Extraction**: Turn unstructured Markdown into schema-enforced JSON (summaries, metadata, Q&A) using LLMs (OpenAI, Claude, Gemini, etc.).
- **Precision Translation**: Translate OCR Markdown to Chinese/Japanese (`.zh.md`, `.ja.md`) while **freezing** formulas, code, tables, and references. No more broken layout.
- **Local Knowledge DB**: A high-performance local Web UI to browse papers with **Split View** (Source vs. Translated vs. Summary), full-text search, and multi-dimensional filtering.
- **Snapshot + API Serve**: Build a production-ready SQLite snapshot with static assets, then serve a read-only JSON API for a separate frontend.
- **Coverage Compare**: Compare JSON/PDF/Markdown/Translated datasets to find missing artifacts and export CSV reports.
- **Matched Export**: Extract matched JSON or translated Markdown after coverage checks.
- **OCR Post-Processing**: Automatically fix broken references (`[1]` -> `[^1]`), merge split paragraphs, and standardize layouts.

---

## Quick Start

### 1) Installation

```bash
# Recommended: using uv for speed
uv pip install deepresearch-flow

# Or standard pip
pip install deepresearch-flow
```

### 2) Configuration

Set up your LLM providers. We support OpenAI, Claude, Gemini, Ollama, and more.

```bash
cp config.example.toml config.toml
# Edit config.toml to add your API keys (e.g., env:OPENAI_API_KEY)
```

Multiple keys per provider are supported. Keys rotate per request and enter a short cooldown on retryable errors.
You can also provide quota metadata per key:

```toml
api_keys = [
  "env:OPENAI_API_KEY",
  { key = "env:OPENAI_API_KEY_2", quota_duration = 18000, reset_time = "2026-01-23 18:04:25 +0800 CST", quota_error_tokens = ["exceed", "quota"] }
]
```

### 3) The "Zero to Hero" Workflow

#### Step 1: Extract Insights

Scan a folder of markdown files and extract structured summaries.

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

Validate extracted JSON against the template schema and retry only the missing items.

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

#### Step 2: Translate Safely

Translate papers to Chinese, protecting LaTeX and tables.

```bash
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --fix-level moderate
```

#### Step 3: Repair OCR Outputs (Recommended)

Recommended sequence to stabilize markdown before serving:

```bash
# 1) Fix OCR markdown (auto-detects JSON if inputs are .json)
uv run deepresearch-flow recognize fix \
  --input ./docs \
  --in-place
```

<p align="center">
  <img src=".github/assets/fix.png" width="70%" alt="fix" />
</p>

```bash
# 2) Fix LaTeX formulas
uv run deepresearch-flow recognize fix-math \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --in-place
```

<p align="center">
  <img src=".github/assets/fix-math.png" width="70%" alt="fix math" />
</p>

```bash
# 3) Fix Mermaid diagrams
uv run deepresearch-flow recognize fix-mermaid \
  --input ./paper_outputs \
  --json \
  --model openai/gpt-4o-mini \
  --in-place
```

<p align="center">
  <img src=".github/assets/fix-mermaid.png" width="70%" alt="fix mermaid" />
</p>

```bash
# (optional) Retry failed formulas/diagrams only
uv run deepresearch-flow recognize fix-math \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --retry-failed

uv run deepresearch-flow recognize fix-mermaid \
  --input ./paper_outputs \
  --json \
  --model openai/gpt-4o-mini \
  --retry-failed
```

<p align="center">
  <img src=".github/assets/fix-retry-failed.png" width="70%" alt="fix retry failed" />
</p>

```bash
# 4) Fix again to normalize formatting
uv run deepresearch-flow recognize fix \
  --input ./docs \
  --in-place
```

#### Step 4: Serve Your Database

Launch a local UI to read and manage your papers.

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --md-root ./docs \
  --md-translated-root ./docs \
  --host 127.0.0.1
```

#### Step 4.5: Build Snapshot + Serve API + Frontend (Recommended)

Build a production snapshot (SQLite + static assets), serve a read-only API, and run the frontend.

```bash
# 1) Build snapshot + static export
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static

# 2) Serve static assets (CORS required for ZIP export)
npx http-server ./dist/paper-static -p 8002 --cors

# 3) Serve API (read-only)
PAPER_DB_STATIC_BASE_URL=http://127.0.0.1:8002 \
uv run deepresearch-flow paper db api serve \
  --snapshot-db ./dist/paper_snapshot.db \
  --cors-origin http://127.0.0.1:5173 \
  --host 127.0.0.1 --port 8001

# 4) Run frontend
cd frontend
npm install
VITE_PAPER_DB_API_BASE=http://127.0.0.1:8001/api/v1 \
VITE_PAPER_DB_STATIC_BASE=http://127.0.0.1:8002 \
npm run dev
```

---

## Incremental PDF Library Workflow

This workflow keeps a growing PDF library in sync without reprocessing everything.

```bash
# 1) Compare processed JSON vs new PDF library to find missing PDFs
uv run deepresearch-flow paper db compare \
  --input-a ./paper_infos.json \
  --pdf-root-b ./pdfs_new \
  --output-only-in-b ./pdfs_todo.txt

# 2) Stage the missing PDFs for OCR
uv run deepresearch-flow paper db transfer-pdfs \
  --input-list ./pdfs_todo.txt \
  --output-dir ./pdfs_todo \
  --copy

# (optional) use --move instead of --copy
# uv run deepresearch-flow paper db transfer-pdfs --input-list ./pdfs_todo.txt --output-dir ./pdfs_todo --move

# 3) OCR the missing PDFs (use your OCR tool; write markdowns to ./md_todo)

# 4) Export matched existing assets against the new PDF library
uv run deepresearch-flow paper db extract \
  --input-json ./paper_infos.json \
  --pdf-root ./pdfs_new \
  --output-json ./paper_infos_matched.json

uv run deepresearch-flow paper db extract \
  --md-source-root ./mds \
  --output-md-root ./mds_matched \
  --pdf-root ./pdfs_new

uv run deepresearch-flow paper db extract \
  --md-translated-root ./translated \
  --output-md-translated-root ./translated_matched \
  --pdf-root ./pdfs_new \
  --lang zh

# 5) Translate + extract summaries for the new OCR markdowns
uv run deepresearch-flow translator translate \
  --input ./md_todo \
  --target-lang zh \
  --model openai/gpt-4o-mini

uv run deepresearch-flow paper extract \
  --input ./md_todo \
  --model openai/gpt-4o-mini

# 6) Merge and serve the new library (multi-input)
uv run deepresearch-flow paper db serve \
  --input ./paper_infos_matched.json \
  --input ./paper_infos_new.json \
  --md-root ./mds_matched \
  --md-root ./md_todo \
  --md-translated-root ./translated_matched \
  --md-translated-root ./md_todo \
  --pdf-root ./pdfs_new
```

## Merge Paper JSONs

```bash
# Merge multiple libraries using the same template
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# Merge multiple templates from the same library (first input wins on shared fields)
uv run deepresearch-flow paper db merge templates \
  --inputs ./simple.json \
  --inputs ./deep_read.json \
  --output ./paper_infos_templates.json
```

Note: `paper db merge` is now split into `merge library` and `merge templates`.

### Recommended: Merge templates then filter by BibTeX

```bash
# 1) Merge templates for the same library
uv run deepresearch-flow paper db merge templates \
  --inputs ./deep_read.json \
  --inputs ./simple.json \
  --output ./all.json

# 2) Filter the merged set with BibTeX
uv run deepresearch-flow paper db extract \
  --input-bibtex ./library.bib \
  --json ./all.json \
  --output-json ./library_filtered.json \
  --output-csv ./library_filtered.csv
```

## Deployment (Static CDN)

The recommended production setup is **front/back separation**:

- **Static CDN** hosts PDFs/Markdown/images/summaries.
- **API server** serves a read-only snapshot DB.
- **Frontend** is a separate static app (Vite build or any static host).

<p align="center">
  <img src=".github/assets/frontend.png" width="80%" alt="frontend" />
</p>

### 1) Build snapshot + static export

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
- The build host must be able to read the original PDF/Markdown roots.
- The CDN only needs the exported directory (e.g. `/data/paper-static`).

### 2) Serve static assets with CORS + cache headers (Caddy example)

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

### 2.1) Nginx example (API + frontend on one domain, static on another)

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
    proxy_pass http://127.0.0.1:8001/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
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

### 3) Start the API server (read-only)

```bash
export PAPER_DB_STATIC_BASE_URL="https://static.example.com"

uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

### 4) Frontend (static build or dev)

```bash
cd frontend
npm install

# Dev
VITE_PAPER_DB_API_BASE=https://api.example.com/api/v1 \
VITE_PAPER_DB_STATIC_BASE=https://static.example.com \
npm run dev

# Build for static hosting
VITE_PAPER_DB_API_BASE=https://api.example.com/api/v1 \
VITE_PAPER_DB_STATIC_BASE=https://static.example.com \
npm run build
```

---

## Comprehensive Guide

<details>
<summary><strong>1. Translator: OCR-Safe Translation</strong></summary>

The translator module is built for scientific documents. It uses a node-based architecture to ensure stability.

- Structure Protection: automatically detects and "freezes" code blocks, LaTeX (`$$...$$`), HTML tables, and images before sending text to the LLM.
- OCR Repair: use `--fix-level` to merge broken paragraphs and convert text references (`[1]`) to clickable Markdown footnotes (`[^1]`).
- Context-Aware: supports retries for failed chunks and falls back gracefully.
- Group Concurrency: use `--group-concurrency` to run multiple translation groups in parallel per document.

```bash
# Translate with structure protection and OCR repairs
uv run deepresearch-flow translator translate \
  --input ./paper.md \
  --target-lang ja \
  --fix-level aggressive \
  --group-concurrency 4 \
  --model claude/claude-3-5-sonnet-20240620
```

</details>

<details>
<summary><strong>2. Paper Extract: Structured Knowledge</strong></summary>

Turn loose markdown files into a queryable database.

- Templates: built-in prompts like `simple`, `eight_questions`, and `deep_read` guide the LLM to extract specific insights.
- Async and throttled: precise control over concurrency (`--max-concurrency`), rate limits (`--sleep-every`), and request timeout (`--timeout`).
- Incremental: skips already processed files; resumes from where you left off.
- Stage resume: multi-stage templates persist per-module outputs; use `--force-stage <name>` to rerun a module.
- Stage DAG: enable `--stage-dag` (or `extract.stage_dag = true`) for dependency-aware parallelism; DAG mode only passes dependency outputs to a stage and `--dry-run` prints the per-stage plan.
- Diagram hints: `deep_read` can emit inferred diagrams labeled `[Inferred]`; use `recognize fix-mermaid` on rendered markdown if needed.
- Stage focus: multi-stage runs emphasize the active module and summarize others to reduce context overload.
- Range filter: use `--start-idx/--end-idx` to slice inputs; range applies before `--retry-failed`/`--retry-failed-stages` (`--end-idx -1` = last item).
- Retry failed stages: use `--retry-failed-stages` to re-run only failed stages (multi-stage templates); missing stages are forced to run. Retry runs keep existing results and only update retried items.

```bash
uv run deepresearch-flow paper extract \
  --input ./library \
  --output paper_data.json \
  --template-dir ./my-custom-prompts \
  --max-concurrency 10 \
  --timeout 180

# Extract items 0..99, then retry only failed ones from that range
uv run deepresearch-flow paper extract \
  --input ./library \
  --start-idx 0 \
  --end-idx 100 \
  --retry-failed \
  --model claude/claude-3-5-sonnet-20240620

# Retry only failed stages in multi-stage templates
uv run deepresearch-flow paper extract \
  --input ./library \
  --retry-failed-stages \
  --model claude/claude-3-5-sonnet-20240620
```

</details>

<details>
<summary><strong>4. Recognize Fix: Repair Math and Mermaid</strong></summary>

Fix broken LaTeX formulas and Mermaid diagrams in markdown or JSON outputs.

- Retry Failed: use `--retry-failed` with the prior `--report` output to reprocess only failed formulas/diagrams.

```bash
uv run deepresearch-flow recognize fix-math \
  --input ./docs \
  --in-place \
  --model claude/claude-3-5-sonnet-20240620 \
  --report ./fix-math-errors.json \
  --retry-failed

uv run deepresearch-flow recognize fix-mermaid \
  --input ./docs \
  --in-place \
  --model claude/claude-3-5-sonnet-20240620 \
  --report ./fix-mermaid-errors.json \
  --retry-failed
```

</details>

<details>
<summary><strong>3. Database and UI: Your Personal ArXiv</strong></summary>

The db serve command creates a local research station.

- Split View: read the original PDF/Markdown on the left and the Summary/Translation on the right.
- Full Text Search: search by title, author, year, or content tags (`tag:fpga year:2023..2024`).
- Stats: visualize publication trends and keyword frequencies.
- PDF Viewer: built-in PDF.js viewer prevents cross-origin issues with local files.

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --pdf-root ./pdfs \
  --cache-dir .cache/db
```

</details>

<details>
<summary><strong>4. Paper DB Compare: Coverage Audit</strong></summary>

Compare two datasets (A/B) to find missing PDFs, markdowns, translations, or JSON items, with match metadata.

```bash
uv run deepresearch-flow paper db compare \
  --input-a ./a.json \
  --md-root-b ./md_root \
  --output-csv ./compare.csv

# Compare translated markdowns by language
uv run deepresearch-flow paper db compare \
  --md-translated-root-a ./translated_a \
  --md-translated-root-b ./translated_b \
  --lang zh
```

</details>

<details>
<summary><strong>5. Paper DB Extract: Matched Export</strong></summary>

Extract matched JSON entries or translated Markdown after coverage comparison.

```bash
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-bibtex ./refs.bib \
  --pdf-root ./pdfs \
  --output-json ./matched.json \
  --output-csv ./extract.csv

# Use a JSON reference list to filter the target JSON
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-json ./reference.json \
  --pdf-root ./pdfs \
  --output-json ./matched.json \
  --output-csv ./extract.csv

# Extract translated markdowns by language
uv run deepresearch-flow paper db extract \
  --md-root ./md_root \
  --md-translated-root ./translated \
  --lang zh \
  --output-md-translated-root ./translated_matched \
  --output-csv ./extract.csv
```

</details>

<details>
<summary><strong>6. Recognize: OCR Post-Processing</strong></summary>

Tools to clean up raw outputs from OCR engines like MinerU.

- Embed Images: convert local image links to Base64 for a portable single-file Markdown.
- Unpack Images: extract Base64 images back to files.
- Organize: flatten nested OCR output directories.
- Fix: apply OCR fixes and rumdl formatting during organize, or as a standalone step.
- Fix JSON: apply the same fixes to markdown fields inside paper JSON outputs.
- Fix Math: validate and repair LaTeX formulas with optional LLM assistance.
- Fix Mermaid: validate and repair Mermaid diagrams (requires `mmdc` from mermaid-cli).
- Recommended order: `fix` -> `fix-math` -> `fix-mermaid` -> `fix`.

```bash
uv run deepresearch-flow recognize md embed --input ./raw_ocr --output ./clean_md
```

```bash
# Organize MinerU output and apply OCR fixes
uv run deepresearch-flow recognize organize \
  --input ./mineru_outputs \
  --output-simple ./ocr_md \
  --fix

# Fix and format existing markdown outputs
uv run deepresearch-flow recognize fix \
  --input ./ocr_md \
  --output ./ocr_md_fixed

# Fix in place
uv run deepresearch-flow recognize fix \
  --input ./ocr_md \
  --in-place

# Fix JSON outputs in place
uv run deepresearch-flow recognize fix \
  --json \
  --input ./paper_outputs \
  --in-place

# Fix LaTeX formulas in markdown
uv run deepresearch-flow recognize fix-math \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --in-place

# Fix Mermaid diagrams in JSON outputs
uv run deepresearch-flow recognize fix-mermaid \
  --json \
  --input ./paper_outputs \
  --model openai/gpt-4o-mini \
  --in-place
```

</details>

---

## Docker Support

Don't want to manage Python environments?

```bash
docker run --rm -v $(pwd):/app -it ghcr.io/nerdneilsfield/deepresearch-flow:latest --help
```

Deploy image (API + frontend via nginx):

```bash
docker run --rm -p 8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

Notes:
- nginx listens on 8899 and proxies `/api` to the internal API at `127.0.0.1:8000`.
- Mount your snapshot DB to `/db/papers.db` inside the container.
- Mount snapshot static assets to `/static` when serving assets from this container (default `PAPER_DB_STATIC_BASE` is `/static`).
- If `PAPER_DB_STATIC_BASE` is a full URL (e.g. `https://static.example.com`), nginx still serves the frontend locally, while API responses use that external static base for asset links.

Docker Compose example (two modes):

```bash
docker compose -f scripts/docker/docker-compose.example.yml --profile local-static up
# or
docker compose -f scripts/docker/docker-compose.example.yml --profile external-static up
```

External static assets example:

```bash
docker run --rm -p 8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -e PAPER_DB_STATIC_BASE=https://static.example.com \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

## Configuration

The config.toml is your control center. It supports:

- Multiple Providers: mix and match OpenAI, DeepSeek (DashScope), Gemini, Claude, and Ollama.
- Model Routing: explicit routing to specific models (`--model provider/model_name`).
- Environment Variables: keep secrets safe using `env:VAR_NAME` syntax.

See `config.example.toml` for a full reference.

---

<p align="center">
  Built with love for the Open Science community.
</p>
