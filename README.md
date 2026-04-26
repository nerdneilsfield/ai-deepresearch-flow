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
# Edit config.toml to add your weighted providers, keys, and models
```

Breaking change: the old `api_keys`, `model_list`, and `structured_mode` fields are no longer accepted.
The new config uses:

- top-level `main_model` for weighted model routing
- `providers[].base[]` for weighted URL routing
- `providers[].base[].key[]` for weighted key routing
- `providers[].models[]` for model capability declarations

Missing `env:VAR_NAME` references now fail explicitly during config load.

Optional translator CLI and scheduler defaults can live under `[translator_config]`:

```toml
[translator_config]
model = "openai/gpt-4o-mini"
# Optional: same format as model/--model; omit to keep retry on the main model.
# If retry_model uses a different provider from model, also set retry_concurrency;
# otherwise retry requests share the main model semaphore.
# retry_model = "openai/gpt-4.1-mini"
# retry_model = '[{"model":"openai/gpt-4.1-mini","weight":3},{"model":"claude/claude-sonnet-4-5-20250929","weight":1}]'
fallback_model = "claude/claude-sonnet-4-5-20250929"
# fallback_model_2 = "ollama/llama3.1"
document_window = 8
initial_workers = 4
retry_workers = 2
fallback_workers = 2
fallback_2_workers = 2
main_concurrency = 4
# Optional: omit to share main_concurrency with retry requests.
# retry_concurrency = 2
fallback_concurrency = 2
fallback_2_concurrency = 2
```

Per-key quota metadata still lives on the key object:

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
    { value = "env:OPENAI_API_KEY", weight = 4 },
    { value = "env:OPENAI_API_KEY_2", weight = 1, quota_duration = 18000, reset_time = "2026-01-23 18:04:25 +0800 CST", quota_error_tokens = ["exceed", "quota"] }
  ] }
]
models = [
  { model_name = "gpt-4o-mini", is_stream = true, is_support_json_schema = true, is_support_json_object = true }
]
```

```toml
[embedding]
default_model = "Qwen3-Embedding-4B"
default_provider = "ollama"
dimensions = 1024
normalized = true
batch_size = 32
max_concurrency = 1
chunk_max_tokens = 512
chunk_overlap_tokens = 64

[[embedding.providers]]
name = "ollama"
type = "openai_compatible"
base = [
  { url = "http://localhost:11434/v1", weight = 1, key = [{ value = "ollama", weight = 1 }] }
]
models = [
  { model_name = "Qwen3-Embedding-4B", dimensions = 1024, max_context = 32768 },
  { model_name = "bge-m3", dimensions = 1024, max_context = 8192 }
]

[[embedding.providers]]
name = "siliconflow"
type = "openai_compatible"
base = [
  { url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "env:SF_KEY", weight = 1 }] }
]
models = [
  { model_name = "Qwen/Qwen3-Embedding-4B", dimensions = 2560, max_context = 32768 }
]

[rerank]
enabled = true
default_model = "BAAI/bge-reranker-v2-m3"
default_provider = "siliconflow"
top_n = 10

[[rerank.providers]]
name = "siliconflow"
type = "openai_compatible"
base = [
  { url = "https://api.siliconflow.cn/v1", weight = 1, key = [{ value = "env:SF_KEY", weight = 1 }] }
]
models = [
  { model_name = "BAAI/bge-reranker-v2-m3", max_context = 8192, max_chunks_per_doc = 1024 },
  { model_name = "Qwen/Qwen3-Reranker-8B", max_context = 32768, instruction = "Rerank by relevance" }
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

#### Step 2.5: Run OCR on PDFs/Images (Optional)

If your source documents are PDFs or scanned images, run OCR first to produce markdown:

```bash
# 1) Copy and edit the OCR config
cp ocr.example.toml ocr.toml
# Set your PaddleOCR token: export PADDLE_OCR_TOKEN=xxx

# 2) Run OCR on a directory of PDFs
uv run deepresearch-flow recognize ocr ./pdfs --config ocr.toml --output-dir ./ocr_output
```

Output follows the mineru layout (`full.md` + `images/` per document), compatible with the repair steps below.

See [`ocr.example.toml`](ocr.example.toml) for backend configuration. Currently supports PaddleOCR; more backends planned.

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

#### Step 4.1: Add Semantic Search (Optional)

Build a LanceDB vector index from extracted JSON or an existing snapshot:

```bash
# Build from one or more extracted JSON files
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --input ./paper_infos.json \
  --max-concurrency 4 \
  --output-embed-db ./paper_vectors

