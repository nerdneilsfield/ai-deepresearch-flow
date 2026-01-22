## MODIFIED Requirements

### Requirement: Stage-level retry for failed extractions
The system SHALL support a `--retry-failed-stages` option that retries only the stages recorded as failed in the errors report for each document. Missing stage outputs SHALL be re-run when this option is used.

#### Scenario: Retry only failed stages
- **WHEN** a document has failures recorded for stages C1 and D
- **AND** the user runs `paper extract` with `--retry-failed-stages`
- **THEN** only stages C1 and D are re-executed for that document
- **AND** other stages for that document are skipped

#### Scenario: Missing stage output
- **WHEN** a document has a missing required stage output
- **AND** the user runs `paper extract` with `--retry-failed-stages`
- **THEN** the missing stage is re-executed even if it is not listed in the errors report
