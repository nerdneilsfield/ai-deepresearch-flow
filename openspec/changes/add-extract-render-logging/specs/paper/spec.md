## ADDED Requirements

### Requirement: Extract-time markdown rendering
The system SHALL allow `paper extract` to optionally render markdown output in the same run.
The renderer SHALL reuse the same template selection rules as `paper db render-md`.

#### Scenario: Render markdown during extract
- **WHEN** the user runs `deepresearch-flow paper extract --render-md`
- **THEN** the tool renders markdown outputs after extraction completes

### Requirement: Progress and verbose logging
The system SHALL provide progress indicators for extraction and stage processing.
The system SHALL provide a `--verbose` flag to enable detailed logs.
Logs SHALL be colorized when possible.

#### Scenario: Show progress and verbose logs
- **WHEN** the user runs `deepresearch-flow paper extract --verbose`
- **THEN** the tool shows tqdm progress and detailed colored logs
