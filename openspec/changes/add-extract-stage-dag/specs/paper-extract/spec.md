## ADDED Requirements

### Requirement: Stage dependency declarations
The system SHALL allow multi-stage templates to declare an optional `depends_on` list per stage.

#### Scenario: Missing depends_on defaults to sequential
- **WHEN** a stage definition omits `depends_on`
- **THEN** the stage MUST depend on the previous stage in definition order

### Requirement: DAG scheduling toggle
The system SHALL provide a `extract.stage_dag` configuration setting and a `--stage-dag` CLI flag to enable dependency-aware scheduling.

#### Scenario: DAG disabled preserves sequential order
- **WHEN** `stage_dag` is false or unset
- **THEN** stages MUST run in definition order per document

### Requirement: Parallel execution of ready stages
When DAG scheduling is enabled, the system SHALL schedule any stage whose dependencies are satisfied, up to `max_concurrency`.

#### Scenario: Ready stages run concurrently
- **WHEN** multiple stages have no unmet dependencies
- **THEN** the scheduler MAY execute them in parallel

### Requirement: Ready-only scheduling
In DAG mode, the scheduler SHALL enqueue only stages whose dependencies are satisfied and MUST NOT block workers on unmet dependencies.

#### Scenario: Workers never block on unmet dependencies
- **WHEN** a stage has unmet dependencies
- **THEN** the stage MUST NOT be queued until dependencies are complete

### Requirement: Deterministic previous_outputs in DAG mode
When DAG scheduling is enabled, `previous_outputs` SHALL include only outputs from declared dependencies.

#### Scenario: previous_outputs contains dependencies only
- **WHEN** a stage depends on module_a and module_c3
- **THEN** previous_outputs MUST include only module_a and module_c3 outputs

### Requirement: Dependency cycle detection
The system SHALL detect dependency cycles at startup and fail fast with a clear error.

#### Scenario: Cycle reported before execution
- **WHEN** stage definitions contain a dependency cycle
- **THEN** extraction MUST abort before scheduling any stage

### Requirement: DAG dry-run plan preview
When DAG scheduling is enabled with `--dry-run`, the system SHALL print a per-document execution plan that reflects dependency order.

#### Scenario: Dry-run shows dependency plan
- **WHEN** the user runs extract with `--stage-dag --dry-run`
- **THEN** the output MUST include a DAG plan for each document (or a representative document) showing stage order by dependency level

### Requirement: Dependency-aware retries
When `--retry-failed-stages` is enabled in DAG mode, the system SHALL enqueue failed stages and any unmet dependencies required to execute them.

#### Scenario: Retry requeues dependencies
- **WHEN** a failed stage depends on another stage that is missing or invalid
- **THEN** the dependency MUST be scheduled before the failed stage

### Requirement: Failure propagation
If a stage fails, the system SHALL mark the document as failed and skip dependent stages.

#### Scenario: Dependent stages are skipped
- **WHEN** stage B depends on stage A and stage A fails
- **THEN** stage B MUST NOT run and the document MUST be marked failed