# Or build later from an existing snapshot + static export
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper_static \
  --output-embed-db ./paper_vectors

# Or build alongside snapshot generation
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos.json \
  --output-embed-db ./paper_vectors
```

Search from the CLI:

```bash
uv run deepresearch-flow paper search \
  --config ./config.toml \
  --embed-db ./paper_vectors \
  --query "attention mechanism in transformer" \
  --top-n 10
```

Enable semantic search in the local Web UI:

```bash
uv run deepresearch-flow paper db serve \
  --input ./paper_infos.json \
  --md-root ./docs \
  --md-translated-root ./docs \
  --embed-db ./paper_vectors \
  --search-access-token "your-token"
```

Notes:

- `paper embed` accepts repeatable `-i/--input` JSON files and merges multiple templates for the same paper.
- `paper embed --snapshot-db --static-export-dir` lets you add or rebuild embeddings later from an already-built snapshot.
- `paper embed` and `paper db snapshot build --output-embed-db` automatically ensure scalar indexes for `doc_id` and `template_tag` inside the LanceDB store. Existing vector stores missing those indexes are upgraded during the build, even if the current run does not add new chunks.
- `paper search` uses the configured embedding provider/model from `[[embedding.providers]]`, optional hybrid recall, and optional cloud reranking from `[[rerank.providers]]`.
- The Web UI exposes a lock button next to the search bar. After you enter the token once, it is stored in the browser and reused for `/api/papers/semantic`.
- `paper db snapshot build --output-embed-db` builds the snapshot and LanceDB index in one pass.

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
# Basic mode: keyword / facets / paper detail only
PAPER_DB_STATIC_BASE_URL=http://127.0.0.1:8002 \
uv run deepresearch-flow paper db api serve \
  --snapshot-db ./dist/paper_snapshot.db \
  --cors-origin http://127.0.0.1:5173 \
  --host 127.0.0.1 --port 8001

# Advanced search mode: add LanceDB + token
PAPER_DB_STATIC_BASE_URL=http://127.0.0.1:8002 \
SEARCH_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./paper_vectors \
  --config ./config.toml \
  --cors-origin http://127.0.0.1:5173 \
  --host 127.0.0.1 --port 8001

# 4) Run frontend
cd frontend
npm install
VITE_PAPER_DB_API_BASE=http://127.0.0.1:8001/api/v1 \
VITE_PAPER_DB_STATIC_BASE=http://127.0.0.1:8002 \
npm run dev
```

#### Step 4.6: Supplement Missing Templates (Optional)

If some papers are missing specific templates (e.g., `deep_read`), you can identify gaps and supplement extract them:

```bash
# 1) Check missing templates in snapshot
uv run deepresearch-flow paper db snapshot show-missing \
  --snapshot-db ./dist/paper_snapshot.db

# 2) Export papers missing specific template (with file paths for extraction)
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type template \
  --template deep_read \
  --static-export-dir ./dist/paper-static \
  --output ./missing_deep_read.json \
  --txt-output ./missing_ids.txt \
  --output-paths ./extractable_paths.txt

# 3) Extract missing templates (only for papers with source markdown)
uv run deepresearch-flow paper extract \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --input-list ./extractable_paths.txt \
  --output ./deep_read_supplement.json

# 4) Merge with existing paper_infos.json
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos.json \
  --inputs ./deep_read_supplement.json \
  --output ./paper_infos_complete.json

# 5) Rebuild snapshot with complete data
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs \
  --pdf-root ./pdfs \
  --output-db ./dist/paper_snapshot_complete.db \
  --static-export-dir ./dist/paper-static-complete
```

**Alternative 1: Supplement Missing Content (Templates/Translations)**

If existing papers are missing templates or translations, supplement them without rebuilding:

```bash
# Supplement missing templates for existing papers (in-place)
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --in-place

# Or output to new location
uv run deepresearch-flow paper db snapshot supplement \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./deep_read_supplement.json \
  --output-db ./dist/paper_snapshot_supplemented.db \
  --output-static-dir ./dist/paper-static-supplemented
```

Notes:
- `--md-root` and `--md-translated-root` are optional for `snapshot supplement`.
- Use them only when you want to resolve/copy markdown files from local source directories.

**Alternative 2: Add New Papers**

If you have completely new papers to add to the snapshot:

