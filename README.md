# ai-deepresearch-flow

Paper/document extraction and database tooling for the DeepResearch flow.

## Requirements

- Python >= 3.12

## Setup

```bash
pip install -e .
# or
uv pip install -e .
```

## Configuration

Copy `config.example.toml` to `config.toml` and edit providers.

- Providers are configured under `[[providers]]`.
- Use `api_keys = ["env:OPENAI_API_KEY"]` to read from environment variables.
- `model_list` is required for each provider and controls allowed `provider/model` values.
- Explicit model routing is required: `--model provider/model`.
- Supported provider types: `ollama`, `openai_compatible`, `dashscope`, `gemini_ai_studio`, `gemini_vertex`, `azure_openai`, `claude`.
- Provider-specific fields: `azure_openai` requires `endpoint`, `api_version`, `deployment`; `gemini_vertex` requires `project_id`, `location`; `claude` requires `anthropic_version`.
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

## Commands

`deepresearch-flow` exposes multiple subcommands; the `paper` workflow is one of them.
The details below live in collapsible sections so the README stays compact.

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

- For `db render-md`, `db statistics`, `db filter`, and `db generate-tags`, the input is the aggregated JSON list.
- For `db serve`, each input JSON must be an object: `{"template_tag": "simple", "papers": [...]}`.
  When `template_tag` is missing, the server attempts to infer it as a fallback.

Web UI highlights:

- Summary/Source/PDF views with tab navigation.
- Summary template dropdown shows only available templates per paper.
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
- Fields: `title:`, `author:`, `tag:`, `venue:`, `year:`, `month:`
- Year range: `year:2020..2024`

Other database helpers:

- `append-bibtex`
- `sort-papers`
- `split-by-tag`
- `split-database`
- `statistics`
- `merge`

</details>
