## ADDED Requirements

### Requirement: Extract sleep throttling
The system SHALL provide `--sleep-every` and `--sleep-time` options for `paper extract` to pause request execution before issuing the next request on a global interval.

#### Scenario: Sleep after threshold
- **WHEN** the user runs `paper extract --sleep-every 10 --sleep-time 60`
- **THEN** the extractor sleeps for 60 seconds before issuing request 11, 21, 31, ...

### Requirement: Throttle counts retries and stages
The system SHALL count every request toward the threshold, including retries and multi-stage requests, and SHALL apply the sleep globally across concurrent tasks.

#### Scenario: Retries and stages count toward sleep
- **WHEN** a request is retried or executed as part of a multi-stage template
- **THEN** the retry or stage contributes to the sleep counter
