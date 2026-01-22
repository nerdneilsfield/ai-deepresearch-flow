## ADDED Requirements

### Requirement: Stage-level retry for failed extractions
The system SHALL support a `--retry-failed-stages` option that retries only the stages recorded as failed in the errors report for each document.

#### Scenario: Retry only failed stages
- **WHEN** a document has failures recorded for stages C1 and D
- **AND** the user runs `paper extract` with `--retry-failed-stages`
- **THEN** only stages C1 and D are re-executed for that document
- **AND** other stages for that document are skipped

### Requirement: Retry mode exclusivity
The system SHALL reject runs that specify both `--retry-failed` and `--retry-failed-stages`.

#### Scenario: Conflicting retry options
- **WHEN** a user passes both `--retry-failed` and `--retry-failed-stages`
- **THEN** the command fails with a clear error message

### Requirement: Stage failure summary
The system SHALL include stage failure totals in the extract summary output.

#### Scenario: Summary reports stage failures
- **WHEN** an extraction run completes
- **THEN** the summary includes the total number of failed stages
- **AND** the summary includes the number of stages retried in the run
