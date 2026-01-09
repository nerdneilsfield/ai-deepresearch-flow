## Context
The system currently supports built-in template bundles for extract and render. Complex templates like deep_read are currently single-shot. Users also want custom templates without editing built-ins.

## Goals / Non-Goals
- Goals:
  - Add CLI support for custom prompt templates, custom JSON schema, and custom render Jinja2 templates.
  - Support multi-stage extraction for deep_read, seven_questions, and three_pass templates.
  - Persist intermediate stage outputs for each document.
  - Keep output language captured in JSON for render selection.
- Non-Goals:
  - Streaming output.
  - Model-side tool usage or web search.

## Decisions
- Custom template inputs:
  - `--schema-json PATH` supplies a JSON Schema.
  - Prompt templates can be supplied in two ways:
    - `--prompt-system PATH` + `--prompt-user PATH`
  - `--markdown-template PATH` supplies a Jinja2 template for render-md.
  - Alternatively, a template directory can be provided containing all four files:
    - `system.j2`, `user.j2`, `schema.json`, `render.j2`
- Multi-stage extraction:
  - Only for built-in templates: `deep_read`, `seven_questions`, `three_pass`.
  - Stage calls are sequential per document; concurrency applies at the stage level across documents.
  - Each stage call receives: prompt template + document content + previously completed module outputs.
- Persistence:
  - After each stage, write/update an intermediate JSON file alongside aggregated output (e.g., `paper_stage_outputs.json`).
  - Each stage entry includes `source_path`, `source_hash`, `prompt_template`, `stage_name`, and `output_language`.

## Risks / Trade-offs
- Multi-stage extraction increases total API calls; mitigate with per-stage retry/backoff and explicit logging.
- Custom prompt/schema mismatches can increase validation failures; provide clear errors.

## Migration Plan
- Add CLI flags, template loader for custom paths, and stage runner.
- Maintain backward compatibility for built-in simple template flow.

## Open Questions
- Confirm the name and location for the stage-output file (default: `paper_stage_outputs.json`).
