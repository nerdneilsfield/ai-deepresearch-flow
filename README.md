# deepresearch-flow

DeepResearch Flow command-line tools for document extraction, OCR post-processing, and paper database operations.

## Quick Start

```bash
pip install deepresearch-flow
# or
uv pip install deepresearch-flow

# Development install
pip install -e .

cp config.example.toml config.toml

# Extract from a docs folder
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini

# Serve a local UI
uv run deepresearch-flow paper db serve \
  --input ./paper_infos_simple.json \
  --host 127.0.0.1 \
  --port 8000
```

Docker images:

```bash
docker run --rm -it nerdneils/deepresearch-flow --help
# or
docker run --rm -it ghcr.io/nerdneilsfield/deepresearch-flow --help
```

## Commands

`deepresearch-flow` is the top-level CLI. Workflows live under `paper` and `recognize`.
Use `deepresearch-flow --help`, `deepresearch-flow paper --help`, and `deepresearch-flow recognize --help` to explore flags.

<details>
<summary>Configuration details</summary>

Copy `config.example.toml` to `config.toml` and edit providers.

- Providers are configured under `[[providers]]`.
- Use `api_keys = ["env:OPENAI_API_KEY"]` to read from environment variables.
- `model_list` is required for each provider and controls allowed `provider/model` values.
- Explicit model routing is required: `--model provider/model`.
- Supported provider types: `ollama`, `openai_compatible`, `dashscope`, `gemini_ai_studio`, `gemini_vertex`, `azure_openai`, `claude`.
- Provider-specific fields: `azure_openai` requires `endpoint`, `api_version`, `deployment`; `gemini_vertex` requires `project_id`, `location`; `claude` requires `anthropic_version`.
- `claude` providers optionally accept `max_tokens` to control translation output length.
- Built-in prompt templates for extraction: `simple`, `deep_read`, `eight_questions`, `three_pass`.
- Template rename: `seven_questions` is now `eight_questions`.
- Render templates use `paper db render-md --template-name` with the same names.
- `--language` defaults to `en`; extraction stores it as `output_language` and render uses that field.
- When `output_language` is `zh`, render headings include both Chinese and English.
- Complex templates (`deep_read`, `eight_questions`, `three_pass`) run multi-stage extraction and persist per-document stage files under `paper_stage_outputs/`.
- Custom templates: use `--prompt-system`/`--prompt-user` with `--schema-json`, or `--template-dir` containing `system.j2`, `user.j2`, `schema.json`, `render.j2`.
- Custom templates run in single-stage extraction mode.
- Built-in schemas require `publication_date` and `publication_venue`.
- The `simple` template requires `abstract`, `keywords`, and a single-paragraph `summary` that covers the eight-question aspects.
- Extraction tolerates minor JSON formatting errors and ignores extra top-level fields when required keys validate.

</details>

<details>
<summary>paper extract — structured extraction from markdown</summary>

Extract structured JSON from markdown files using configured providers and prompt templates.

Key options:

- `--input` (repeatable): file or directory input.
- `--glob`: filter when scanning directories.
- `--prompt-template` / `--language`: select built-in prompts and output language.
- `--prompt-system` / `--prompt-user` / `--schema-json`: custom prompt + schema.
- `--template-dir`: use a directory containing `system.j2`, `user.j2`, `schema.json`, `render.j2`.
- `--sleep-every` / `--sleep-time`: throttle request initiation.
- `--max-concurrency`: override concurrency.
- `--render-md`: render markdown output as part of extraction.
- `--dry-run`: scan inputs and show summary metrics without calling providers.

Outputs:

- Aggregated JSON: `paper_infos.json`
- Errors: `paper_errors.json`
- Optional rendered Markdown: `rendered_md/` by default

Incremental behavior:

- Reuses existing entries when `source_path` and `source_hash` match.
- Use `--force` to re-extract everything.
- Use `--retry-failed` to retry only failed documents listed in `paper_errors.json`.
- Use `--verbose` for detailed logs alongside progress bars.
- Extract-time rendering defaults to the same built-in template as `--prompt-template`.
- Output JSON is written as `{"template_tag": "...", "papers": [...]}`.
- A summary table prints input/prompt/output character totals, token estimates, and throughput after each run.
- Progress bars include a live prompt/completion/total token ticker.

Examples:

```bash
# Scan a directory recursively (default: *.md)
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini

# Multiple inputs + custom output
deepresearch-flow paper extract \
  --input ./docs \
  --input ./more-docs \
  --output ./out/papers.json \
  --model openai/gpt-4o-mini

# Built-in template with output language
deepresearch-flow paper extract \
  --input ./docs \
  --prompt-template deep_read \
  --language zh \
  --model openai/gpt-4o-mini

# Custom template directory
deepresearch-flow paper extract \
  --input ./docs \
  --template-dir ./prompts \
  --model openai/gpt-4o-mini

# Extract + render in one run
deepresearch-flow paper extract \
  --input ./docs \
  --prompt-template eight_questions \
  --render-md \
  --model openai/gpt-4o-mini

# Throttle request initiation
deepresearch-flow paper extract \
  --input ./docs \
  --sleep-every 10 \
  --sleep-time 60 \
  --model openai/gpt-4o-mini
```

