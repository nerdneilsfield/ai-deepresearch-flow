## ADDED Requirements
### Requirement: Recognize fix supports JSON inputs
The system SHALL allow `recognize fix --json` to process JSON files containing extracted paper items and write updated JSON while preserving the original top-level structure.

#### Scenario: Fix JSON list output in-place
- **WHEN** `recognize fix --json --in-place` is invoked on a JSON file containing a list of paper items
- **THEN** the command updates only the markdown fields and keeps the JSON list structure intact

#### Scenario: Fix JSON dict output to a new file
- **WHEN** `recognize fix --json --output <dir>` is invoked on a JSON file with a `{ "papers": [...] }` payload
- **THEN** the command writes a JSON file in the output directory with the same top-level keys

### Requirement: JSON fix applies template-aware markdown repair
The system SHALL apply the markdown fix pipeline and optional rumdl formatting to template-specific fields in JSON items.

#### Scenario: Fix template outputs
- **WHEN** a JSON item has `prompt_template` or `template_tag` set to `three_pass`
- **THEN** the `step1_summary`, `step2_analysis`, and `step3_analysis` fields are fixed and formatted