```bash
# Add new papers to existing snapshot (in-place)
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --md-translated-root ./docs_translated \
  --pdf-root ./pdfs \
  --in-place

# Or output to new location
uv run deepresearch-flow paper db snapshot update \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  -i ./new_papers.json \
  -b ./new_papers.bib \
  --md-root ./docs \
  --output-db ./dist/paper_snapshot_updated.db \
  --output-static-dir ./dist/paper-static-updated
```

**Differences:**
- `supplement`: Only adds missing templates/translations for **existing** papers (skips new papers)
- `update`: Only adds **completely new** papers (skips existing papers)

#### Upgrade Legacy Snapshot Schema (DOI/BibTeX)

**Recommended: Migrate Schema In-Place (No Data Loss)**

If your existing snapshot was built before DOI/BibTeX support, use the `migrate` command to upgrade the schema without losing any papers:

```bash
# In-place migration with timestamped backup
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --in-place

# Or copy to new location
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --bibtex ./papers.bib \
  --static-export-dir ./dist/paper-static \
  --output-db ./dist/paper_snapshot_v2.db

# Schema-only migration (no BibTeX enrichment)
uv run deepresearch-flow paper db snapshot migrate \
  --snapshot-db ./dist/paper_snapshot.db \
  --in-place
```

Features:
- **No data loss**: Uses `ALTER TABLE` to upgrade schema, preserving all papers
- **Timestamped backups**: Creates `.bak_YYYYMMDD_HHMMSS` backup files automatically
- **BibTeX enrichment**: Matches papers with BibTeX and extracts DOI metadata
- **Static export update**: Updates `paper_index.json` with DOI/BibTeX references
- **Beautiful output**: Rich tables showing schema changes and match statistics

The migrate command will:
1. Create a timestamped backup (unless `--no-backup` is used)
2. Add `doi` column to the `paper` table (if missing)
3. Create `paper_bibtex` table (if missing)
4. Match papers with BibTeX entries and populate DOI/BibTeX data
5. Update static export index with new metadata

**Alternative: Rebuild with Previous Snapshot**

If you need to rebuild from scratch while preserving identity continuity:

```bash
uv run deepresearch-flow paper db snapshot build \
  --input ./paper_infos_complete.json \
  --bibtex ./papers.bib \
  --output-db ./dist/paper_snapshot_v2.db \
  --static-export-dir ./dist/paper-static-v2 \
  --previous-snapshot-db ./dist/paper_snapshot.db
```

Notes:
- `--md-root`, `--md-translated-root`, and `--pdf-root` are optional for this rebuild.
- If a paper in current inputs already has DOI/BibTeX, current input wins; otherwise data can be inherited from `--previous-snapshot-db`.
- **Warning**: This approach only includes papers from the input JSON files, so ensure all papers are included to avoid data loss.

#### Supplement Missing Translations

If some papers are missing translations (e.g., `zh`), you can export and translate them:

```bash
# 1) Export papers missing Chinese translation (with file paths)
uv run deepresearch-flow paper db snapshot export-missing \
  --snapshot-db ./dist/paper_snapshot.db \
  --type translation \
  --lang zh \
  --static-export-dir ./dist/paper-static \
  --output-paths ./to_translate_paths.txt

# 2) Translate missing papers
uv run deepresearch-flow translator translate \
  --input ./docs \
  --target-lang zh \
  --model openai/gpt-4o-mini \
  --input-list ./to_translate_paths.txt \
  --output-dir ./docs_translated

# 3) Rebuild or supplement snapshot with new translations
uv run deepresearch-flow paper db snapshot build ...
# Or use snapshot supplement if only adding translations
```

Other useful export types:
- `--type source_md` - Papers without source markdown
- `--type pdf` - Papers without PDF
- `--type translation --lang zh` - Papers without Chinese translation

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

### Merge multiple databases (PDF + Markdown + BibTeX)

```bash
# 1) Copy PDFs into a single folder
rsync -av ./pdfs_a/ ./pdfs_merged/
rsync -av ./pdfs_b/ ./pdfs_merged/

# 2) Copy Markdown folders into a single folder
rsync -av ./md_a/ ./md_merged/
rsync -av ./md_b/ ./md_merged/

# 3) Merge JSON libraries
uv run deepresearch-flow paper db merge library \
  --inputs ./paper_infos_a.json \
  --inputs ./paper_infos_b.json \
  --output ./paper_infos_merged.json

# 4) Merge BibTeX files
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

### Merge BibTeX files

```bash
uv run deepresearch-flow paper db merge bibtex \
  -i ./library_a.bib \
  -i ./library_b.bib \
  -o ./library_merged.bib
