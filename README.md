# DeepResearch Flow

**The All-in-One Workflow for Deep Research Automation**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

**DeepResearch Flow** is a command-line power tool designed for researchers and engineers who need to process, understand, and organize massive collections of technical documents. It bridges the gap between raw OCR output and structured human knowledge.

---

## ⚡️ The Core Pain Points

- **OCR Chaos**: Raw markdown from OCR tools is often broken -- tables drift, formulas break, and references are non-clickable.
- **Translation Nightmares**: Translating technical papers often destroys code blocks, LaTeX formulas, and table structures.
- **Information Overload**: Extracting structured insights (authors, venues, summaries) from hundreds of PDFs manually is impossible.
- **Context Switching**: Managing PDFs, summaries, and translations in different windows kills focus.

## 🛡️ The Solution

DeepResearch Flow provides a unified pipeline to **Repair**, **Translate**, **Extract**, and **Serve** your research library.

## Key Features

- **🔍 Smart Extraction**: Turn unstructured Markdown into schema-enforced JSON (summaries, metadata, Q&A) using LLMs (OpenAI, Claude, Gemini, etc.).
- **🌐 Precision Translation**: Translate OCR Markdown to Chinese/Japanese (`.zh.md`, `.ja.md`) while **freezing** formulas, code, tables, and references. No more broken layout.
- **📚 Local Knowledge DB**: A high-performance local Web UI to browse papers with **Split View** (Source vs. Translated vs. Summary), full-text search, and multi-dimensional filtering.
- **🛠️ OCR Post-Processing**: Automatically fix broken references (`[1]` -> `[^1]`), merge split paragraphs, and standardize layouts.

---

## 🚀 Quick Start

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

#### Step 3: Serve Your Database

Launch a local UI to read and manage your papers.

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --md-root ./docs \
  --md-translated-root ./docs \
  --host 127.0.0.1
```

---

## 📖 Comprehensive Guide

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
<summary><strong>4. Recognize: OCR Post-Processing</strong></summary>

Tools to clean up raw outputs from OCR engines like MinerU.

- Embed Images: convert local image links to Base64 for a portable single-file Markdown.
- Unpack Images: extract Base64 images back to files.
- Organize: flatten nested OCR output directories.

```bash
uv run deepresearch-flow recognize md embed --input ./raw_ocr --output ./clean_md
```

</details>

---

## 🐳 Docker Support

Don't want to manage Python environments?

```bash
docker run --rm -v $(pwd):/app -it ghcr.io/nerdneilsfield/deepresearch-flow --help
```

## ⚙️ Configuration

The config.toml is your control center. It supports:

- Multiple Providers: mix and match OpenAI, DeepSeek (DashScope), Gemini, Claude, and Ollama.
- Model Routing: explicit routing to specific models (`--model provider/model_name`).
- Environment Variables: keep secrets safe using `env:VAR_NAME` syntax.

See `config.example.toml` for a full reference.

---

<p align="center">
  Built with love for the Open Science community.
</p>
