## ADDED Requirements

### Requirement: Paper CLI command group
The system SHALL provide a CLI entrypoint `deepresearch-flow` that exposes a `paper` command group.

#### Scenario: Paper help lists subcommands
- **WHEN** the user runs `deepresearch-flow paper --help`
- **THEN** the help output lists the `extract` and `db` subcommands

### Requirement: Explicit model routing
For any subcommand that calls a model provider, the system SHALL require `--model` to be specified in `provider/model` format.

#### Scenario: Reject missing provider prefix
- **WHEN** the user passes `--model qwen-max`
- **THEN** the command fails with a validation error describing the required `provider/model` format

### Requirement: Markdown input discovery
The system SHALL accept markdown inputs from a file path and/or a directory path.
When a directory is provided, the system SHALL recursively discover `*.md` files by default.
The system SHALL support restricting discovered files using a glob pattern.

#### Scenario: Directory recursion default
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs`
- **THEN** the tool recursively collects `*.md` files under `./docs`

#### Scenario: Glob filtering
- **WHEN** the user runs `deepresearch-flow paper extract --input ./docs --glob '**/output.md'`
- **THEN** only matching markdown files are processed

### Requirement: Config-driven providers
The system SHALL load providers from a TOML config file (default: `config.toml`) containing one or more `[[providers]]` entries.
Each provider entry SHALL have a unique `name`.

#### Scenario: Reject config without providers
- **WHEN** the config file contains zero `[[providers]]` entries
- **THEN** the tool fails at startup with a validation error

### Requirement: Supported provider types
The system SHALL support at least the following provider types:
- `ollama` using the native Ollama API
- `openai_compatible` using an OpenAI-compatible HTTP API
- `dashscope` using the DashScope SDK API

#### Scenario: Select ollama provider explicitly
- **WHEN** the user passes `--model ollama/llama3.1`
- **THEN** the tool routes requests to the provider named `ollama` using model `llama3.1`

#### Scenario: Select dashscope provider explicitly
- **WHEN** the user passes `--model dashscope/qwen-max`
- **THEN** the tool routes requests to the provider named `dashscope` using model `qwen-max`

### Requirement: API key resolution
For providers that require API keys, the system SHALL support:
- literal keys, and
- indirection via `env:VAR_NAME` that resolves at runtime.
The system SHALL NOT emit raw API key values in logs or error messages.

#### Scenario: Resolve env indirection
- **WHEN** config contains an API key entry `env:OPENAI_API_KEY`
- **AND** the environment variable `OPENAI_API_KEY` is set
- **THEN** the request is authenticated using the environment variable value

### Requirement: JSON Schema based extraction
The system SHALL accept a JSON Schema definition for extraction.
If no schema is provided via CLI or config, the system SHALL use a built-in default schema.
At config load time, the schema SHALL be validated to include required top-level keys `paper_title` and `paper_authors`.
During extraction, model output SHALL be validated against the JSON Schema and retried up to a configurable limit when invalid.
The `paper_authors` field SHALL be an array of strings in all extracted outputs.

#### Scenario: Use built-in schema by default
- **WHEN** the user runs `deepresearch-flow paper extract` without providing a schema
- **THEN** the tool uses a built-in default JSON Schema that includes `paper_title` and `paper_authors`

#### Scenario: Reject schema missing required keys
- **WHEN** the provided JSON Schema does not include `paper_title`
- **THEN** the tool fails before processing any documents with a validation error

#### Scenario: Retry on invalid model output
- **WHEN** a model response cannot be parsed as JSON or does not validate against the schema
- **THEN** the tool retries extraction up to the configured limit and records a per-document failure if it still cannot produce valid JSON

#### Scenario: Enforce authors as list
- **WHEN** a model response returns `paper_authors` as a string
- **THEN** the response is treated as invalid and retried

### Requirement: Incremental processing and idempotency
When the aggregated output file already exists, the system SHALL run in incremental mode by default.
The system SHALL persist per-document source metadata including `source_path` and a deterministic `source_hash`.
If a document with matching `source_path` and `source_hash` is already present in the aggregated output, the system SHALL skip re-extraction for that document.
The system SHALL support `--force` to re-extract all documents regardless of prior results.

#### Scenario: Skip unchanged files by default
- **WHEN** `paper_infos.json` already contains an entry with `source_path` and `source_hash` matching a discovered markdown file
- **THEN** the tool does not call the model provider for that file and keeps the existing extracted result

#### Scenario: Force re-extraction
- **WHEN** the user runs `deepresearch-flow paper extract --force`
- **THEN** the tool re-extracts all discovered markdown files even if they exist unchanged in `paper_infos.json`

### Requirement: Structured error persistence
The system SHALL persist extraction failures to a machine-readable file (`paper_errors.json` by default).
Each error record SHALL include at least `source_path`, `provider`, `model`, `error_type`, and `error_message`.
The system SHALL support `--retry-failed` to retry only the documents listed in the error file.

#### Scenario: Persist failures for later retry
- **WHEN** extraction fails for one or more documents
- **THEN** `paper_errors.json` is written or updated with an entry per failed document

#### Scenario: Retry only failed documents
- **WHEN** the user runs `deepresearch-flow paper extract --retry-failed`
- **THEN** only documents recorded as failed in `paper_errors.json` are reprocessed

### Requirement: Concurrency control and backoff
The system SHALL limit concurrent provider requests using a configurable `max_concurrency` value (configurable via config and overrideable via CLI).
The system SHALL implement retry with exponential backoff for rate limit responses (e.g., HTTP 429) and transient network errors up to a configurable limit.

#### Scenario: Concurrency is bounded
- **WHEN** `max_concurrency` is set to `5`
- **THEN** no more than 5 provider requests are in-flight at the same time

#### Scenario: Rate limit backoff
- **WHEN** a provider request receives a rate limit response
- **THEN** the tool waits using backoff before retrying until `max_retries` is reached

### Requirement: Dry run and cost safety
The system SHALL provide a `--dry-run` mode for extraction that performs input discovery and reports the number of files that would be processed without calling any model provider.
The tool SHOULD provide an approximate cost estimate (e.g., based on character count) during dry run.

#### Scenario: Dry run does not call providers
- **WHEN** the user runs `deepresearch-flow paper extract --dry-run`
- **THEN** the tool lists the number of discovered markdown files and exits without producing `paper_infos.json`

### Requirement: Provider extraction strategy configuration
The system SHALL support configuring a per-provider extraction strategy for structured output, including:
- a structured JSON mode (e.g., JSON Schema / JSON object) when supported, and
- a prompt-based fallback mode.
The system SHALL fall back to prompt-based extraction when structured mode is unsupported or fails, while still validating output locally against the JSON Schema.

#### Scenario: Provider uses structured mode when enabled
- **WHEN** a provider is configured to use structured JSON mode
- **THEN** extraction requests use the structured mode for that provider

#### Scenario: Fallback to prompt mode
- **WHEN** structured mode fails or is unsupported by a provider
- **THEN** the tool retries using prompt-based extraction and validates the resulting JSON locally

### Requirement: Context truncation
The system SHALL support truncating markdown content before sending it to providers using configurable limits and strategies.
The tool SHALL record truncation metadata for each processed document when truncation occurs.

#### Scenario: Truncate overly long documents
- **WHEN** a markdown file exceeds the configured maximum input size
- **THEN** the content is truncated according to the configured strategy and extraction proceeds without provider-side context errors

### Requirement: Aggregated JSON output
For non-dry-run extraction runs, the system SHALL write an aggregated JSON output file containing extracted results.
The default aggregated output filename SHALL be `paper_infos.json`.
The system SHALL support `--split` to additionally write per-document JSON outputs.

#### Scenario: Write aggregated output
- **WHEN** `deepresearch-flow paper extract` completes
- **THEN** an aggregated JSON file `paper_infos.json` is written

#### Scenario: Write split outputs
- **WHEN** the user passes `--split`
- **THEN** the tool writes additional per-document JSON outputs using deterministic names with a short hash suffix for collisions

### Requirement: Markdown rendering from JSON
The system SHALL provide a DB command that renders Markdown from the aggregated JSON output using a template.
The rendering command SHALL NOT require calling a model provider.

#### Scenario: Render markdown using a built-in template
- **WHEN** the user runs `deepresearch-flow paper db render-md --input paper_infos.json`
- **THEN** Markdown files are generated using the built-in default template

#### Scenario: Render markdown using a custom template
- **WHEN** the user runs `deepresearch-flow paper db render-md --input paper_infos.json --template paper.md.j2`
- **THEN** Markdown files are generated from JSON content using the specified template
