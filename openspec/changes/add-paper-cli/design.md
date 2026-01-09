## Context
The existing scripts under `ref/` implement two Click-based CLIs:
- `paper_info_extractor.py`: extracts structured info from markdown files, but has a hard-coded schema and uses only DashScope.
- `paper_info_database_process.py`: manages/operates on the extracted JSON database and also depends on DashScope for tagging.

This change integrates both CLIs into this repo as a cohesive `deepresearch-flow paper ...` command group and expands provider support to:
- Ollama native API (`/api/chat`)
- OpenAI-compatible HTTP APIs (OpenAI/OpenRouter/DeepSeek-style endpoints)
- DashScope SDK (Qwen models)

## Goals / Non-Goals
- Goals:
  - Schema-driven extraction using JSON Schema (primary), with prompt fallback when needed.
  - Explicit model routing via `--model provider/model` (no hidden default inference).
  - Async, high-throughput provider calls; support multiple API keys per provider for higher QPS.
  - Safe large-scale processing: incremental/idempotent extraction, bounded concurrency, and structured error persistence.
  - Aggregated JSON database output by default; optional per-doc outputs via `--split`.
  - Markdown output is rendered locally from JSON via templates (not model-generated Markdown).
  - Reduce accidental spend: support dry-run file counting and optional cost estimation.
- Non-Goals:
  - Perfect portability of every legacy output field from `ref/` without migration rules.
  - Adding streaming responses (not required for now).

## Decisions
- CLI:
  - Keep Click for compatibility with the existing scripts.
  - Command layout:
    - `deepresearch-flow paper extract`
    - `deepresearch-flow paper db ...`
- Code location:
  - New production package lives under `python/`.
  - `ref/` remains reference-only and stays ignored in git.
- Config:
  - Config file is TOML and defaults to `config.toml`.
  - Provider selection is always explicit in CLI: `--model provider/model`.
  - Config supports both literal keys and `env:VAR` indirection.
  - Config is validated at load time; missing required fields fail fast.
- Schema:
  - The extraction schema is a JSON Schema file path, with a built-in default schema when none is specified.
  - Validation requires at least `paper_title` and `paper_authors` at the top level.
- Providers:
  - `ollama`: call native `/api/chat` with model name and messages.
  - `openai_compatible`: call `/v1/chat/completions`-style endpoints via async HTTP.
  - `dashscope`: call DashScope SDK with message-based requests.
  - Providers may include extra headers and multiple API keys; choose keys round-robin.
  - Providers and/or the orchestrator enforce bounded concurrency and retry/backoff on rate limits.
- Extraction strategy:
  - Provider config can declare/choose a structured output strategy (JSON mode) vs prompt-based extraction.
  - Always validate JSON locally against the JSON Schema regardless of provider features.
- Output:
  - Aggregated JSON is always written (default `paper_infos.json`).
  - Extraction is incremental by default when `paper_infos.json` exists; unchanged sources are skipped.
  - Failures are persisted to `paper_errors.json` to enable targeted retries.
  - `--split` writes per-document JSON; file naming uses:
    - the markdown filename (without extension) for typical files
    - the parent directory name when the markdown filename is `output.md` (common dataset layout)
    - short hash suffix to avoid collisions.
- Safety controls:
  - `--dry-run` lists discovered files without calling providers.
  - Input truncation is applied when content exceeds configured limits to avoid provider context errors.
- Markdown rendering:
  - Separate DB command renders Markdown from JSON using Jinja2 templates, so the model output remains strict JSON.
  - A built-in default template is provided for quick viewing, with the option to supply custom templates.

## Risks / Trade-offs
- OpenAI-compatible providers differ in feature support; structured-output features may not be available everywhere.
  - Mitigation: always validate JSON locally against JSON Schema; keep prompt-based fallback.
- File naming collisions when scanning directories.
  - Mitigation: deterministic short hash derived from the source path.
- Large batch runs can hit provider rate limits and generate noisy failures.
  - Mitigation: bounded concurrency, backoff retries, and persistent error reporting for targeted reruns.

## Open Questions
- Exact list of DB subcommands to port first (can be prioritized in implementation).
