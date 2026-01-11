## ADDED Requirements

### Requirement: Compact detail header and toolbar
The system SHALL place the paper title in the top header bar and provide a single-row toolbar for view tabs and split controls.

#### Scenario: Detail view uses compact toolbar
- **WHEN** a user opens a paper detail view
- **THEN** the title is displayed in the header and controls appear in one compact row

### Requirement: Fullscreen toggle for detail views
The system SHALL provide a fullscreen toggle for all paper detail views, with a visible control to exit fullscreen.

#### Scenario: Enter and exit fullscreen
- **WHEN** the user toggles fullscreen
- **THEN** the content area expands to the viewport and an exit control remains visible
