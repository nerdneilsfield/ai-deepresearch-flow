## MODIFIED Requirements

### Requirement: JSON Schema based extraction
The system SHALL accept a JSON Schema definition for extraction.
If no schema is provided via CLI or config, the system SHALL use a built-in default schema.
At config load time, the schema SHALL be validated to include required top-level keys `paper_title` and `paper_authors`.
During extraction, model output SHALL be parsed as JSON and validated against the JSON Schema.
If JSON parsing fails, the system SHALL attempt to repair the response before raising a parse error.
Extra top-level fields not defined in the schema SHALL NOT cause validation failure and MAY be ignored.
During extraction, model output SHALL be retried up to a configurable limit when it cannot be parsed or validated.
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

#### Scenario: Accept extra fields
- **WHEN** a model response contains extra top-level fields not defined in the schema
- **THEN** the tool accepts the response as long as required fields validate

#### Scenario: Repair malformed JSON
- **WHEN** a model response contains minor JSON syntax errors (e.g., invalid escapes)
- **THEN** the tool attempts JSON repair and proceeds if validation passes