```

Duplicate keys keep the entry with the most fields; ties keep the first input order.

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
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location ^~ /mcp {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  # SSE transport for MCP clients that require Server-Sent Events
  location ^~ /mcp-sse {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    chunked_transfer_encoding off;
    add_header X-Accel-Buffering no;
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

# Option A: basic mode (no advanced search routes)
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001

# Option B: enable advanced search fully from CLI
SEARCH_ACCESS_TOKEN=your-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --embed-db /data/paper_vectors \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

Or enable advanced search via `config.toml`:

```toml
[search]
advanced_enabled = true
vector_dir = "/data/paper_vectors"
access_token = "env:SEARCH_ACCESS_TOKEN"
```

```bash
export PAPER_DB_STATIC_BASE_URL="https://static.example.com"
export SEARCH_ACCESS_TOKEN="your-token"

# Option C: use config.search.vector_dir + config.search.access_token
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --config ./config.toml \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

Advanced-search startup rules:

- `--embed-db` has highest priority and overrides `config.search.vector_dir`.
- `--search-access-token` has highest token priority; otherwise the server reads `SEARCH_ACCESS_TOKEN`, then `config.search.access_token`.
- If `config.search.advanced_enabled = true` but neither `--embed-db` nor `config.search.vector_dir` is set, startup fails fast.
- If `config.search.advanced_enabled = false`, the server starts normally without LanceDB or token and simply does not mount the advanced routes.

BibTeX metadata endpoint:

- `GET /api/v1/papers/{paper_id}/bibtex`
- Success payload: `{ paper_id, doi, bibtex_raw, bibtex_key, entry_type }`
- Error codes:
  - `paper_not_found`
  - `bibtex_not_found`

### 3.1) Admin API (Optional)

Enable the admin API to add or delete papers remotely via Bearer token authentication.

```bash
# Start API server with admin enabled
PAPER_DB_ADMIN_TOKEN=your-secret-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

Or pass the token via CLI flag: `--admin-token your-secret-token`

To receive semantic chunk syncs from `paper db api push --embed-db`, start the admin API with a remote LanceDB path as well:

```bash
PAPER_DB_ADMIN_TOKEN=your-secret-token \
uv run deepresearch-flow paper db api serve \
  --snapshot-db /data/paper_snapshot.db \
  --embed-db /data/paper_vectors \
  --cors-origin https://frontend.example.com \
  --host 0.0.0.0 --port 8001
```

If you prefer config-driven advanced search, `config.search.vector_dir` can provide the same remote LanceDB path; `--embed-db` still takes precedence when both are set.

Endpoints (all require `Authorization: Bearer <token>` header):

- `POST /api/v1/admin/papers` — Batch add papers (up to 200 per request)
  ```bash
  curl -X POST https://api.example.com/api/v1/admin/papers \
    -H "Authorization: Bearer your-secret-token" \
    -H "Content-Type: application/json" \
    -d '{"papers": [{"paper_title": "...", "paper_authors": [...], ...}]}'
  ```
  Response: `{ added, skipped, errors, paper_ids }`

- `DELETE /api/v1/admin/papers/{paper_id}` — Delete a paper and all its relations
  ```bash
  curl -X DELETE https://api.example.com/api/v1/admin/papers/{paper_id} \
    -H "Authorization: Bearer your-secret-token"
  ```
  Response: `{ deleted: true, paper_id }`

The paper JSON format is the same as `snapshot update` input. The admin API handles metadata insertion; static files can be pushed separately through `api push` when `remote.storage` is configured.

#### Push from Local DB to Remote

Use `api push` to merge a locally-built snapshot DB into a remote deployment:

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

```bash
# Preview what will be pushed
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --dry-run

# Push to remote
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml

# Push metadata + semantic LanceDB chunks to the remote admin API
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# Push only the admin API metadata
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --only-api

# Push only admin API metadata + semantic chunks
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --only-api

# Push only static storage assets
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --only-storage \
  --storage-concurrency 8

# Retry only failed static files from the last push
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --static-export-dir ./dist/paper-static \
  --config remote.toml \
  --retry-failed push-static-errors.json

# Retry only failed semantic batches from the last push
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --retry-failed push-semantic-errors.json

# Slice the paper list before pushing metadata + semantic chunks
uv run deepresearch-flow paper db api push \
  --snapshot-db ./dist/paper_snapshot.db \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --only-api \
  --start-idx 100 \
  --end-idx 200