</details>

<details>
<summary>paper db — render, analyze, and serve extracted data</summary>

Render outputs, compute stats, and serve a local web UI over paper JSON.

JSON input formats:

- For `db render-md`, `db statistics`, `db filter`, and `db generate-tags`, the input can be either an aggregated JSON list or `{"template_tag": "...", "papers": [...]}` (the commands operate on `papers`).
- For `db serve`, each input JSON must be an object: `{"template_tag": "simple", "papers": [...]}`.
  When `template_tag` is missing, the server attempts to infer it as a fallback (legacy list-only inputs are rejected).

Web UI highlights:

- Summary/Source/Translated/PDF/PDF Viewer views with tab navigation.
- Split view: choose left/right panes independently (summary/source/translated/pdf/pdf viewer) via URL params.
- Summary/Source/Translated views include a collapsible outline panel (top-left) and a back-to-top control (bottom-left).
- Translated view renders `<base>.<lang>.md` files under `--md-translated-root` (defaults to `zh` when available, with a language dropdown).
- Summary template dropdown shows only available templates per paper.
- Homepage filters: PDF/Source/Translated/Summary availability and template tags, plus a filter syntax input (`tmpl:...`, `has:pdf`, `no:source`, `translated:yes`).
- Homepage stats: total and filtered counts for PDF/Source/Translated/Summary plus per-template totals.
- Stats page includes keyword frequency charts.
- Source view renders Markdown and supports embedded HTML tables plus `data:image/...;base64` `<img>` tags (images are constrained to the content width).
- PDF Viewer is served locally (PDF.js viewer assets) to avoid cross-origin issues with local PDFs.
- PDF-only entries are surfaced for unmatched PDFs under `--pdf-root` (metadata title if available, otherwise filename), with badges and detail warnings.
- PDF-only entries are excluded from stats counts.
- Merge behavior for multi-input serve: title similarity (>= 0.95), preferring `bibtex.fields.title` and falling back to `paper_title`.
- Cache merged inputs with `--cache-dir`; bypass with `--no-cache`.

Examples:

```bash
# Render Markdown from JSON
deepresearch-flow paper db render-md --input paper_infos.json

# Render with a built-in template and language fallback
deepresearch-flow paper db render-md \
  --input paper_infos.json \
  --template-name deep_read \
  --language zh

# Generate tags
deepresearch-flow paper db generate-tags \
  --input paper_infos.json \
  --output paper_infos_with_tags.json \
  --model openai/gpt-4o-mini

# Filter papers
deepresearch-flow paper db filter \
  --input paper_infos.json \
  --output filtered.json \
  --tags hardware_acceleration,fpga

# Statistics (rich tables)
deepresearch-flow paper db statistics \
  --input paper_infos.json \
  --top-n 20
# Statistics also include keyword frequency (normalized to lowercase)

# Serve a local read-only web UI (loads charts/libs via CDN)
deepresearch-flow paper db serve \
  --input paper_infos_simple.json \
  --input paper_infos_deep_read.json \
  --cache-dir .cache/db-serve \
  --host 127.0.0.1 \
  --port 8000

# Serve with optional BibTeX enrichment and source roots
deepresearch-flow paper db serve \
  --input paper_infos_simple.json \
  --input paper_infos_deep_read.json \
  --bibtex ./refs/library.bib \
  --md-root ./docs \
  --md-translated-root ./translations \
  --md-root ./more_docs \
  --pdf-root ./pdfs \
  --cache-dir .cache/db-serve \
  --host 127.0.0.1 \
  --port 8000
```

Web search syntax (Scholar-style):

- Default is AND: `fpga kNN`
- Quoted phrases: `title:"nearest neighbor"`
- OR: `fpga OR asic`
- Negation: `-survey` or `-tag:survey`
- Fields: `title:`, `author:`, `tag:`, `venue:`, `year:`, `month:` (content tags only)
- Year range: `year:2020..2024`

Other database helpers:

- `append-bibtex`
- `sort-papers`
- `split-by-tag`
- `split-database`
- `statistics`
- `merge`

</details>

<details>
<summary>translator translate — OCR markdown translation</summary>

Translate OCR-produced Markdown into another language while preserving structure.

Key options:

