## MODIFIED Requirements

### Requirement: Provider retry behavior
The system SHALL retry failed requests based on `max_retries`. JSON parse failures and schema validation failures SHALL be retried using the same retry loop.

#### Scenario: Retry parse failures
- **WHEN** the model response cannot be parsed as JSON
- **THEN** the system retries the request up to `max_retries`

#### Scenario: Retry validation failures
- **WHEN** the response fails schema validation
- **THEN** the system retries the request up to `max_retries`

### Requirement: Failure logging
The system SHALL include error type and error message details in extraction warning logs.

#### Scenario: Warning includes reason
- **WHEN** an extraction fails
- **THEN** the warning log includes error_type and error_message
