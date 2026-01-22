## MODIFIED Requirements
### Requirement: Deep read module guidance
The system SHALL provide detailed, per-module guidance for deep_read outputs, including required subpoints and examples to encourage comprehensive responses. During multi-stage runs, the system SHALL present detailed instructions only for the active module and brief summaries for other modules. The system SHALL include recency instructions reinforcing JSON-only output, completeness, and avoidance of non-target fields.

#### Scenario: Expanded module outputs
- **WHEN** deep_read runs
- **THEN** each module follows the expanded checklist and provides richer, more complete content

#### Scenario: Stage prompt brevity
- **WHEN** a single module runs in stage mode
- **THEN** the active module uses detailed instructions while other modules are summarized briefly

#### Scenario: Recency enforcement
- **WHEN** a single module runs in stage mode
- **THEN** the prompt ends with explicit JSON-only and completeness reminders for that stage
