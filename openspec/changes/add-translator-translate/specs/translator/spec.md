## ADDED Requirements

### Requirement: Translator CLI command group
The system SHALL provide a CLI command group `deepresearch-flow translator` that exposes a `translate` subcommand.

#### Scenario: Translator help lists translate
- **WHEN** the user runs `deepresearch-flow translator --help`
- **THEN** the help output lists the `translate` subcommand

### Requirement: Explicit model routing
For translation, the system SHALL require `--model` to be specified in `provider/model` format and route requests to the named provider.

#### Scenario: Reject missing provider prefix
- **WHEN** the user passes `--model qwen-max`
- **THEN** the command fails with a validation error describing the required `provider/model` format

### Requirement: Markdown input discovery
The system SHALL accept markdown inputs from a file path and/or a directory path.
When a directory is provided, the system SHALL recursively discover `*.md` files by default.
The system SHALL support restricting discovered files using a glob pattern.

#### Scenario: Directory recursion default
- **WHEN** the user runs `deepresearch-flow translator translate --input ./docs --model openai/gpt-4o-mini`
- **THEN** the tool recursively collects `*.md` files under `./docs`

#### Scenario: Glob filtering
- **WHEN** the user runs `deepresearch-flow translator translate --input ./docs --glob '**/full.md' --model openai/gpt-4o-mini`
- **THEN** only matching markdown files are processed

### Requirement: Language suffix output naming
The system SHALL write translated markdown outputs using a deterministic language suffix (e.g., `.zh.md`, `.ja.md`) derived from `--target-lang`.

#### Scenario: Zh suffix output
- **WHEN** the user runs `deepresearch-flow translator translate --input paper.md --target-lang zh --model openai/gpt-4o-mini`
- **THEN** the tool writes `paper.zh.md` (unless an explicit output override is provided)

#### Scenario: Japanese normalizes to ja
- **WHEN** the user runs `deepresearch-flow translator translate --input paper.md --target-lang ja --model openai/gpt-4o-mini`
- **THEN** the tool writes `paper.ja.md` (unless an explicit output override is provided)

### Requirement: Config-driven providers reuse
The system SHALL reuse the existing `config.toml` provider configuration (`[[providers]]`) for translation.
For providers that require API keys, the system SHALL resolve `env:VAR_NAME` indirections at runtime.
The system SHALL NOT emit raw API key values in logs or error messages.

#### Scenario: Resolve env indirection
- **WHEN** config contains an API key entry `env:OPENAI_API_KEY`
- **AND** the environment variable `OPENAI_API_KEY` is set
- **THEN** the request is authenticated using the environment variable value

### Requirement: OCR markdown reference repair
When OCR repair is enabled, the system SHALL normalize common OCR reference formats into footnote-style references.
At minimum, it SHALL support:
- `[1]` → `[^1]`
- `[1-3]` → `[^1] [^2] [^3]`
- `[1, 2, 3]` → `[^1] [^2] [^3]`

#### Scenario: Normalize reference range
- **WHEN** the input contains `[2-4]`
- **THEN** the translated output preserves the equivalent footnote references `[^2] [^3] [^4]`

### Requirement: Placeholder-based Markdown protection
The system SHALL protect non-translatable Markdown spans by replacing them with placeholders before translation and restoring them after translation.
At minimum, protection SHALL cover:
- images (`![...](...)`)
- LaTeX math blocks and inline math
- code fences and inline code
- HTML blocks and inline HTML
- tables (whole-table freezing)
- URLs and autolinks

#### Scenario: Preserve formula and images
- **WHEN** the input contains inline math `$E=mc^2$` and an image `![fig](data:image/png;base64,...)`
- **THEN** the translated output preserves those spans byte-for-byte after restore

### Requirement: Unstructured translation calls
Translation provider calls SHALL request unstructured text output even if the provider is configured for structured output elsewhere.

#### Scenario: Ignore structured output config for translation
- **WHEN** the provider is configured with `structured_mode = "json_schema"`
- **THEN** the translator still requests plain text output for translation

### Requirement: Validation and retries for model drift
The system SHALL validate translation output and retry when it violates invariants.
At minimum, the system SHALL detect:
- missing or malformed node markers, and
- placeholder multiset mismatches.
After exhausting retries, the system SHALL fall back to the original node content for the failed nodes.

#### Scenario: Placeholder mismatch triggers retry
- **WHEN** a translated node is missing one or more placeholders present in the source node
- **THEN** the system retries translation for that node and falls back to the original content if retries are exhausted

### Requirement: Claude max_tokens configuration
For `claude` providers, the system SHALL support an optional `max_tokens` configuration to control output length for translation calls.

#### Scenario: Claude uses configured max_tokens
- **WHEN** the provider is `claude` and config sets `max_tokens = 4096`
- **THEN** the translation request uses `max_tokens = 4096`

### Requirement: Debug dumps
The system SHALL provide optional debug outputs for troubleshooting translation failures, including:
- protected markdown output, and
- placeholder mapping database and/or per-node diagnostics.

#### Scenario: Dump placeholder database
- **WHEN** the user passes `--dump-placeholders`
- **THEN** the tool writes a machine-readable placeholder mapping file alongside the output
