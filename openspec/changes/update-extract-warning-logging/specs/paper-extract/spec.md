## ADDED Requirements

### Requirement: Colored warning output
The system SHALL render extraction failure warnings with colored output for readability.

#### Scenario: Colored failure warning
- **WHEN** an extraction fails
- **THEN** the warning log is colorized

### Requirement: Human-readable error summaries
The system SHALL include a human-readable summary of the error cause in warning logs.

#### Scenario: Error summary in warning
- **WHEN** an extraction fails due to parse, validation, or provider errors
- **THEN** the warning includes a concise, human-readable reason
