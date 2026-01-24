## Context
The `paper db verify` command validates existing JSON outputs against the schema defined by a prompt template (e.g., `deep_read`). The output should drive selective re-extraction via `paper extract --retry-list-json`.

## Goals / Non-Goals
- Goals:
  - Identify empty or missing fields per schema.
  - Produce a machine-readable report for retrying only affected items.
  - Provide a human-readable summary via rich console output.
- Non-Goals:
  - Alter extraction logic beyond filtering inputs and stages.
  - Enforce schema value type correctness beyond empty checks.

## Decisions
- Report format:
  - JSON file with `template_tag`, `schema_fields`, and `items` entries.
  - Each item includes `source_path`, `paper_title`, `missing_fields`, and optional `retry_stages`.
- Stage mapping:
  - For multi-stage templates, `retry_stages` is derived by intersecting missing fields with known stage names (e.g., `module_a`, `module_h`).
  - Missing fields that do not map to stage names trigger a full document retry.

## Risks / Trade-offs
- Missing field mapping may be conservative for non-stage fields; full document retries can be more expensive but are safe.

## Open Questions
- None.
