## ADDED Requirements

### Requirement: Per-key cooldown tracking
The system SHALL track retryable provider errors per API key and apply a cooldown window after such errors.

#### Scenario: Cool down a failing key
- **WHEN** an API key returns a retryable provider error
- **THEN** that key is marked on cooldown for a short window
- **AND** subsequent requests skip that key while the cooldown is active

### Requirement: Skip cooled keys during rotation
The system SHALL select the next available non-cooled key for each request.

#### Scenario: Skip cooled keys
- **WHEN** multiple keys are configured and some are cooling down
- **THEN** requests use a non-cooled key when available

### Requirement: All keys cooling fallback
The system SHALL wait until the earliest cooldown expires if all keys are cooling down.

#### Scenario: All keys cooling
- **WHEN** all configured keys are on cooldown
- **THEN** the system waits until a cooldown expires before retrying
