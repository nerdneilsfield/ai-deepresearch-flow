## ADDED Requirements

### Requirement: Queue pause when key pool unavailable
The system SHALL pause scheduling new extraction work when all API keys are unavailable due to cooldown or quota waits.

#### Scenario: All keys unavailable triggers pause
- **WHEN** every configured key is in cooldown or quota wait
- **THEN** the scheduler MUST stop dequeuing new stages/documents

### Requirement: Pause threshold for short cooldowns
The system SHALL avoid queue pauses for short cooldown windows below a configured threshold.

#### Scenario: Short wait does not pause queue
- **WHEN** all keys are unavailable for less than the pause threshold
- **THEN** the scheduler MUST continue without entering queue pause mode

### Requirement: In-flight work continues during pause
The system SHALL allow in-flight requests to complete while the queue is paused.

#### Scenario: Active requests finish during pause
- **WHEN** the queue is paused due to key exhaustion
- **THEN** any already-dispatched requests MUST continue to completion

### Requirement: Automatic resume on key availability
The system SHALL resume scheduling when any key becomes available.

#### Scenario: Resume after cooldown ends
- **WHEN** a key's cooldown or quota wait expires
- **THEN** the scheduler MUST resume dequeuing new work

### Requirement: Pause/resume logging
The system SHALL log pause and resume events with timestamps and reasons.

#### Scenario: Logs show pause window
- **WHEN** the queue pauses due to key unavailability
- **THEN** logs MUST include the pause reason and expected resume time

### Requirement: Pause watchdog safety
The system SHALL prevent a paused queue from deadlocking if the watcher fails.

#### Scenario: Watcher failure does not deadlock queue
- **WHEN** the pause watcher raises an exception
- **THEN** the system MUST log the error and release the pause gate to avoid deadlock
