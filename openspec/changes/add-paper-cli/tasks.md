## 1. Packaging
- [x] 1.1 Update `pyproject.toml` to `requires-python >= 3.12`
- [x] 1.2 Add a Python package under `python/` and wire console script `deepresearch-flow`

## 2. Configuration
- [x] 2.1 Define `config.toml` format with `[[providers]]`
- [x] 2.2 Implement config loader + validation (including required schema keys)
- [x] 2.3 Support `api_key = "env:VAR"` and literal keys without leaking secrets
- [x] 2.4 Add `config.example.toml` and ignore `config.toml`
- [x] 2.5 Add config options for `max_concurrency`, retries/backoff, and truncation strategy
- [x] 2.6 Add per-provider extraction strategy config (structured mode vs prompt fallback + custom prompts)

## 3. Providers
- [x] 3.1 Implement `ollama` provider (native `/api/chat`)
- [x] 3.2 Implement `openai_compatible` provider (async HTTP via `httpx`)
- [x] 3.3 Support extra headers and multiple API keys per provider (round-robin)
- [x] 3.4 Implement `dashscope` provider (DashScope SDK)
- [x] 3.5 Implement retry + backoff for rate limits and transient errors

## 4. Paper extraction
- [x] 4.1 Implement input discovery: file/dir recursion + `--glob` filtering
- [x] 4.2 Add `--dry-run` to report discovered files and optional cost estimate
- [x] 4.3 Provide built-in default JSON Schema when none is specified
- [x] 4.4 Implement schema-driven extraction (JSON Schema primary; prompt fallback)
- [x] 4.5 Validate model JSON against JSON Schema and retry on invalid output
- [x] 4.6 Implement incremental mode: load existing `paper_infos.json`, compute `source_hash`, skip unchanged
- [x] 4.7 Add `--force` and `--retry-failed` controls for reprocessing
- [x] 4.8 Persist structured failures to `paper_errors.json`
- [x] 4.9 Apply context truncation per config before provider calls
- [x] 4.10 Write aggregated output `paper_infos.json` (required)
- [x] 4.11 Implement `--split` per-document outputs with stable naming + short hash for collisions

## 5. Paper DB commands
- [x] 5.1 Port existing commands from `ref/paper_info_database_process.py` into `deepresearch-flow paper db`
- [x] 5.2 Migrate `generate_tags` to use the new provider system
- [x] 5.3 Add `render-md` command: render JSON → Markdown using Jinja2 templates
- [x] 5.4 Ship a built-in default Markdown template

## 6. Documentation
- [x] 6.1 Add CLI usage examples to `README.md`
- [x] 6.2 Document `config.toml` fields and model selection (`provider/model`)
