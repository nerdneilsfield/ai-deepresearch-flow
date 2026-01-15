## Context
`recognize fix` currently operates on markdown files only. Extracted paper summaries are stored as JSON (either list or `{papers: [...]}`) and contain markdown-like fields that benefit from the same OCR fixes and rumdl formatting.

## Goals / Non-Goals
- Goals:
  - Reuse the existing markdown fix pipeline (`fix_markdown_text`) for JSON fields that represent markdown.
  - Preserve JSON structure and non-markdown fields.
- Non-Goals:
  - Changing extract schemas or adding new prompt templates.
  - Modifying the rendering pipeline in db serve.

## Decisions
- Decision: Add a `--json` flag to `recognize fix` instead of creating a new command.
  - Reason: keeps fix behavior in one place and reuses existing flags (`--in-place`, `--output`, `--no-format`).
- Decision: Determine markdown fields by template schema name (`prompt_template` / `template_tag`).
  - Simple/default: `abstract`, `summary`.
  - Deep read: `module_*` fields.
  - Eight questions: `question1`…`question8`.
  - Three pass: `step1_summary`, `step2_analysis`, `step3_analysis`.
- Decision: If template is unknown, only fix `summary` and `abstract` when present to avoid mutating metadata.

## Risks / Trade-offs
- Risk: Users may expect additional fields to be fixed (e.g., `keywords`).
  - Mitigation: keep mapping explicit; document field list in CLI help.
- Risk: Large JSON files could be slow to format with rumdl.
  - Mitigation: reuse async worker pool and only format fields that changed.

## Migration Plan
- Add JSON mode with no changes to default markdown fix behavior.
- Validate against sample JSON outputs and confirm no structural changes.