```

- `--static-export-dir` is optional — when provided, summary JSON payloads are included so the remote side can build FTS indexes and preview text.
- `--embed-db` is optional — when provided, `api push` reads the local LanceDB snapshot and uploads semantic chunks after the metadata/static phases.
- `[remote.semantic]` tunes semantic sync batching and retries. Defaults are `max_rows = 25`, `max_payload_bytes = 4000000`, `timeout = 120`, `retries = 3`, and `retry_backoff_seconds = 2`.
- Duplicate papers (same `paper_id`) are automatically skipped.
- When `[remote.storage]` is configured, static files under the export dir are pushed after the metadata API sync.
- Remote semantic sync requires the server to run `paper db api serve` with admin auth enabled and a remote LanceDB path available via `--embed-db` or `config.search.vector_dir`.
- The currently supported storage backend is `webdav`.
- Static file push prints per-file status logs: `uploaded`, `skipped`, and `failed`.
- Semantic push now shows a `tqdm` progress bar by chunk and writes `push-semantic-errors.json` when a semantic batch still fails after retries.
- If some static uploads fail, a `push-static-errors.json` report is written and can be retried with `--retry-failed`.
- `--only-api` pushes only the admin API metadata and skips static storage.
- `--only-api` can be combined with `--embed-db` to push metadata plus semantic chunks only.
- `--only-storage` pushes only static storage and skips the admin API metadata step.
- `--embed-db` cannot be combined with `--only-storage`.
- `--storage-concurrency` controls the number of concurrent workers used for static storage push.
- `--only-api` and `--only-storage` are mutually exclusive.
- `--dry-run` cannot be combined with `--only-storage`.
- `--dry-run` silently skips semantic push even if `--embed-db` is provided.
- `--retry-failed` accepts either `push-static-errors.json` or `push-semantic-errors.json` and routes retry behavior by report type.
- Semantic retry via `api push --retry-failed push-semantic-errors.json` requires `--embed-db`.
- `--start-idx/--end-idx` use paper-list indices derived from the local snapshot export, not semantic chunk indices.
- `--start-idx/--end-idx` cannot be combined with `--only-storage`.
- If updated `summary` / `manifest` JSON behaves differently in one browser only, try a hard refresh or clear that browser's site cache first; stale browser cache can make static JSON appear inconsistent after a push.

#### Push Semantic Only from a Local Embed DB

Use `push-semantic` when you already have a local LanceDB semantic index and only want to sync semantic rows:

```bash
# Push all semantic groups from a local embed DB
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml

# Retry failed semantic batches only
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --retry-failed push-semantic-errors.json

# Select a semantic chunk window, then expand to full remote groups before push
uv run deepresearch-flow paper db api push-semantic \
  --embed-db ./dist/paper_vectors \
  --config remote.toml \
  --start-chunk-idx 1000 \
  --end-chunk-idx 2000
```

- `push-semantic` is semantic-only: it does not push metadata or static files.
- `--start-chunk-idx/--end-chunk-idx` are `0`-based, `end` is exclusive, and `-1` means “to the end”.
- Chunk-window selection is only used to choose groups; the command expands back to full `(doc_id, template_tag)` groups before pushing, so the remote semantic index is never updated with half a group.
- `--retry-failed` on `push-semantic` accepts semantic retry reports only.
- `--retry-failed` cannot be combined with `--start-chunk-idx/--end-chunk-idx`.

### 3.2) MCP (FastMCP Streamable HTTP + SSE)

This project exposes MCP servers mounted on the snapshot API:

- Streamable HTTP endpoint: `http://<host>:8001/mcp`
- SSE endpoint: `http://<host>:8001/mcp-sse`
- Transport behavior:
  - `/mcp`: Streamable HTTP via `POST` only (`GET` returns 405)
  - `/mcp-sse`: SSE-enabled transport (supports `GET` handshake)
- Protocol header: optional `mcp-protocol-version` (`2025-03-26` or `2025-06-18`)
- Static reads: summary/source/translation are served as **text content** by reading snapshot static assets (local-first via `PAPER_DB_STATIC_EXPORT_DIR`, HTTP fallback via `PAPER_DB_STATIC_BASE` / `PAPER_DB_STATIC_BASE_URL`)
- MCP auth: when enabled, clients must send `Authorization: Bearer <token>`.
  - Configure it with `--mcp-access-token` or `MCP_ACCESS_TOKEN`.
  - The MCP token, advanced-search token, and admin token are separate credentials that protect different surfaces.
  - `search_papers_semantic(...)` is gated only by the MCP surface, not by `SEARCH_ACCESS_TOKEN`. If advanced search is enabled and should not be exposed through MCP, set `MCP_ACCESS_TOKEN`.

