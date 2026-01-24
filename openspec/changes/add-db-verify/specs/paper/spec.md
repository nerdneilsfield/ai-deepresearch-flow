## ADDED Requirements
### Requirement: Verify paper JSON entries
The system SHALL provide a `paper db verify` command that checks a JSON database against the schema of a prompt template (e.g., `deep_read`) and reports empty or missing fields.

#### Scenario: Verify database with template schema
- **WHEN** the user runs `paper db verify` with `--input-json` and `--prompt-template deep_read`
- **THEN** the command SHALL evaluate every schema field per paper and treat missing fields, empty strings, nulls, empty lists, and empty objects as empty

#### Scenario: Console summary and details
- **WHEN** verification completes
- **THEN** the command SHALL print a rich console summary plus per-item details of missing fields

#### Scenario: JSON report emitted
- **WHEN** the user provides `--output-json`
- **THEN** the command SHALL write a JSON report that includes `template_tag`, `schema_fields`, and per-item `missing_fields`

### Requirement: Retry extraction from verify report
The system SHALL allow `paper extract` to re-run only items listed in a verification report.

#### Scenario: Retry list filters inputs
- **WHEN** the user runs `paper extract` with `--retry-list-json` pointing to a verify report
- **THEN** extraction SHALL be limited to the items listed in the report

#### Scenario: Stage-specific retries
- **WHEN** a verify report item includes `retry_stages`
- **THEN** `paper extract` SHALL re-run only those stages for that item

#### Scenario: Full re-extraction fallback
- **WHEN** a verify report item has missing fields that do not map to stage names
- **THEN** `paper extract` SHALL re-run the full document for that item
