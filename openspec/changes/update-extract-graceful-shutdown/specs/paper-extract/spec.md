## ADDED Requirements

### Requirement: Graceful shutdown on signals
The system SHALL initiate graceful shutdown when it receives SIGINT or SIGTERM during extract.

#### Scenario: SIGINT triggers shutdown
- **WHEN** the user presses Ctrl+C during extract
- **THEN** the system MUST begin graceful shutdown

### Requirement: Stop scheduling new work
The system SHALL stop dequeuing new documents/stages once graceful shutdown begins.

#### Scenario: Queue stops on shutdown
- **WHEN** graceful shutdown is requested
- **THEN** no new tasks MUST be scheduled

### Requirement: Allow in-flight tasks to complete
The system SHALL allow in-flight requests to finish during graceful shutdown.

#### Scenario: In-flight completion
- **WHEN** a request is already in progress at shutdown
- **THEN** it MUST be allowed to complete

### Requirement: Flush outputs before exit
The system SHALL write aggregated outputs and error records before exiting after graceful shutdown.

#### Scenario: Outputs persisted
- **WHEN** shutdown completes
- **THEN** output JSON and errors JSON MUST be written

### Requirement: Shutdown logging
The system SHALL log shutdown state transitions for visibility.

#### Scenario: Shutdown logs emitted
- **WHEN** graceful shutdown starts and finishes
- **THEN** logs MUST show the shutdown phase