Optional (avoid HTTP fetch by reading exported assets directly on the API host):

```bash
export PAPER_DB_STATIC_EXPORT_DIR=/data/paper-static
```

#### MCP Tools (API functions)

<details>
<summary><strong>search_papers(query, limit=10)</strong> — full-text search (relevance-ranked)</summary>

- Args:
  - `query` (str): keywords / topic query
  - `limit` (int): number of results (clamped to API max page size)
- Returns: list of `{ paper_id, title, year, venue, snippet_markdown }`

</details>

<details>
<summary><strong>search_papers_by_keyword(keyword, limit=10)</strong> — facet keyword search</summary>

- Args:
  - `keyword` (str): keyword substring
  - `limit` (int): number of results (clamped)
- Returns: list of `{ paper_id, title, year, venue, snippet_markdown }`

</details>

<details>
<summary><strong>search_papers_semantic(query, top_n=10, mmr_lambda=None, rerank="auto", filters=None)</strong> — full advanced semantic search payload</summary>

- Notes:
  - Requires advanced search to be configured on the snapshot API / MCP server
  - This MCP tool is protected by the MCP bearer token only; it does not additionally enforce `SEARCH_ACCESS_TOKEN`
  - Returns the same full payload shape as the advanced HTTP search pipeline
- Args:
  - `query` (str): raw semantic search query
  - `top_n` (int): number of final chunks to return
  - `mmr_lambda` (float | null): optional MMR lambda override; defaults to advanced search config
  - `rerank` (str): `auto`, `always`, or `never`
  - `filters` (dict | null): MCP-friendly filter map such as `{ "year": "2024", "venue": ["ICLR"] }`
- Returns: dict with `success`, `trace_id`, `query`, `results`, `metadata`, `degraded`, and `degradation`

</details>

<details>
<summary><strong>get_paper_metadata(paper_id)</strong> — metadata + available summary templates</summary>

- Args:
  - `paper_id` (str)
- Returns: dict with:
  - `paper_id`, `title`, `year`, `venue`
  - `doi`, `arxiv_id`, `openreview_id`, `paper_pw_url`
  - `has_bibtex`
  - `preferred_summary_template`, `available_summary_templates`

</details>

<details>
<summary><strong>get_paper_bibtex(paper_id)</strong> — persisted BibTeX payload</summary>

- Args:
  - `paper_id` (str)
- Returns: dict with:
  - `paper_id`, `doi`, `bibtex_raw`, `bibtex_key`, `entry_type`
- Errors:
  - `paper_not_found`
  - `bibtex_not_found`

</details>

<details>
<summary><strong>get_paper_summary(paper_id, template=None, max_chars=None)</strong> — summary JSON as raw text</summary>

- Notes:
  - Uses `preferred_summary_template` if `template` is omitted
  - Returns the **full JSON content** (not a URL)
  - When `max_chars` is omitted, the shared MCP default ceiling is `8000` even if server config raises `max_chars_default`
- Args:
  - `paper_id` (str)
  - `template` (str | null)
  - `max_chars` (int | null): truncation limit, must be positive if provided; omitted defaults to `8000`
- Returns: JSON string (may include a `[truncated: ...]` marker)

</details>

<details>
<summary><strong>get_paper_summary_keys(paper_id, template=None, max_depth=2, include_preview=False)</strong> — summary key paths in document order</summary>

- Notes:
  - Returns recursive summary key paths for the selected summary template
  - When `include_preview=True`, string previews are capped at `80` Unicode code points
- Args:
  - `paper_id` (str)
  - `template` (str | null)
  - `max_depth` (int): recursion depth limit
  - `include_preview` (bool): include short string previews for leaf strings
- Returns: dict with `paper_id`, `template`, `root_type`, and `paths`

</details>

<details>
<summary><strong>get_paper_summary_key(paper_id, key, template=None, max_chars=None)</strong> — one addressed summary node</summary>

- Notes:
  - Returns the selected summary subtree as text
  - `key` accepts dotted fields, indexed arrays like `items[0]`, and chained array indexes like `matrix[0][1]`
  - `max_chars` defaults to `8000`
- Args:
  - `paper_id` (str)
  - `key` (str): dotted path, with optional list indexes like `items[0]` or `matrix[0][1]`
  - `template` (str | null)
  - `max_chars` (int | null): must be positive if provided
