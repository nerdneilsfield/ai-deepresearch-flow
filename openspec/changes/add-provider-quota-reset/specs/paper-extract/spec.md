## ADDED Requirements

### Requirement: Per-key quota metadata
The system SHALL accept `api_keys` as a list of strings or objects. Object entries SHALL include `key`, `quota_duration` (seconds), `reset_time` (string example), and `quota_error_tokens` (list of strings).

#### Scenario: Mixed api_keys configuration
- **WHEN** a provider config includes both string keys and object keys
- **THEN** the system loads string keys with no quota metadata
- **AND** object keys with quota metadata

### Requirement: Quota detection by error tokens
The system SHALL detect quota exhaustion when all configured `quota_error_tokens` are present in an error message for that key.

#### Scenario: Quota detection for a key
- **WHEN** an error message contains all required quota tokens for the key
- **THEN** the key is marked as quota-exhausted

### Requirement: Reset time calculation
The system SHALL compute the next reset time using `reset_time` + `quota_duration` and pause the key until the next window.

#### Scenario: Pause until next reset
- **WHEN** a key hits quota and has reset metadata
- **THEN** the key is skipped until the next reset time derived from the configured values
