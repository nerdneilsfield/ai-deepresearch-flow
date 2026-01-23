## ADDED Requirements
### Requirement: Extract timeout configuration
The system SHALL allow users to configure the request timeout for `paper extract` via `extract.timeout` (seconds) in config, and SHALL allow a `--timeout` CLI flag to override the configured value for a run.

#### Scenario: Use configured timeout
- **WHEN** `paper extract` runs without `--timeout`
- **THEN** provider calls use the `extract.timeout` value from config

#### Scenario: Override timeout per run
- **WHEN** `paper extract` runs with `--timeout 180`
- **THEN** provider calls use 180 seconds regardless of config