- Returns: dict with `key`, `value_type`, `content_format`, `content`, `truncated`

</details>

<details>
<summary><strong>get_paper_source(paper_id, max_chars=None)</strong> — source markdown as raw text</summary>

- Notes:
  - When `max_chars` is omitted, the shared MCP default ceiling is `8000` even if server config raises `max_chars_default`
- Args:
  - `paper_id` (str)
  - `max_chars` (int | null): truncation limit, must be positive if provided; omitted defaults to `8000`
- Returns: markdown string (may include a `[truncated: ...]` marker)

</details>

<details>
<summary><strong>get_paper_source_outline(paper_id)</strong> — source markdown outline as section ranges</summary>

- Args:
  - `paper_id` (str)
- Returns: dict with `paper_id`, `total_lines`, and `sections` with `start_line` / `end_line`

</details>

<details>
<summary><strong>get_paper_source_lines(paper_id, start_line, end_line)</strong> — source markdown line range</summary>

- Args:
  - `paper_id` (str)
  - `start_line` (int): 1-based inclusive start
  - `end_line` (int): 1-based inclusive end
- Returns: dict with `paper_id`, `start_line`, `end_line`, `actual_start_line`, `actual_end_line`, `total_lines`, `content`

</details>

<details>
<summary><strong>get_paper_translation_outline(paper_id, lang)</strong> — translated markdown outline as section ranges</summary>

- Args:
  - `paper_id` (str)
  - `lang` (str): language code such as `zh` or `ja`
- Returns: dict with `paper_id`, `lang`, `total_lines`, and `sections`

</details>

<details>
<summary><strong>get_paper_translation_lines(paper_id, lang, start_line, end_line)</strong> — translated markdown line range</summary>

- Args:
  - `paper_id` (str)
  - `lang` (str): language code such as `zh` or `ja`
  - `start_line` (int): 1-based inclusive start
  - `end_line` (int): 1-based inclusive end
- Returns: dict with `paper_id`, `lang`, `start_line`, `end_line`, `actual_start_line`, `actual_end_line`, `total_lines`, `content`

</details>

<details>
<summary><strong>get_database_stats()</strong> — snapshot-level stats</summary>

- Returns:
  - `total`
  - `years`, `months`: list of `{ value, paper_count }`
  - `authors`, `venues`, `institutions`, `keywords`, `tags`: top lists of `{ value, paper_count }`

</details>

<details>
<summary><strong>list_top_facets(category, limit=20)</strong> — top values for one facet</summary>

- Args:
  - `category`: `author | venue | keyword | institution | tag`
  - `limit` (int)
- Returns: list of `{ value, paper_count }`

</details>

<details>
<summary><strong>filter_papers(author=None, venue=None, year=None, keyword=None, tag=None, limit=10)</strong> — structured filtering</summary>

- Args (all optional except `limit`):
  - `author`, `venue`, `keyword`, `tag`: substring match
  - `year`: exact match
  - `limit` (int): number of results (clamped)
- Returns: list of `{ paper_id, title, year, venue }`

</details>

#### MCP Resources (URI access)

<details>
<summary><strong>paper://{paper_id}/metadata</strong> — metadata JSON</summary>

Returns the same content as `get_paper_metadata(paper_id)` (as a JSON string).

</details>

<details>
<summary><strong>paper://{paper_id}/summary</strong> — preferred summary JSON</summary>

Returns the same content as `get_paper_summary(paper_id)` (preferred template; JSON string).

</details>

<details>
<summary><strong>paper://{paper_id}/summary/{template}</strong> — summary JSON for template</summary>

Returns the same content as `get_paper_summary(paper_id, template=template)` (JSON string).

</details>

<details>
<summary><strong>paper://{paper_id}/source</strong> — source markdown</summary>

Returns the same content as `get_paper_source(paper_id)` (markdown string).

</details>

<details>
<summary><strong>paper://{paper_id}/translation/{lang}</strong> — translated markdown</summary>

Returns translated markdown for `lang` (e.g. `zh`, `ja`) when available.

</details>

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
- Multi-document Scheduler: documents, retries, and fallback stages now run through separate worker queues.
- Concurrency Controls: use `--document-window`, `--initial-workers`, `--retry-workers`, and provider-level `--main-concurrency` / `--retry-concurrency` / fallback concurrency flags. `--max-concurrency` is an optional total cap; when omitted, it defaults to the sum of enabled stage concurrencies.
- Config Defaults: put `model` / `retry_model` / `fallback_model` / `fallback_model_2` and the same scheduler defaults in `[translator_config]` inside `config.toml`.
- Backward Compatibility: `--group-concurrency` is deprecated and maps to `--initial-workers`.

