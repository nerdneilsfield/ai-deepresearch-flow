[← Back to README](../../README.md)

# Reference

## 1. Translator: OCR-Safe Translation

The translator module is built for scientific documents using a node-based architecture.

- **Structure Protection**: automatically detects and "freezes" code blocks, LaTeX (`$$...$$`), HTML tables, and images before sending text to the LLM.
- **OCR Repair**: use `--fix-level` to merge broken paragraphs and convert text references (`[1]`) to clickable Markdown footnotes (`[^1]`). Levels: `off`, `light`, `moderate` (default), `aggressive`.
- **Context-Aware**: supports retries for failed chunks and falls back gracefully.
- **Multi-document Scheduler**: documents, retries, and fallback stages run through separate worker queues.
- **Concurrency Controls**: `--document-window`, `--initial-workers`, `--retry-workers`, `--main-concurrency` / `--retry-concurrency` / `--fallback-concurrency` / `--fallback-2-concurrency`. `--max-concurrency` is optional total cap.
- **Config Defaults**: put `model` / `retry_model` / `fallback_model` / `fallback_model_2` and scheduler defaults in `[translator_config]` in `config.toml`.
- **Backward Compatibility**: `--group-concurrency` is deprecated, maps to `--initial-workers`.

```bash
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

## 2. Paper Extract: Structured Knowledge

Turn loose markdown files into a queryable database.

- **Templates**: built-in prompts like `simple`, `eight_questions`, and `deep_read`.
- **Async and throttled**: control over `--max-concurrency`, `--sleep-every`, `--timeout`.
- **Incremental**: skips already processed files.
- **Stage resume**: multi-stage templates persist per-module outputs; `--force-stage <name>` to rerun a module.
- **Stage DAG**: enable `--stage-dag` for dependency-aware parallelism; `--dry-run` prints per-stage plan.
- **Diagram hints**: `deep_read` can emit inferred diagrams labeled `[Inferred]`; use `recognize fix-mermaid` if needed.
- **Stage focus**: multi-stage runs emphasize active module to reduce context overload.
- **Range filter**: `--start-idx/--end-idx` slice inputs; applies before `--retry-failed`/`--retry-failed-stages`.
- **Retry failed stages**: `--retry-failed-stages` re-runs only failed stages; missing stages forced.
- **Model routing**: `--model` accepts `provider/model`, inline JSON pool, or `@file`. Falls back to `config.toml` `main_model`.

```bash
uv run deepresearch-flow paper extract \
  --input ./library \
  --output paper_data.json \
  --template-dir ./my-custom-prompts \
  --max-concurrency 10 --timeout 180

# Range + retry
uv run deepresearch-flow paper extract \
  --input ./library \
  --start-idx 0 --end-idx 100 \
  --retry-failed \
  --model claude/claude-3-5-sonnet-20240620

# Retry failed stages in multi-stage templates
uv run deepresearch-flow paper extract \
  --input ./library \
  --retry-failed-stages \
  --model claude/claude-3-5-sonnet-20240620
```

## 3. Database and UI: Your Personal ArXiv

The db serve command creates a local research station.

- **Split View**: original PDF/Markdown on left, Summary/Translation on right.
- **Full Text Search**: `tag:fpga year:2023..2024`
- **Stats**: visualize publication trends and keyword frequencies.
- **PDF Viewer**: built-in PDF.js prevents cross-origin issues.

```bash
uv run deepresearch-flow paper db serve \
  --input paper_infos.json \
  --pdf-root ./pdfs \
  --cache-dir .cache/db
```

## 4. Paper DB Compare: Coverage Audit

Compare two datasets (A/B) to find missing PDFs, markdowns, translations, or JSON items.

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

## 5. Paper DB Extract: Matched Export

Extract matched JSON entries or translated Markdown after coverage comparison.

```bash
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-bibtex ./refs.bib \
  --pdf-root ./pdfs \
  --output-json ./matched.json --output-csv ./extract.csv

