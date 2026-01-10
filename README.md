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

## Paper extraction

```bash
# Scan a directory recursively (default: *.md)
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini

# Scan multiple inputs in one run
deepresearch-flow paper extract \
  --input ./docs \
  --input ./more-docs \
  --model openai/gpt-4o-mini

# Use built-in prompt template and output language
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template deep_read \
  --language zh

# Use custom prompt + schema
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-system ./prompts/system.j2 \
  --prompt-user ./prompts/user.j2 \
  --schema-json ./prompts/schema.json

# Use a full template directory
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --template-dir ./prompts

# Filter with glob
deepresearch-flow paper extract \
  --input ./docs \
  --glob '**/output.md' \
  --model ollama/llama3.1

# Dry run
deepresearch-flow paper extract \
  --input ./docs \
  --dry-run \
  --model openai/gpt-4o-mini

# Split per-document outputs
deepresearch-flow paper extract \
  --input ./docs \
  --split \
  --model openai/gpt-4o-mini

# Extract and render markdown outputs in one run
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --render-md \
  --render-template-name simple \
  --render-output-dir rendered_md

# Extract + render using the same built-in template (defaults to prompt template)
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --prompt-template eight_questions \
  --render-md

# Render output defaults to the --output parent directory
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini \
  --output ./out/papers.json \
  --prompt-template eight_questions \
  --render-md
```

Outputs:

- Aggregated JSON: `paper_infos.json`
- Errors: `paper_errors.json`
- Optional rendered Markdown: `rendered_md/` by default (use `--render-md`)

Incremental behavior:

- The extractor reuses existing entries when `source_path` and `source_hash` match.
- Use `--force` to re-extract everything.
- Use `--retry-failed` to retry only failed documents listed in `paper_errors.json`.
- Use `--verbose` for detailed logs alongside progress bars.
- Extract-time rendering defaults to the same built-in template as `--prompt-template`.
- If `--template-dir` is used for prompts, `--render-md` reuses its `render.j2` unless overridden.
- When `--output` is provided, render output defaults to the parent directory of that file unless `--render-output-dir` is set.

## Paper database commands

```bash
# Render Markdown from JSON
deepresearch-flow paper db render-md --input paper_infos.json

# Render with a built-in template and language fallback
deepresearch-flow paper db render-md \
  --input paper_infos.json \
  --template-name deep_read \
  --language zh

# Render with a custom markdown template
deepresearch-flow paper db render-md \
  --input paper_infos.json \
  --markdown-template ./prompts/render.j2

# Render using render.j2 from a template directory
deepresearch-flow paper db render-md \
  --input paper_infos.json \
  --template-dir ./prompts

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
# Each input JSON must be: {"template_tag": "simple", "papers": [...]}
deepresearch-flow paper db serve \
  --input paper_infos_simple.json \
  --input paper_infos_deep_read.json \
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

Other commands:

- `append-bibtex`
- `sort-papers`
- `split-by-tag`
- `split-database`
- `statistics`
- `merge`
