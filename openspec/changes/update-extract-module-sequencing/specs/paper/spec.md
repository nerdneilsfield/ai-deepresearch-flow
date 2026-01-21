## MODIFIED Requirements
### Requirement: Multi-stage extraction order
The system SHALL execute multi-stage template modules from a single linear doc+module task queue, allowing concurrency across tasks while preserving per-document module order.

#### Scenario: Sequential modules per document
- **WHEN** `paper extract` runs with a multi-stage template
- **THEN** each document's modules execute in order even when multiple documents are processed concurrently

### Requirement: Incremental persistence per module
The system SHALL persist per-module outputs immediately after each module completes, including the aggregate output JSON.

#### Scenario: Save after module completion
- **WHEN** a module finishes for a document
- **THEN** its stage output and aggregate output JSON are written to disk before the next module starts

### Requirement: Resume with module-level skipping
The system SHALL load existing outputs on start and skip modules that are already completed for a document.

#### Scenario: Skip completed modules
- **WHEN** `paper extract` runs and prior outputs indicate some modules are already complete
- **THEN** the command skips those modules and continues with remaining modules

### Requirement: Prompt hash invalidation
The system SHALL store a prompt template hash per module and re-run a module if the stored hash differs from the current hash.

#### Scenario: Re-run on prompt change
- **WHEN** a module prompt template changes since the last run
- **THEN** the module is re-executed even if prior output exists

### Requirement: Force module re-run
The system SHALL allow forcing specific modules to re-run via CLI.

#### Scenario: Force a module
- **WHEN** `paper extract --force-stage module_b` is invoked
- **THEN** module_b is re-run for affected documents regardless of prior outputs