# Use a JSON reference list to filter the target JSON
uv run deepresearch-flow paper db extract \
  --json ./processed.json \
  --input-json ./reference.json \
  --pdf-root ./pdfs \
  --output-json ./matched.json --output-csv ./extract.csv

# Extract translated markdowns by language
uv run deepresearch-flow paper db extract \
  --md-root ./md_root \
  --md-translated-root ./translated \
  --lang zh \
  --output-md-translated-root ./translated_matched \
  --output-csv ./extract.csv
```

## 6. Recognize: OCR Post-Processing

Tools to clean up raw outputs from OCR engines like MinerU.

- **Embed Images**: convert local image links to Base64 for portable single-file Markdown.
- **Extract Images**: convert local image links to Base64 for portable single-file Markdown.
- **Organize**: flatten nested OCR output directories.
- **Fix**: apply OCR fixes and rumdl formatting.
- **Fix JSON**: apply same fixes to markdown fields inside paper JSON.
- **Fix Math**: validate and repair LaTeX formulas with optional LLM assistance.
- **Fix Mermaid**: validate and repair Mermaid diagrams (requires `mmdc` from mermaid-cli).
- **Recommended order**: `fix` → `fix-math` → `fix-mermaid` → `fix`.

```bash
uv run deepresearch-flow recognize md embed --input ./raw_ocr --output ./clean_md
```

```bash
# Organize MinerU output and apply OCR fixes
uv run deepresearch-flow recognize organize \
  --input ./mineru_outputs \
  --output-simple ./ocr_md --fix

# Fix and format existing markdown
uv run deepresearch-flow recognize fix \
  --input ./ocr_md --output ./ocr_md_fixed

# Fix in place (also works with --json flag)
uv run deepresearch-flow recognize fix \
  --input ./ocr_md --in-place

# Fix LaTeX formulas
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model openai/gpt-4o-mini --in-place

# Fix Mermaid diagrams in JSON outputs
uv run deepresearch-flow recognize fix-mermaid \
  --json --input ./paper_outputs \
  --model openai/gpt-4o-mini --in-place

# Retry only failed formulas/diagrams
uv run deepresearch-flow recognize fix-math \
  --input ./docs --model claude/claude-3-5-sonnet-20240620 \
  --report ./fix-math-errors.json --retry-failed
```

## Configuration Reference

The config.toml supports:
- Multiple providers (OpenAI, Claude, Gemini, DashScope, Ollama, Azure OpenAI)
- Weighted model routing via `main_model`, URL routing via `providers[].base[]`, key routing via `providers[].base[].key[]`
- Runtime route pooling: `model -> base -> key` selection per request
- `--model` accepts single `provider/model`, inline JSON pool, or `@file` JSON pool
- `env:VAR_NAME` for secure key injection

Model routing examples:
```bash
# config.toml main_model
uv run deepresearch-flow paper extract --input ./docs

# Fixed model
uv run deepresearch-flow paper extract --input ./docs --model openai/gpt-4o-mini

# Inline weighted pool
uv run deepresearch-flow paper extract \
  --input ./docs \
  --model '[{"model":"openai/gpt-4o-mini","weight":4},{"model":"claude/claude-sonnet-4-5-20250929","weight":1}]'

# File-based pool
uv run deepresearch-flow paper extract \
  --input ./docs --model @main_model.json
```

Mode probing:
```bash
uv run deepresearch-flow utils test-mode \
  --config ./config.toml --model openai/gpt-4o-mini

# Write probe results back to config
uv run deepresearch-flow utils test-mode \
  --config ./config.toml --model openai/gpt-4o-mini --write-back
```

## 7. Semantic search: paper embed

`paper embed` builds a LanceDB vector index over summaries and Markdown. `paper db serve` and the API server use the index for semantic search. The input is either repaired summary JSON files or a built snapshot database with its static export directory. `config.toml` must have an `[embedding]` section, otherwise the command exits with an error.

```bash
uv run deepresearch-flow paper embed \
  --config ./config.toml \
  --input ./deep_read.json \
  --md-root ./md_simple \
  --md-translated-root ./md_base64_translated \
  --max-concurrency 4 \
  --document-window 8 \
  --output-embed-db ./paper_vectors
