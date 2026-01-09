## Context
Extraction retries can be triggered by small JSON formatting mistakes (invalid escapes, missing quotes) and by model outputs that include extra top-level fields not required by the schema.

## Goals / Non-Goals
- Goals:
  - Recover from minor JSON formatting errors before declaring a parse failure.
  - Allow extra top-level fields without failing schema validation.
- Non-Goals:
  - Alter required-field validation rules.
  - Infer or coerce missing required fields.

## Decisions
- Use `json-repair` as a lightweight fallback parser when `json.loads` fails.
- Keep required field validation unchanged; only relax handling of extra top-level fields.

## Risks / Trade-offs
- JSON repair may accept malformed outputs that still contain incorrect values; validation still protects required shape.
- Added dependency increases package size slightly.

## Migration Plan
- Add dependency and wire parse fallback.
- Update extraction validation path to ignore extra top-level fields.

## Open Questions
- None.
