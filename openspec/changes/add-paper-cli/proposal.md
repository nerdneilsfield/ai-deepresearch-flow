# Change: Add paper extraction + DB CLI

## Why
The repo already contains a first version of a paper/document structured information extraction tool under `ref/`, but it is not integrated into the project and is limited to a single model provider implementation.

We want to make it a first-class part of this project and expand provider support (Ollama + OpenAI-compatible API) while keeping the extracted structure schema-driven and configurable.

## What Changes
- Add a CLI entrypoint `deepresearch-flow` with `paper` subcommands.
- Implement `deepresearch-flow paper extract`:
  - Accept markdown inputs from file(s) and/or directory recursion.
  - Support filtering with glob patterns.
  - Default to incremental/idempotent processing by reusing existing `paper_infos.json` when present.
  - Provide safety controls: `--dry-run`, bounded concurrency, retry/backoff.
  - Produce an aggregated JSON database by default (`paper_infos.json`).
  - Optionally generate per-document outputs via `--split`.
  - Persist structured failures to `paper_errors.json` and support `--retry-failed`.
- Implement `deepresearch-flow paper db ...` commands:
  - Port the existing database management commands (sort/statistics/filter/split/merge/tagging).
  - Add a Markdown rendering command that renders from JSON + templates (not model-generated Markdown).
- Add a provider system driven by `config.toml`:
  - `ollama` provider using the native Ollama API.
  - `openai_compatible` provider for OpenAI/OpenRouter/DeepSeek-like endpoints.
  - API key resolution supports both literal keys and `env:VAR` indirection.
- Adopt JSON Schema as the primary extraction schema format; validate required keys at config load time.
- Provide a built-in default extraction schema and a built-in Markdown template for quick start.
- Update Python support to `>= 3.12` to avoid pinning to bleeding-edge versions.

## Impact
- Affected specs: `paper` (new)
- Affected code: new package under `python/`, new CLI entrypoint, new config template (`config.example.toml`)
- Data compatibility: the aggregated JSON remains list-based for easy processing, but schema field names are standardized (`paper_title`, `paper_authors`).
