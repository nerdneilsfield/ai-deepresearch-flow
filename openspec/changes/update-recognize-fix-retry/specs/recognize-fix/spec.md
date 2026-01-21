## ADDED Requirements
### Requirement: Retry failed fix-math items
The system SHALL support a retry-only mode for `recognize fix-math` that processes only formulas listed in the prior error report.

#### Scenario: Retry failed formulas from report
- **WHEN** `recognize fix-math --retry-failed` is invoked and a fix-math error report is available
- **THEN** the command re-processes only the formulas referenced in the report and skips all other formulas

#### Scenario: Missing report in retry mode
- **WHEN** `recognize fix-math --retry-failed` is invoked and the error report is missing or empty
- **THEN** the command fails fast with a clear error message

### Requirement: Retry failed fix-mermaid items
The system SHALL support a retry-only mode for `recognize fix-mermaid` that processes only Mermaid blocks listed in the prior error report.

#### Scenario: Retry failed Mermaid blocks from report
- **WHEN** `recognize fix-mermaid --retry-failed` is invoked and a fix-mermaid error report is available
- **THEN** the command re-processes only the Mermaid blocks referenced in the report and skips all other blocks

#### Scenario: Missing report in retry mode
- **WHEN** `recognize fix-mermaid --retry-failed` is invoked and the error report is missing or empty
- **THEN** the command fails fast with a clear error message
