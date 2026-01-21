## ADDED Requirements
### Requirement: Translation request/response logging
The translator SHALL optionally emit a JSON log that records each request/response attempt.

#### Scenario: Record initial attempt
- **WHEN** translation runs with debug logging enabled
- **THEN** the log SHALL include the initial request and response payload

#### Scenario: Record retries and fallbacks
- **WHEN** translation retries or uses fallback models
- **THEN** the log SHALL record each attempt with provider/model identifiers and attempt metadata
