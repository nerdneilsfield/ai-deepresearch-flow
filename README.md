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

## Paper extraction

```bash
# Scan a directory recursively (default: *.md)
deepresearch-flow paper extract \
  --input ./docs \
  --model openai/gpt-4o-mini

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
```

Outputs:

- Aggregated JSON: `paper_infos.json`
- Errors: `paper_errors.json`

Incremental behavior:

- The extractor reuses existing entries when `source_path` and `source_hash` match.
- Use `--force` to re-extract everything.
- Use `--retry-failed` to retry only failed documents listed in `paper_errors.json`.

## Paper database commands

```bash
# Render Markdown from JSON
deepresearch-flow paper db render-md --input paper_infos.json

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
```

Other commands:

- `append-bibtex`
- `sort-papers`
- `split-by-tag`
- `split-database`
- `statistics`
- `merge`