```bash
# Translate with structure protection, OCR repairs, and concurrent scheduling
uv run deepresearch-flow translator translate \
  --input ./papers \
  --target-lang ja \
  --fix-level aggressive \
  --document-window 8 \
  --initial-workers 4 \
  --retry-workers 2 \
  --main-concurrency 4 \
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
- Retry failed stages: use `--retry-failed-stages` to re-run only failed stages (multi-stage templates); missing stages are forced to run. Sequential retry plans enqueue only stages that still need execution, and the final `paper_infos.json` stays aligned with the final `errors.json` (documents with unresolved errors are omitted from output until fixed).

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
# Basic mode: no advanced-search env vars
docker run --rm -p 8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest

# Embedded mode: set at least two advanced env vars
docker run --rm -p 8899:8899 \
  -v $(pwd)/paper_snapshot.db:/db/papers.db \
  -v $(pwd)/paper-static:/static \
  -v $(pwd)/paper_vectors:/db/paper_vectors \
  -v $(pwd)/config.toml:/app/config.toml:ro \
  -e PAPER_DB_EMBED_DB=/db/paper_vectors \
  -e PAPER_DB_CONFIG=/app/config.toml \
  -e SEARCH_ACCESS_TOKEN=your-token \
  ghcr.io/nerdneilsfield/deepresearch-flow:deploy-latest
```

Notes:
- nginx listens on 8899 and proxies `/api`, `/mcp`, and `/mcp-sse` to the internal API at `127.0.0.1:8000`.
- Mount your snapshot DB to `/db/papers.db` inside the container.
- Mount snapshot static assets to `/static` when serving assets from this container (default `PAPER_DB_STATIC_BASE` is `/static`).
- If `PAPER_DB_STATIC_BASE` is a full URL (e.g. `https://static.example.com`), nginx still serves the frontend locally, while API responses use that external static base for asset links.
- `scripts/docker/start-api.sh` switches mode by counting advanced env vars: `PAPER_DB_EMBED_DB`, `PAPER_DB_CONFIG`, `SEARCH_ACCESS_TOKEN`.
- `0` set → basic mode.
- `1` set → fail fast as partial advanced configuration.
- `>=2` set → embedded mode; the script passes `--embed-db` and `--config` when present, and `SEARCH_ACCESS_TOKEN` is consumed via the existing CLI envvar.

Docker Compose example (four profiles):

```bash
docker compose -f scripts/docker/docker-compose.example.yml --profile local-static up
# or
docker compose -f scripts/docker/docker-compose.example.yml --profile external-static up
# or
docker compose -f scripts/docker/docker-compose.example.yml --profile local-static-advanced up
# or
docker compose -f scripts/docker/docker-compose.example.yml --profile external-static-advanced up
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
- Weighted model routing via `main_model`, weighted URL routing via `providers[].base[]`, and weighted key routing via `providers[].base[].key[]`.
- Request-time route pooling: real LLM requests pull routes from a shared runtime pool, so weighted `model -> base -> key` selection happens per request, not just once at process startup.
- Model Routing: `--model` accepts a single `provider/model`, an inline JSON model pool, or an `@file` JSON model pool. If omitted in `paper extract`, the command falls back to `config.toml` `main_model`.
- Environment Variables: keep secrets safe using `env:VAR_NAME` syntax.

Examples:

```bash
# Use config.toml main_model
uv run deepresearch-flow paper extract --input ./docs

# Fixed model
uv run deepresearch-flow paper extract --input ./docs --model openai/gpt-4o-mini

# Inline weighted main_model override
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model '[{"model":"openai/gpt-4o-mini","weight":4},{"model":"claude/claude-sonnet-4-5-20250929","weight":1}]'

# File-based weighted main_model override
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model @main_model.json
```

Mode probing:

```bash
# Report only
uv run deepresearch-flow utils test-mode \
  --config ./config.toml \
  --model openai/gpt-4o-mini

# Write probe results back to config
uv run deepresearch-flow utils test-mode \
  --config ./config.toml \
  --model openai/gpt-4o-mini \
  --write-back
```

`utils test-mode` probes one weighted `base + key` path per requested model. Normal extraction, translation, recognize repair, and tag-generation flows now select routes from the runtime pool per request.

See `config.example.toml` for a full reference.

---

<p align="center">
  Built with love for the Open Science community.
</p>
