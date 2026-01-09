## ADDED Requirements

### Requirement: Custom template inputs
The system SHALL allow users to provide custom extraction prompts, JSON Schema, and render templates.
Custom inputs SHALL be provided via CLI flags:
- `--schema-json` for a JSON Schema file
- `--prompt-system` and `--prompt-user` for separate prompt files, or
- `--prompt-dir` for a directory containing `system.j2` and `user.j2`
- `--markdown-template` for a Jinja2 render template

#### Scenario: Use custom prompt and schema
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-system ./system.j2 --prompt-user ./user.j2 --schema-json ./schema.json`
- **THEN** the extractor uses the custom prompts and validates output against the provided schema

#### Scenario: Use prompt directory
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-dir ./prompts`
- **THEN** the extractor loads `system.j2` and `user.j2` from the directory

#### Scenario: Use custom markdown template
- **WHEN** the user runs `deepresearch-flow paper db render-md --markdown-template ./render.j2`
- **THEN** the renderer uses the custom markdown template for output

### Requirement: Multi-stage extraction for complex templates
The system SHALL support multi-stage extraction for the built-in templates `deep_read`, `seven_questions`, and `three_pass`.
Each stage SHALL be executed as a separate model call with stage-level retry and backoff.
Each stage SHALL receive the document content and any previously completed stage outputs.

#### Scenario: Deep-read stages run sequentially
- **WHEN** the user runs `deepresearch-flow paper extract --prompt-template deep_read`
- **THEN** the extractor executes module stages sequentially and merges their outputs

### Requirement: Stage output persistence
The system SHALL persist intermediate stage outputs in a machine-readable file to allow resume/debugging.
Each persisted entry SHALL include `source_path`, `source_hash`, `prompt_template`, `stage_name`, and `output_language`.

#### Scenario: Persist stage outputs
- **WHEN** a stage completes for a document
- **THEN** the stage output file is updated with the new stage result