- `--input` (repeatable): file or directory input.
- `--glob`: filter when scanning directories.
- `--model`: `provider/model` routing (same providers as `paper extract`).
- `--fallback-model`: optional fallback `provider/model` for failed nodes after retries.
- `--target-lang`: target language hint (default `zh`).
- `--fix-level`: OCR repair (`off`, `moderate`, `aggressive`).
- `--max-chunk-chars`: max chars per translation chunk.
- `--max-concurrency`: max concurrent provider requests.
- `--dump-protected` / `--dump-placeholders` / `--dump-nodes`: optional debug outputs.
- Existing outputs are skipped by default; use `--force` to overwrite.
- Runs `rumdl fmt` before and after translation to normalize Markdown (disable with `--no-format`).

Outputs:

- Translated Markdown files with a language suffix (e.g. `paper.zh.md`, `paper.ja.md`).

Examples:

```bash
# Translate a directory (default: zh suffix)
deepresearch-flow translator translate \
  --input ./ocr_md \
  --model openai/gpt-4o-mini

# Translate to Japanese with OCR repairs enabled
deepresearch-flow translator translate \
  --input ./ocr_md \
  --target-lang ja \
  --fix-level moderate \
  --model openai/gpt-4o-mini

# Retry failed nodes with a fallback model
deepresearch-flow translator translate \
  --input ./ocr_md \
  --model openai/gpt-4o-mini \
  --fallback-model zhipu/glm-4.7

# Write outputs into a separate directory and dump placeholders
deepresearch-flow translator translate \
  --input ./ocr_md \
  --output-dir ./translated_md \
  --dump-placeholders \
  --model openai/gpt-4o-mini
```

</details>

<details>
<summary>recognize md — embed or unpack markdown images</summary>

`recognize md embed` replaces local image links in markdown with `data:image/...;base64,` URLs.
`recognize md unpack` extracts embedded images into `images/` and updates markdown links.

Key options:

- `--input` (repeatable): file or directory input.
- `--recursive`: recurse into directories.
- `--output`: output directory (flattened outputs).
- `--enable-http`: allow embedding HTTP(S) images (embed only).
- `--workers`: concurrent workers (default: 4).
- `--dry-run`: report planned outputs without writing files.
- `--verbose`: enable detailed logs for image resolution/HTTP fetches.

 Notes:

- Progress bars report completion; a rich summary table lists counts, image totals, duration, and output locations.
- Summary paths are shown relative to the current working directory when possible.
- If the output directory is not empty, the command logs a warning before writing files.

Examples:

```bash
# Embed local images (flatten outputs)
deepresearch-flow recognize md embed \
  --input ./docs \
  --recursive \
  --output ./out_md

# Embed HTTP images (with browser User-Agent)
deepresearch-flow recognize md embed \
  --input ./docs \
  --enable-http \
  --output ./out_md

# Unpack embedded images into output/images/
deepresearch-flow recognize md unpack \
  --input ./docs \
  --recursive \
  --output ./out_md
```

</details>

<details>
<summary>recognize organize — flatten OCR outputs</summary>

Organize OCR outputs (layout: `mineru`) into flat markdown files, with optional image embedding.

Key options:

- `--layout`: OCR layout type (currently `mineru`).
- `--input` (repeatable): directories containing `full.md` + `images/`.
- `--recursive`: search for layout folders (required when inputs contain nested result directories).
- `--output-simple`: copy markdown + images to output (shared `images/`).
- `--output-base64`: embed images into markdown.
- `--workers`: concurrent workers (default: 4).
- `--dry-run`: report planned outputs without writing files.
- `--verbose`: enable detailed logs for layout discovery and file copying.

 Notes:

- Use `--recursive` when the input directory contains nested layout folders (otherwise no layouts are discovered).
- If output directories are not empty, the command logs a warning before writing files.
- A summary table lists counts, image totals, duration, and output locations after completion.
- Summary paths are shown relative to the current working directory when possible.

Examples:

```bash
# Copy markdown + images into a flat output directory
deepresearch-flow recognize organize \
  --layout mineru \
  --input ./ocr_results \
  --recursive \
  --output-simple ./out_simple

# Embed images into markdown
deepresearch-flow recognize organize \
  --layout mineru \
  --input ./ocr_results \
  --output-base64 ./out_base64
```

</details>

<details>
<summary>Data formats (examples)</summary>

Aggregated extraction output is a JSON list:

```json
[
  {
    "paper_title": "Example Paper",
    "paper_authors": ["Author A", "Author B"],
    "publication_date": "2024-01-01",
    "publication_venue": "ExampleConf",
    "source_path": "/abs/path/to/doc.md"
  }
]
```

`db serve` expects each input to be an object with a `template_tag` and a `papers` list:

```json
{
  "template_tag": "simple",
  "papers": [
    {
      "paper_title": "Example Paper",
      "paper_authors": ["Author A"],
      "publication_date": "2024-01-01",
      "publication_venue": "ExampleConf"
    }
  ]
}
```

</details>
