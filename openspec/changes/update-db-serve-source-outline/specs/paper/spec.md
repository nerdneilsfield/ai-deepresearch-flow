## ADDED Requirements

### Requirement: Source outline navigation
The system SHALL provide a collapsible outline panel and a back-to-top control in the Source view.

#### Scenario: Source view outline
- **WHEN** the Source view is opened
- **THEN** an outline panel and a back-to-top control are available for navigation

### Requirement: Source image width constraints
The system SHALL constrain Source-rendered images to the content width to prevent overflow.

#### Scenario: Source image sizing
- **WHEN** a Source view contains embedded images
- **THEN** images render within the content width without overflowing the layout
