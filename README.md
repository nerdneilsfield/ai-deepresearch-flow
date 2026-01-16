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
  <a href="https://ghcr.io/nerdneilsfield/deepresearch-flow">
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
- **Coverage Compare**: Compare JSON/PDF/Markdown/Translated datasets to find missing artifacts and export CSV reports.
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

### 3) The "Zero to Hero" Workflow

#### Step 1: Extract Insights

Scan a folder of markdown files and extract structured summaries.

```bash
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read
```

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

# 2) Fix LaTeX formulas
uv run deepresearch-flow recognize fix-math \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --in-place

# 3) Fix Mermaid diagrams
uv run deepresearch-flow recognize fix-mermaid \
  --input ./paper_outputs \
  --json \
  --model openai/gpt-4o-mini \
  --in-place

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

---

## Comprehensive Guide

<details>
<summary><strong>1. Translator: OCR-Safe Translation</strong></summary>

The translator module is built for scientific documents. It uses a node-based architecture to ensure stability.

- Structure Protection: automatically detects and "freezes" code blocks, LaTeX (`$$...$$`), HTML tables, and images before sending text to the LLM.
- OCR Repair: use `--fix-level` to merge broken paragraphs and convert text references (`[1]`) to clickable Markdown footnotes (`[^1]`).
- Context-Aware: supports retries for failed chunks and falls back gracefully.

```bash
# Translate with structure protection and OCR repairs
uv run deepresearch-flow translator translate \
  --input ./paper.md \
  --target-lang ja \
  --fix-level aggressive \
  --model claude/claude-3-5-sonnet-20240620
```

</details>

<details>
<summary><strong>2. Paper Extract: Structured Knowledge</strong></summary>

Turn loose markdown files into a queryable database.

- Templates: built-in prompts like `simple`, `eight_questions`, and `deep_read` guide the LLM to extract specific insights.
- Async and throttled: precise control over concurrency (`--max-concurrency`) and rate limits (`--sleep-every`).
- Incremental: skips already processed files; resumes from where you left off.

```bash
uv run deepresearch-flow paper extract \
  --input ./library \
  --output paper_data.json \
  --template-dir ./my-custom-prompts \
  --max-concurrency 10
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
<summary><strong>5. Recognize: OCR Post-Processing</strong></summary>

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
docker run --rm -v $(pwd):/app -it ghcr.io/nerdneilsfield/deepresearch-flow --help
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
