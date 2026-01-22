## MODIFIED Requirements
### Requirement: Deep read diagram guidance
The system SHALL guide deep_read diagram generation based on model judgment rather than fixed keyword triggers, SHALL allow inferred diagrams when the content implies a structure or flow (clearly labeled as inferred), and SHALL provide Mermaid syntax safety guidance (simple node IDs with descriptive labels). Diagrams SHALL complement text rather than replace it.

#### Scenario: Implicit structure
- **WHEN** a paper implies a method structure or flow without explicit keywords
- **THEN** the system may generate a diagram based on model judgment and label it as inferred

#### Scenario: Mermaid-safe nodes
- **WHEN** the system generates a Mermaid diagram
- **THEN** it uses simple node IDs and places descriptive text in labels to avoid syntax errors

### Requirement: Deep read module A depth
The system SHALL increase Module A output depth by covering user purpose, input coverage (PDF/appendix/code/data), missing artifacts, and a concrete reading plan.

#### Scenario: Richer Module A
- **WHEN** Module A is generated
- **THEN** it includes purpose, input coverage, missing artifacts, and a stepwise reading plan
