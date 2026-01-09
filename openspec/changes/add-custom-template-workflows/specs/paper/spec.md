## ADDED Requirements

### Requirement: Custom template inputs
The system SHALL allow users to provide custom extraction prompts, JSON Schema, and render templates.
Custom inputs SHALL be provided via CLI flags:
- `--schema-json` for a JSON Schema file
- `--prompt-system` and `--prompt-user` for separate prompt files
- `--markdown-template` for a Jinja2 render template
- `--template-dir` for a directory containing `system.j2`, `user.j2`, `schema.json`, and `render.j2`

#### Scenario: Use custom prompt and schema
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-system ./system.j2 --prompt-user ./user.j2 --schema-json ./schema.json`
- **THEN** the extractor uses the custom prompts and validates output against the provided schema

#### Scenario: Use template directory
- **WHEN** the user runs `deepresearch-flow paper extract --template-dir ./templates`
- **THEN** the extractor loads `system.j2`, `user.j2`, and `schema.json` from the directory

#### Scenario: Use custom markdown template
- **WHEN** the user runs `deepresearch-flow paper db render-md --markdown-template ./render.j2`
- **THEN** the renderer uses the custom markdown template for output

### Requirement: Multi-stage extraction for complex templates
The system SHALL support multi-stage extraction for the built-in templates `deep_read`, `seven_questions`, and `three_pass`.
Each stage SHALL be executed as a separate model call with stage-level retry and backoff.
Each stage SHALL receive the document content and any previously completed stage outputs.
The system SHALL validate each stage output before proceeding to the next stage.

#### Scenario: Deep-read stages run sequentially
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template deep_read`
- **THEN** the extractor executes module stages sequentially and merges their outputs

#### Scenario: Fail fast on invalid stage output
- **WHEN** a stage response is not valid JSON or is missing required stage keys
- **THEN** the stage is retried and the pipeline does not proceed to the next stage until it succeeds or retries are exhausted

### Requirement: Stage output persistence
The system SHALL persist intermediate stage outputs in per-document stage files to allow resume/debugging.
Each persisted entry SHALL include `source_path`, `source_hash`, `prompt_template`, `stage_name`, and `output_language`.

#### Scenario: Persist stage outputs
- **WHEN** a stage completes for a document
- **THEN** the per-document stage output file is updated with the new stage result

### Requirement: Schema precedence
When `--schema-json` is provided, the system SHALL use it in place of any built-in template schema.

#### Scenario: CLI schema overrides template schema
- **WHEN** the user passes `--schema-json ./schema.json` with `--prompt-template deep_read`
- **THEN** the extractor validates against `./schema.json` instead of the built-in schema

### Requirement: Custom templates are single-stage
When custom prompt inputs are used (`--prompt-system`/`--prompt-user` or `--template-dir`), the system SHALL run in single-stage extraction mode.

#### Scenario: Custom prompts force single-stage mode
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-system ./system.j2 --prompt-user ./user.j2`
- **THEN** the extractor performs a single-stage extraction