```

| Option | Description |
|---|---|
| `-i`, `--input` | Summary JSON file; repeatable. Mutually exclusive with `--snapshot-db`; giving both is an error |
| `--snapshot-db` | Read from a built snapshot instead of `--input`. Requires `--static-export-dir` |
| `--static-export-dir` | Static export directory of the snapshot |
| `--md-root`, `--md-translated-root` | Root directories of the source and translated Markdown |
| `--output-embed-db` | Output directory of the vector index |
| `--embedding` | Override the embedding `provider/model` from the configuration |
| `--max-concurrency` | Number of concurrent embedding requests |
| `--document-window` | Maximum number of papers prepared at the same time |
| `--rpm` | Requests-per-minute limit. No limit if omitted |
| `--tpm` | Estimated tokens-per-minute limit. Input tokens are estimated as characters ÷ 4 and counted in a sliding window. No limit if omitted |
| `--force` | Delete the existing index and rebuild it from scratch |

`paper db snapshot build` also accepts `--rpm` and `--tpm` when it builds the index in the same pass.

For how to start a server with semantic search, see the [Deployment Guide](deployment.md). For local browsing, add `--embed-db ./paper_vectors` and `--search-access-token <token>` to `paper db serve`.

## 8. Verify summary fields: paper db verify

`paper db verify` checks summary JSON against the schema of a prompt template and lists papers whose fields are missing or empty. It reads only the JSON; no database is needed.

```bash
uv run deepresearch-flow paper db verify \
  --input-json ./deep_read.json \
  --prompt-template deep_read \
  --output-json ./deep_read_verify.json
```

Pass the report to `paper extract --retry-list-json` to extract only the papers it lists:

```bash
uv run deepresearch-flow paper extract \
  --input ./md_simple \
  --model <provider/model> \
  --prompt-template deep_read \
  --output ./deep_read.json \
  --retry-list-json ./deep_read_verify.json
```

Do not put the report in a directory that the `fix` commands scan.

## 9. Exit codes

The batch commands share one set of exit codes, so a script can decide whether to retry.

| Command | When it exits with 3 |
|---|---|
| `paper extract` | Some documents failed to extract; the error report was written |
| `translator translate` | Some files failed to translate; the other files were translated |
| `recognize ocr` | Some files failed OCR |
| `recognize fix-math`, `recognize fix-mermaid` | The report lists items that were not fixed |

| Code | Meaning |
|---|---|
| 0 | Everything succeeded |
| 1 | The command itself failed; retrying will not help |
| 3 | Outputs and the error report were written, but some items failed; a retry is worthwhile |

How to retry after exit code 3 depends on the command. For `paper extract`, `recognize fix-math`, and `recognize fix-mermaid`, add `--retry-failed`. For `recognize ocr` and `translator translate`, run the same command again; they skip finished files.

`translator translate` used to exit with 0 when a single file failed. It now exits with 3.

## 10. utils init-run: generate run.sh

```text
deepresearch-flow utils init-run PROJECT [--target-lang LANG] [--config PATH]
                                 [--ocr-config PATH] [--rounds N]
```

Writes an executable `run.sh` into `PROJECT`. For usage and the flow, see [From PDFs to a snapshot](pipeline.md).

| Argument | Default | Description |
|---|---|---|
| `PROJECT` | required | An existing directory with a `pdf/` subdirectory and exactly one `.bib` file. Otherwise the command exits with an error |
| `--target-lang` | `zh` | Target language of the translation |
| `--config` | `config.toml` | Path of the LLM configuration. Resolved against the current directory and written into the script as an absolute path |
| `--ocr-config` | `ocr.toml` | Path of the OCR configuration, handled like `--config` |
| `--rounds` | `3` | Maximum retry rounds per step for failed items |

The command has no `--model` option. In the generated script, every command that calls an LLM carries the placeholder `--model PROVIDER/MODEL`, which you replace. The command overwrites an existing `run.sh`.
